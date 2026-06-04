"""
Runtime loader + progression state machine for the generated worldbible.

Loads canon + quest graph + puzzles (from world/generated/) and tracks a single
player's progress as a flag set. The SAME `solvability.World` powers offline
validation and live play, so what the validator proved winnable is exactly what the
game runs.

Design rule (ARCHITECTURE §9): the LLM narrates; **state lives here, authoritatively.**
A quest's effects (its `grants`/`consumes`) are fixed and already validated, so any
path to completing it — solving the puzzle, a clever alternative, or the stuck-player
*bypass* — applies the same flags and therefore cannot break solvability.

Pure Python (no Evennia import) so it unit-tests standalone; the command layer wraps
it onto a character. v1 uses set semantics for flags (a resource is present or not),
a deliberate simplification of the validator's count semantics — fine because the
generated graphs lean on facts, not multi-count resources.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from solvability import World  # noqa: E402
from reactive import compose as compose_reactions, load_reactions  # noqa: E402

GEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated")

# After this many failed attempts AND all hints shown, the wizard may offer a
# guaranteed alternative so the player is never permanently stuck.
FAIL_THRESHOLD = 2


@dataclass
class Progress:
    """One player's state. Serializable to character.db (sets <-> lists)."""
    flags: set = field(default_factory=set)        # currently-true flags
    done: set = field(default_factory=set)         # completed quest ids
    attempts: dict = field(default_factory=dict)   # quest id -> failed attempts
    hints_used: dict = field(default_factory=dict)  # quest id -> hints revealed

    def to_dict(self):
        return {
            "flags": sorted(self.flags),
            "done": sorted(self.done),
            "attempts": self.attempts,
            "hints_used": self.hints_used,
        }

    @staticmethod
    def from_dict(d):
        d = d or {}
        return Progress(
            flags=set(d.get("flags", [])),
            done=set(d.get("done", [])),
            attempts=dict(d.get("attempts", {})),
            hints_used=dict(d.get("hints_used", {})),
        )


class Worldbible:
    """Static generated content + progression queries over a Progress."""

    def __init__(self, world: World, puzzles: dict, canon: str = "", reactions: dict = None):
        self.world = world
        self.puzzles = puzzles or {}
        self.canon = canon
        self.reactions = reactions or {}  # {location: {"base": str, "reactions": [Reaction]}}
        self._by_id = {q.id: q for q in world.quests}

    @classmethod
    def load(cls, gen_dir: str = GEN_DIR) -> "Worldbible":
        world = World.load(os.path.join(gen_dir, "worldbible_quests.json"))
        ppath = os.path.join(gen_dir, "worldbible_puzzles.json")
        puzzles = {}
        if os.path.exists(ppath):
            with open(ppath) as fh:
                puzzles = json.load(fh)
        cpath = os.path.join(gen_dir, "worldbible.md")
        canon = ""
        if os.path.exists(cpath):
            with open(cpath) as fh:
                canon = fh.read()
        rpath = os.path.join(gen_dir, "worldbible_reactions.json")
        reactions = load_reactions(rpath) if os.path.exists(rpath) else {}
        return cls(world, puzzles, canon, reactions)

    def location_text(self, location: str, p: "Progress") -> str:
        """Reactive description of a location, composed from the player's current flags."""
        entry = self.reactions.get(location)
        if not entry:
            return ""
        return compose_reactions(entry.get("base", ""), entry.get("reactions", []), p.flags)

    # --- lookups --------------------------------------------------------
    def quest(self, qid):
        return self._by_id.get(qid)

    def puzzle(self, qid):
        return self.puzzles.get(qid, {})

    # --- progression queries -------------------------------------------
    def available(self, p: Progress):
        """Quests the player can start now: not done, preconditions met."""
        return [q for q in self.world.quests
                if q.id not in p.done and q.requires <= p.flags]

    def completed(self, p: Progress):
        return [q for q in self.world.quests if q.id in p.done]

    def is_won(self, p: Progress) -> bool:
        return self.world.goal <= p.flags

    def bonus_achieved(self, p: Progress):
        return sorted(self.world.optional_goals & p.flags)

    # --- mutations (the only ways flags change) ------------------------
    def complete(self, p: Progress, qid: str, *, via: str = "solved") -> bool:
        """Apply a quest's validated effects. `via` is just narration provenance
        ('solved' | 'alternative' | 'bypass'); the flag outcome is identical, so
        none of these can break solvability."""
        q = self._by_id.get(qid)
        if not q or qid in p.done or not (q.requires <= p.flags):
            return False  # unknown, already done, or preconditions not met (locked)
        p.flags |= set(q.grants)
        p.flags -= set(q.consumes)
        p.done.add(qid)
        return True

    def record_fail(self, p: Progress, qid: str):
        p.attempts[qid] = p.attempts.get(qid, 0) + 1

    def next_hint(self, p: Progress, qid: str):
        """Return (hint_text | None, hints_remaining:int). Advances the counter."""
        hints = self.puzzle(qid).get("hints", []) or []
        used = p.hints_used.get(qid, 0)
        if used >= len(hints):
            return None, 0
        p.hints_used[qid] = used + 1
        return hints[used], len(hints) - (used + 1)

    def bypass_available(self, p: Progress, qid: str) -> bool:
        """The stuck-player escape hatch: all hints shown AND enough failed attempts.
        Guarantees the player can always progress (the alternative)."""
        hints = self.puzzle(qid).get("hints", []) or []
        hints_done = p.hints_used.get(qid, 0) >= len(hints)
        return hints_done and p.attempts.get(qid, 0) >= FAIL_THRESHOLD
