"""
Narrative solvability validator for wizardmud  ("narrative CI").

The quest set is a FLAG-DEPENDENCY GRAPH:
  - flags are world state. A flag that is *consumed* by some quest is a
    **resource** (spendable); a flag never consumed is a **fact** (permanent).
  - each quest REQUIRES flags (preconditions), GRANTS flags, and may CONSUME
    some resources.
  - the win condition is a target set of flags (the GOAL).

`validate(world)` answers two questions deterministically:
  1. **Solvable?**  Is there *some* ordering of quests that reaches the goal?
  2. **Soft-lock-free?**  Can the player ever reach a state from which the goal
     is no longer reachable (e.g. by spending a key on an optional sidequest the
     main path needed)?

How: a bounded **state-space search** where a state = the set of completed
quests (facts/resources are derived from it + the start state). This unifies the
three checks we sketched (reachability, self-inflicted soft-lock, sidequest
interference) into one correct analysis, and lets us name the quest that creates
a soft-lock. It is exponential in the worst case, so it is capped at
`node_cap` states; if the cap is hit we say so (no silent truncation).

Pure Python, no Evennia import — run standalone for CI:
    python solvability.py path/to/world.json     # exit 0 = solvable & safe
and import in-game as `world.solvability`.

World JSON shape (what the worldbible generator must emit):
    {
      "start": ["intro_done"],
      "goal":  ["dragon_slain"],
      "quests": [
        {"id": "find_sword", "requires": [], "grants": ["has_sword"],
         "consumes": [], "optional": false, "location": "armory",
         "summary": "..."},
        ...
      ]
    }
"""

from __future__ import annotations

import json
import sys
from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Quest:
    """One node in the dependency graph.

    requires:  flags that must hold to start (preconditions).
    grants:    persistent flags / resources gained on completion.
    consumes:  resources spent on completion (these define what's a "resource").
    optional:  True for sidequests (not required to win).
    """

    id: str
    requires: frozenset[str] = frozenset()
    grants: frozenset[str] = frozenset()
    consumes: frozenset[str] = frozenset()
    optional: bool = False
    location: str = ""
    summary: str = ""

    @staticmethod
    def from_dict(d: dict) -> "Quest":
        return Quest(
            id=d["id"],
            requires=frozenset(d.get("requires", [])),
            grants=frozenset(d.get("grants", [])),
            consumes=frozenset(d.get("consumes", [])),
            optional=bool(d.get("optional", False)),
            location=d.get("location", ""),
            summary=d.get("summary", ""),
        )


@dataclass
class World:
    quests: list[Quest]
    start: frozenset[str] = frozenset()
    goal: frozenset[str] = frozenset()
    # Bonus objectives (alternate endings / optional rewards). NOT required to
    # win, but they make sidequest rewards "meaningful": a grant that feeds an
    # optional_goal is a real payoff, not inert lore.
    optional_goals: frozenset[str] = frozenset()

    @staticmethod
    def from_dict(d: dict) -> "World":
        return World(
            quests=[Quest.from_dict(q) for q in d.get("quests", [])],
            start=frozenset(d.get("start", [])),
            goal=frozenset(d.get("goal", [])),
            optional_goals=frozenset(d.get("optional_goals", [])),
        )

    @staticmethod
    def load(path: str) -> "World":
        with open(path) as fh:
            return World.from_dict(json.load(fh))


@dataclass
class Report:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    states_explored: int = 0

    def __str__(self) -> str:
        lines = [f"solvable & soft-lock-free: {'YES' if self.ok else 'NO'}"]
        for e in self.errors:
            lines.append(f"  ERROR:   {e}")
        for w in self.warnings:
            lines.append(f"  warning: {w}")
        lines.append(f"  ({self.states_explored} states explored)")
        return "\n".join(lines)


def resource_flags(quests) -> frozenset[str]:
    """A flag is a resource iff some quest consumes it; everything else is a fact."""
    out: set[str] = set()
    for q in quests:
        out |= q.consumes
    return frozenset(out)


def reachable_flags(world: World) -> set[str]:
    """Optimistic monotonic closure: which flags are *ever* obtainable, ignoring
    consumption. A sound necessary condition — if a flag isn't here, it's truly
    unobtainable, which gives a clean error before the expensive search.
    """
    reached = set(world.start)
    applied: set[str] = set()
    changed = True
    while changed:
        changed = False
        for q in world.quests:
            if q.id not in applied and q.requires <= reached:
                applied.add(q.id)
                if not q.grants <= reached:
                    reached |= q.grants
                changed = True
    return reached


def _derive_start(world: World, resources: frozenset[str]):
    facts = frozenset(f for f in world.start if f not in resources)
    counts: dict[str, int] = {}
    for f in world.start:
        if f in resources:
            counts[f] = counts.get(f, 0) + 1
    return facts, counts


def _applicable(q: Quest, facts: frozenset[str], counts: dict[str, int],
                resources: frozenset[str]) -> bool:
    for r in q.requires:
        if r in resources:
            if counts.get(r, 0) < 1:
                return False
        elif r not in facts:
            return False
    for c in q.consumes:  # consumes are resources by definition
        if counts.get(c, 0) < 1:
            return False
    return True


def _apply(q: Quest, facts: frozenset[str], counts: dict[str, int],
           resources: frozenset[str]):
    new_facts = set(facts)
    new_counts = dict(counts)
    for g in q.grants:
        if g in resources:
            new_counts[g] = new_counts.get(g, 0) + 1
        else:
            new_facts.add(g)
    for c in q.consumes:
        new_counts[c] = new_counts.get(c, 0) - 1
        if new_counts[c] <= 0:
            del new_counts[c]
    return frozenset(new_facts), new_counts


def _is_goal(world: World, facts: frozenset[str], counts: dict[str, int],
             resources: frozenset[str]) -> bool:
    for g in world.goal:
        if g in resources:
            if counts.get(g, 0) < 1:
                return False
        elif g not in facts:
            return False
    return True


def validate(world: World, node_cap: int = 200_000) -> Report:
    errors: list[str] = []
    warnings: list[str] = []

    # --- cheap pre-filter: is the goal even theoretically obtainable? -------
    optimistic = reachable_flags(world)
    never = world.goal - optimistic
    if never:
        return Report(
            ok=False,
            errors=[
                "goal flag(s) never obtainable: " + ", ".join(sorted(never))
                + " — no quest grants them from the start state"
            ],
        )

    resources = resource_flags(world.quests)
    start_facts, start_counts = _derive_start(world, resources)

    # --- explore the state space (states keyed by completed-quest set) ------
    start_key: frozenset[str] = frozenset()
    facts_of = {start_key: start_facts}
    counts_of = {start_key: start_counts}
    edges: dict[frozenset[str], list[tuple[str, frozenset[str]]]] = {}
    queue = deque([start_key])
    truncated = False

    while queue:
        key = queue.popleft()
        if key in edges:
            continue
        facts, counts = facts_of[key], counts_of[key]
        elist: list[tuple[str, frozenset[str]]] = []
        for quest in world.quests:
            if quest.id in key or not _applicable(quest, facts, counts, resources):
                continue
            nkey = key | {quest.id}
            if nkey not in facts_of:
                if len(facts_of) >= node_cap:
                    truncated = True
                    continue
                nf, nc = _apply(quest, facts, counts, resources)
                facts_of[nkey] = nf
                counts_of[nkey] = nc
                queue.append(nkey)
            elist.append((quest.id, nkey))
        edges[key] = elist

    # --- which states can still reach the goal? (fixpoint) ------------------
    can_win = {
        k: _is_goal(world, facts_of[k], counts_of[k], resources) for k in facts_of
    }
    changed = True
    while changed:
        changed = False
        for key, elist in edges.items():
            if can_win[key]:
                continue
            if any(can_win[nkey] for _qid, nkey in elist):
                can_win[key] = True
                changed = True

    # --- verdicts -----------------------------------------------------------
    if not can_win[start_key]:
        errors.append(
            "goal is unreachable: no valid ordering of quests reaches the win "
            "condition — resource consumption blocks every path (e.g. a required "
            "item is spent before it's needed)"
        )
    else:
        # soft-lock = a reachable state from which the goal can no longer be won.
        # Report the quest that crosses from a winnable state into a trap.
        culprit = None
        for key, elist in edges.items():
            if not can_win[key]:
                continue
            for qid, nkey in elist:
                if not can_win[nkey]:
                    culprit = qid
                    break
            if culprit:
                break
        if culprit:
            q = next(x for x in world.quests if x.id == culprit)
            tag = "optional " if q.optional else ""
            errors.append(
                f"soft-lock: completing {tag}quest '{culprit}' can lead to a state "
                "from which the game is no longer winnable"
            )

    # --- lint: inert rewards + unreachable bonus objectives (warnings) ------
    # A grant is "meaningful" if something downstream uses it: another quest's
    # requires/consumes, the goal, or a bonus objective. Otherwise it's inert.
    used = set(world.goal) | set(world.optional_goals)
    for q in world.quests:
        used |= q.requires | q.consumes
    for q in world.quests:
        dangling = q.grants - used
        if dangling:
            warnings.append(
                f"inert reward: quest '{q.id}' grants {sorted(dangling)} which "
                "nothing requires, consumes, or needs to win/bonus — flavor only"
            )
    for og in sorted(world.optional_goals - optimistic):
        warnings.append(f"bonus objective '{og}' is unreachable — no quest grants it")

    if truncated:
        warnings.append(
            f"state space exceeded the {node_cap:,}-node cap; analysis is PARTIAL "
            "(raise node_cap or simplify the quest graph for a complete check)"
        )

    return Report(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        states_explored=len(facts_of),
    )


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python solvability.py <world.json>", file=sys.stderr)
        return 2
    report = validate(World.load(argv[1]))
    print(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
