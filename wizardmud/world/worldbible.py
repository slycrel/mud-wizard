"""
Offline worldbible generator for wizardmud.

A multi-stage pipeline that authors CANON and proves it before it reaches the game:

  1. prose canon            (Hermes 4 70B)
  2. quest graph            (Hermes)
  3. validate STRUCTURE     (solvability.py — deterministic: solvable? soft-lock-free?)
     -> repair with Hermes if it fails
  4. per-quest puzzles      (Hermes)
  5. critique QUALITY       (an INDEPENDENT critic model — Qwen — judges coherence,
                             meaningfulness, puzzle fairness, variety, pacing)
     -> repair with Hermes; a quality repair that breaks solvability is DISCARDED.

Two models on purpose: Hermes *generates*, Qwen *critiques* — independent eyes
(the adversarial-review principle). Structure validity is non-negotiable; quality
is improved on top of it, never at its expense.

Outputs (under world/generated/):
  - worldbible.md            : prose canon
  - worldbible_quests.json   : validated quest graph (game + validator consume this)
  - worldbible_puzzles.json  : per-quest puzzle specs
  - worldbible_critique.json : the critic's latest scorecard + issues

Run offline (Hermes + the critic model loaded in LM Studio, ideally >=16K context):
    cd wizardmud/world
    ../../.venv/bin/python worldbible.py "a sunken steampunk city" --rating mature
    ../../.venv/bin/python worldbible.py --critique-only   # just re-grade existing content
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from solvability import World, validate  # noqa: E402

LM_STUDIO = os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234/v1")
MODEL = os.environ.get("WORLDBIBLE_MODEL", "nousresearch/hermes-4-70b")     # generator
CRITIC_MODEL = os.environ.get("CRITIC_MODEL", "qwen/qwen3.6-27b")            # independent critic
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated")

# CONTENT_RATING knob (see ARCHITECTURE.md §6/§9): the same value will later be
# injected into the live wizard's system prompt and the image prompts.
RATING_GUIDANCE = {
    "pg13": "Keep content PG-13: adventure, peril, and mild menace; nothing explicit.",
    "mature": ("Mature themes are allowed — violence, intrigue, moral ambiguity, "
               "suggestive elements — as fits dark fantasy fiction. Tasteful, not gratuitous."),
    "explicit": "This is adult fiction; explicit content is permitted where it serves the story.",
}

SCHEMA_SPEC = """Output a SINGLE JSON object with EXACTLY these keys:
{
  "start": ["flag", ...],          // flags true at game start (often [] or one intro flag)
  "goal":  ["flag", ...],          // win condition: flags that MUST be achieved to win
  "optional_goals": ["flag", ...], // BONUS objectives (alternate endings / optional rewards),
                                    //   not required to win but worth striving for
  "quests": [
    {
      "id":       "snake_case_id",   // unique
      "requires": ["flag", ...],     // preconditions that must hold to begin
      "grants":   ["flag", ...],     // flags gained on completion
      "consumes": ["flag", ...],     // resource flags SPENT (subset of requires); [] if none
      "optional": false,             // true = sidequest, not required to win
      "location": "place_id",
      "summary":  "one sentence describing the quest"
    }
  ]
}
RULES:
- The graph MUST be solvable: some ordering of quests reaches EVERY goal flag.
- If a flag is consumed, it must be granted at least as many times on the required
  (non-optional) path so the main path never runs out.
- Include 2-4 OPTIONAL sidequests that must NOT consume any resource the main path
  needs (no soft-locks).
- MEANINGFUL REWARDS: every optional sidequest's grants must MATTER — they must chain
  toward an entry in "optional_goals" (a bonus ending, optional power, or reward).
  Do NOT grant flags that nothing else uses (inert, lore-only rewards are rejected).
  Define 1-3 optional_goals and make the sidequests build to them.
- 8-14 quests total. snake_case for all ids and flags.
- Output ONLY the JSON object — no markdown fences, no commentary."""

PUZZLE_SPEC = """For EACH quest id listed, design the concrete in-world puzzle/interaction the
player performs to complete it. Return a SINGLE JSON object mapping quest_id -> puzzle:
{
  "<quest_id>": {
    "type": "riddle | lever_sequence | item_combine | dialogue | search | ritual | stealth | combat",
    "setup": "what the player encounters, in-world (1-2 sentences)",
    "challenge": "what they must figure out or do",
    "solution": "the canonical solution (GM-facing, used to judge a player's attempt)",
    "hints": ["a gentle nudge", "a stronger hint"],
    "reward_flavor": "the in-world payoff when solved"
  }
}
RULES:
- VARY the puzzle types across quests (do not make them all riddles).
- Solutions must be concrete enough for a game master to judge a player's attempt.
- Keep each field to 1-2 sentences. Output ONLY the JSON object — no fences, no commentary."""

CRITIC_SYSTEM = (
    "You are a sharp, constructive game-design critic reviewing generated content for a "
    "text-adventure MUD. You judge QUALITY: coherence with the canon, whether each quest "
    "and reward is MEANINGFUL (not filler), puzzle interest and fairness, variety, and "
    "pacing. You are specific and fair — you reference ids, praise what works, and flag "
    "what doesn't with a concrete fix."
)

CRITIQUE_SPEC = """Output a SINGLE JSON object:
{
  "scores": {             // 0-10 each
    "coherence": 0,       // fits the canon, no contradictions
    "meaningfulness": 0,  // quests/rewards matter; little or no filler
    "puzzle_quality": 0,  // puzzles are interesting and fair
    "variety": 0,         // varied puzzle types & quest shapes
    "pacing": 0           // good escalation, not repetitive
  },
  "issues": [
    {
      "severity": "blocker | major | minor",
      "target": "<quest_id or puzzle_id or 'overall'>",
      "dimension": "coherence | meaningfulness | fairness | variety | redundancy | pacing",
      "problem": "what's wrong (1 sentence)",
      "suggestion": "a concrete fix (1 sentence)"
    }
  ],
  "summary": "one-paragraph overall verdict"
}
Solvability is verified separately — do NOT comment on winnability. Reserve
'blocker'/'major' for real quality problems (canon contradictions, filler quests,
unfair or trivial puzzles, jarring tone). Output ONLY the JSON object."""

REACTION_SPEC = """For EACH location listed, write a base description plus 2-4 REACTIVE
fragments that the world reveals as the player progresses. Return a SINGLE JSON object:
{
  "<location_id>": {
    "base": "the location's default description (2-3 sentences)",
    "reactions": [
      {"text": "one extra sentence, shown only when its condition holds",
       "requires": ["flag", ...],   // ALL must be set (use flags from the provided list)
       "forbids":  ["flag", ...]}    // NONE may be set ([] if none)
    ]
  }
}
RULES:
- Gate fragments ONLY on flags from the provided list, or they can never fire.
- Each fragment should acknowledge a player DEED — a completed quest's effect now visible
  in the world (a freed ally seen, a sealed door now open, a faction's banner raised).
- One sentence per fragment. Output ONLY the JSON object — no fences, no commentary."""


def chat(messages, max_tokens=2000, temperature=0.8, timeout=1800, model=None):
    body = json.dumps({
        "model": model or MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        LM_STUDIO + "/chat/completions", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"]


def extract_json(text):
    """Pull a JSON object out of the model's reply, tolerating ```json fences."""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        return json.loads(m.group(1))
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j != -1 and j > i:
        return json.loads(text[i:j + 1])
    return json.loads(text)  # let it raise with a clear error


def strip_code_fence(text):
    """Models often wrap markdown output in a ```markdown ... ``` fence; remove it."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n", "", t)
        t = re.sub(r"\n```\s*$", "", t)
    return t.strip()


def generate_prose(theme, system):
    msgs = [
        {"role": "system", "content": system},
        {"role": "user", "content": (
            f'Write the world bible (canon) for a text-adventure MUD from this seed: "{theme}".\n\n'
            "In markdown, include: a short tone/setting overview; 3-5 named locations with a "
            "vivid one-paragraph description each; 2-3 factions and their tensions; the central "
            "conflict and the spine of the main quest arc (how a player ultimately wins); and "
            "3-5 key NPCs with one line of personality each. ~600-900 words. This is canon the "
            "game treats as authoritative."
        )},
    ]
    # Generous budgets: these models "think" (reasoning tokens) before answering, and
    # this is an offline batch pass, so latency doesn't matter — never starve the output.
    return strip_code_fence(chat(msgs, max_tokens=5000, temperature=0.85))


def generate_quests(prose, system):
    msgs = [
        {"role": "system", "content": system},
        {"role": "user", "content": (
            f"Here is the established canon:\n\n{prose}\n\n"
            "Now design the QUEST GRAPH that implements the main arc plus a few sidequests, "
            f"as machine-readable JSON.\n\n{SCHEMA_SPEC}"
        )},
    ]
    return extract_json(chat(msgs, max_tokens=8000, temperature=0.5))


def repair_quests(prev, errors, system):
    msgs = [
        {"role": "system", "content": system},
        {"role": "user", "content": (
            "This quest graph has solvability problems:\n\n- " + "\n- ".join(errors)
            + f"\n\nHere is the JSON to fix:\n{json.dumps(prev, indent=2)}\n\n"
            "Return the CORRECTED JSON — fully solvable and soft-lock-free — keeping the story "
            f"intact. {SCHEMA_SPEC}"
        )},
    ]
    return extract_json(chat(msgs, max_tokens=8000, temperature=0.4))


def generate_puzzles(prose, quests, system):
    """Second pass: a concrete puzzle/interaction spec for every quest."""
    listing = "\n".join(
        f"- {q['id']} ({q.get('location', '?')}, "
        f"{'optional' if q.get('optional') else 'main'}): {q.get('summary', '')}"
        for q in quests.get("quests", [])
    )
    msgs = [
        {"role": "system", "content": system},
        {"role": "user", "content": (
            f"World canon (for tone/consistency):\n\n{prose}\n\n"
            f"Quests needing puzzles:\n{listing}\n\n{PUZZLE_SPEC}"
        )},
    ]
    return extract_json(chat(msgs, max_tokens=9000, temperature=0.7))


def generate_critique(prose, quests, puzzles):
    """Independent QUALITY review by the critic model (not the generator)."""
    payload = {"canon": prose, "quests": quests, "puzzles": puzzles}
    msgs = [
        {"role": "system", "content": CRITIC_SYSTEM},
        {"role": "user", "content": (
            "Review this generated content for QUALITY (solvability is verified elsewhere — "
            "do not comment on winnability). Be specific and reference ids.\n\n"
            f"{json.dumps(payload, indent=2)}\n\n{CRITIQUE_SPEC}"
        )},
    ]
    # The critic reasons heavily before emitting JSON (~4-5k reasoning tokens seen),
    # so give it lots of room or the JSON gets truncated to nothing.
    return extract_json(chat(msgs, max_tokens=12000, temperature=0.4, model=CRITIC_MODEL))


def repair_from_critique(prose, quests, puzzles, issues):
    """Hermes revises quests+puzzles to address blocker/major quality notes."""
    bullets = "\n".join(
        f"- [{i.get('severity')}] {i.get('target')} ({i.get('dimension')}): "
        f"{i.get('problem')} -> {i.get('suggestion')}"
        for i in issues
    )
    msgs = [
        {"role": "system", "content": (
            "You revise your text-adventure content to address a reviewer's quality notes "
            "WITHOUT breaking solvability (every goal flag must stay reachable, no soft-locks, "
            "sidequests still feed optional_goals)."
        )},
        {"role": "user", "content": (
            f"Canon:\n\n{prose}\n\nReviewer's blocking/major notes:\n{bullets}\n\n"
            f"Current quest graph:\n{json.dumps(quests, indent=2)}\n\n"
            f"Current puzzles:\n{json.dumps(puzzles, indent=2)}\n\n"
            'Return a SINGLE JSON object with two keys: "quests" (the FULL corrected quest '
            'graph object with start/goal/optional_goals/quests, same schema) and "puzzles" '
            "(the FULL corrected puzzle map, same schema). Address the notes while keeping the "
            "graph solvable. Output ONLY the JSON object."
        )},
    ]
    return extract_json(chat(msgs, max_tokens=8000, temperature=0.5))


def run_critique_stage(prose, quests, puzzles, rounds):
    """Critique -> (repair -> re-validate) loop. Returns possibly-revised (quests, puzzles)."""
    for cround in range(rounds + 1):
        print(f"\n[5/5] Content critique (round {cround}) via {CRITIC_MODEL} …", flush=True)
        try:
            crit = generate_critique(prose, quests, puzzles)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"      critic returned invalid JSON ({e}); stopping critique", flush=True)
            break
        scores, issues = crit.get("scores", {}), crit.get("issues", [])
        if scores:
            print("      scores: " + ", ".join(f"{k} {v}" for k, v in scores.items()), flush=True)
        if crit.get("summary"):
            print(f"      verdict: {crit['summary']}", flush=True)
        for i in issues:
            print(f"      [{i.get('severity','?')}] {i.get('target','')} "
                  f"({i.get('dimension','')}): {i.get('problem','')}", flush=True)
        with open(os.path.join(OUTDIR, "worldbible_critique.json"), "w") as f:
            json.dump(crit, f, indent=2)

        blockers = [i for i in issues if i.get("severity") in ("blocker", "major")]
        if not blockers or cround == rounds:
            if blockers:
                print(f"      {len(blockers)} blocker/major issue(s) remain after {rounds} round(s)",
                      flush=True)
            break

        print(f"      -> revising {len(blockers)} issue(s) via {MODEL} …", flush=True)
        try:
            revised = repair_from_critique(prose, quests, puzzles, blockers)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"      critique-repair was invalid JSON ({e}); keeping previous", flush=True)
            break
        if not (isinstance(revised, dict) and isinstance(revised.get("quests"), dict)):
            print("      critique-repair missing 'quests'; keeping previous", flush=True)
            break

        # SAFETY: a quality repair that breaks solvability is rejected outright.
        rep = validate(World.from_dict(revised["quests"]))
        if not rep.ok:
            print("      revision broke solvability — DISCARDED, keeping prior version:", flush=True)
            for e in rep.errors:
                print(f"        would-be ERROR: {e}", flush=True)
            break
        quests = revised["quests"]
        if isinstance(revised.get("puzzles"), dict):
            puzzles = revised["puzzles"]
        with open(os.path.join(OUTDIR, "worldbible_quests.json"), "w") as f:
            json.dump(quests, f, indent=2)
        with open(os.path.join(OUTDIR, "worldbible_puzzles.json"), "w") as f:
            json.dump(puzzles, f, indent=2)
        print("      revision applied (re-validated solvable & soft-lock-free)", flush=True)
    return quests, puzzles


def generate_reactions(canon, quests):
    """Per-location base text + flag-gated reactive fragments (the world reacts to deeds)."""
    locs = sorted({q.get("location", "") for q in quests.get("quests", []) if q.get("location")})
    flags = sorted(
        set(quests.get("goal", [])) | set(quests.get("optional_goals", []))
        | {g for q in quests.get("quests", []) for g in q.get("grants", [])}
    )
    listing = ("Locations to write:\n" + "\n".join(f"- {l}" for l in locs)
               + "\n\nFlags you may gate fragments on (use ONLY these):\n" + ", ".join(flags))
    msgs = [
        {"role": "system", "content": (
            "You are the worldbuilder for a text-adventure MUD. You write evocative location "
            "descriptions and reactive fragments that make the world acknowledge the player's deeds."
        )},
        {"role": "user", "content": f"World canon:\n\n{canon}\n\n{listing}\n\n{REACTION_SPEC}"},
    ]
    return extract_json(chat(msgs, max_tokens=9000, temperature=0.7))


def run_layout_stage(gen_dir=OUTDIR, force=False):
    """Emit the *walkable scene manifest* (world/layout.py) so the generated world can be
    dived into in-game — the `worldinit` builder command materializes it into rooms, exits,
    and NPC placements. Deterministic (no LLM): derives a connected hub-and-spoke map from
    the validated worldbible, with the Wizard at the start. Preserves an existing authored
    layout (hand-tuned geography/NPCs) unless force=True. This is what makes a freshly
    generated world playable instead of dropping the player into empty Limbo."""
    import layout as layout_mod
    from worldbible_loader import Worldbible
    print(f"\n[7/8] Scene manifest (walkable layout) …", flush=True)
    path = os.path.join(gen_dir, layout_mod.LAYOUT_FILE)
    if os.path.exists(path) and not force:
        try:
            lay = layout_mod.Layout.from_dict(json.load(open(path)))
            print(f"      kept authored {layout_mod.LAYOUT_FILE} "
                  f"(start={lay.start}, {len(lay.rooms)} rooms, {len(lay.exits)} exits)", flush=True)
            return
        except Exception as e:
            print(f"      existing layout unreadable ({e}); regenerating", flush=True)
    wb = Worldbible.load(gen_dir)
    lay = layout_mod.derive_layout(wb)
    layout_mod.write_layout(lay, gen_dir)
    print(f"      derived -> generated/{layout_mod.LAYOUT_FILE} "
          f"(start={lay.start}, {len(lay.rooms)} rooms, {len(lay.exits)} exits, "
          f"{len(lay.npcs)} npc) — edit for canon-accurate geography/NPC placement", flush=True)


def run_reactions_stage(canon, quests):
    """Generate, lint (fragments must gate on real flags), and save reactive world text."""
    print(f"\n[6/8] Reactive world text via {MODEL} …", flush=True)
    try:
        reactions = generate_reactions(canon, quests)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"      reactions returned invalid JSON ({e}); skipped", flush=True)
        return
    if not isinstance(reactions, dict) or not reactions:
        print("      no reactions produced", flush=True)
        return
    valid = set(quests.get("goal", [])) | set(quests.get("optional_goals", []))
    for q in quests.get("quests", []):
        valid |= set(q.get("grants", []))
    referenced, nfrag = set(), 0
    for entry in reactions.values():
        for r in entry.get("reactions", []):
            nfrag += 1
            referenced |= set(r.get("requires", [])) | set(r.get("forbids", []))
    with open(os.path.join(OUTDIR, "worldbible_reactions.json"), "w") as f:
        json.dump(reactions, f, indent=2)
    print(f"      {len(reactions)} locations, {nfrag} fragments -> generated/worldbible_reactions.json",
          flush=True)
    unknown = referenced - valid
    if unknown:
        print(f"      lint: fragments gate on unknown flags (will never fire): {sorted(unknown)}",
              flush=True)


OBJECTS_SPEC = (
    "Return ONLY JSON of this shape:\n"
    '{\n'
    '  "objects": [\n'
    '    {"key": "iron chest", "location": "<a location id>", "kind": "container", '
    '"desc": "...", "locked": true, "is_open": false, "key_item": "rune-etched key", '
    '"contains": [ {"key":"storm-cloak","kind":"wearable","desc":"..."} ]},\n'
    '    {"key": "rune-etched key", "location": "<a location id>", "kind": "item", "desc": "..."}\n'
    '  ],\n'
    '  "object_goals": { "<quest id>": {"all": [ {"obj":"iron chest","is":"open"}, '
    '{"obj":"storm-cloak","is":"held"} ]} }\n'
    "}\n"
    "kind is one of: item (takeable), wearable (takeable+wearable), container (holds things, "
    "openable), fixture (immovable feature like a door/lever). 'is' is one of: open, closed, "
    "locked, unlocked, held, worn, in_room. RULES: (1) place objects only in the given location "
    "ids; (2) for EACH non-optional quest, give an object_goal its location's objects can satisfy, "
    "coherent with that quest's puzzle solution; (3) make the path reachable — anything required "
    "'held'/'worn' must be a takeable/wearable object, and any locked container required 'open' "
    "must have a key_item that is itself a takeable object placed within reach; (4) keep everything "
    "consistent with the canon."
)


def generate_objects(canon, quests, puzzles):
    """Co-design tangible objects per location AND an object_goal per main quest, grounding the
    puzzles in concrete, manipulable things. Returns {'objects': [...], 'object_goals': {...}}."""
    locs = sorted({q.get("location") for q in quests.get("quests", []) if q.get("location")})
    qlines = []
    for q in quests.get("quests", []):
        pz = puzzles.get(q["id"], {})
        opt = " (optional)" if q.get("optional") else ""
        qlines.append(f"- {q['id']} @ {q.get('location')}{opt}: {q.get('summary','')}\n"
                      f"    puzzle: {pz.get('setup','')} {pz.get('challenge','')}\n"
                      f"    intended solution: {pz.get('solution','')}")
    msgs = [
        {"role": "system", "content": (
            "You are the worldbuilder for a dark-fantasy text MUD. You design tangible, "
            "manipulable OBJECTS that populate locations and GROUND each quest's puzzle in "
            "concrete things the player can open, take, and wear — and you specify, per quest, "
            "the object state(s) that mean it is solved."
        )},
        {"role": "user", "content": (
            f"World canon:\n{canon[:1500]}\n\n"
            f"Location ids: {', '.join(locs)}\n\n"
            f"Quests and their puzzles:\n" + "\n".join(qlines) + "\n\n" + OBJECTS_SPEC
        )},
    ]
    return extract_json(chat(msgs, max_tokens=9000, temperature=0.7))


def apply_objects_result(result, gen_dir=None):
    """Write generated objects into the layout manifest and merge object_goals into the puzzles
    file; return (n_objects, n_goals, lint_warnings). Pure file IO (no LLM) so it unit-tests."""
    import layout as layout_mod
    import objgoal
    gen_dir = gen_dir or OUTDIR
    objects = result.get("objects") or []
    goals = result.get("object_goals") or {}

    lpath = os.path.join(gen_dir, layout_mod.LAYOUT_FILE)
    if os.path.exists(lpath):
        lay = layout_mod.Layout.from_dict(json.load(open(lpath)))
    else:
        lay = layout_mod.load_or_derive(Worldbible.load(gen_dir), gen_dir)
    lay.objects = objects
    layout_mod.write_layout(lay, gen_dir)

    ppath = os.path.join(gen_dir, "worldbible_puzzles.json")
    puzzles = json.load(open(ppath)) if os.path.exists(ppath) else {}
    for qid, goal in goals.items():
        if qid in puzzles:
            puzzles[qid]["object_goal"] = goal
    with open(ppath, "w") as f:
        json.dump(puzzles, f, indent=2)

    return len(objects), len(goals), objgoal.lint(goals, objects)


def run_objects_stage(canon, quests, puzzles):
    """Generate object-aware puzzles: tangible objects + per-quest object_goals (the hybrid)."""
    print(f"\n[8/8] Object-aware puzzles via {MODEL} …", flush=True)
    try:
        result = generate_objects(canon, quests, puzzles)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"      objects returned invalid JSON ({e}); skipped (objects stay sandbox)", flush=True)
        return
    if not isinstance(result, dict) or not result.get("objects"):
        print("      no objects produced", flush=True)
        return
    n_obj, n_goals, warns = apply_objects_result(result, OUTDIR)
    print(f"      {n_obj} objects -> generated/worldbible_layout.json; "
          f"{n_goals} object_goals merged into worldbible_puzzles.json", flush=True)
    for w in warns:
        print(f"      lint: {w}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Generate, validate, and critique a world bible.")
    ap.add_argument("theme", nargs="?",
                    default="a crumbling wizard's keep on a storm-wracked coast")
    ap.add_argument("--rating", choices=list(RATING_GUIDANCE), default="mature")
    ap.add_argument("--max-repairs", type=int, default=3)
    ap.add_argument("--puzzles", action=argparse.BooleanOptionalAction, default=True,
                    help="generate per-quest puzzle specs")
    ap.add_argument("--critique", action=argparse.BooleanOptionalAction, default=True,
                    help="run the independent quality critique (+ repair) pass")
    ap.add_argument("--critique-rounds", type=int, default=1,
                    help="max critique->repair rounds")
    ap.add_argument("--critique-only", action="store_true",
                    help="skip generation; critique the existing generated/ files")
    ap.add_argument("--reactions", action=argparse.BooleanOptionalAction, default=True,
                    help="generate reactive per-location world text (flag-gated)")
    ap.add_argument("--reactions-only", action="store_true",
                    help="skip generation; produce reactions for existing generated/ files")
    ap.add_argument("--layout", action=argparse.BooleanOptionalAction, default=True,
                    help="emit the walkable scene manifest (rooms/exits/NPC placement)")
    ap.add_argument("--layout-only", action="store_true",
                    help="skip generation; (re)emit the layout for existing generated/ files")
    ap.add_argument("--objects", action=argparse.BooleanOptionalAction, default=True,
                    help="co-design tangible objects + per-quest object_goals (object-aware puzzles)")
    ap.add_argument("--objects-only", action="store_true",
                    help="skip generation; (re)author objects/object_goals for existing files")
    args = ap.parse_args()
    os.makedirs(OUTDIR, exist_ok=True)

    # --- critique-only: grade existing content without regenerating ----------
    if args.critique_only:
        prose = open(os.path.join(OUTDIR, "worldbible.md")).read()
        quests = json.load(open(os.path.join(OUTDIR, "worldbible_quests.json")))
        ppath = os.path.join(OUTDIR, "worldbible_puzzles.json")
        puzzles = json.load(open(ppath)) if os.path.exists(ppath) else {}
        print(f"Critiquing existing content in {OUTDIR} …", flush=True)
        run_critique_stage(prose, quests, puzzles, args.critique_rounds)
        return 0

    if args.reactions_only:
        prose = open(os.path.join(OUTDIR, "worldbible.md")).read()
        quests = json.load(open(os.path.join(OUTDIR, "worldbible_quests.json")))
        print(f"Generating reactive world text for existing content in {OUTDIR} …", flush=True)
        run_reactions_stage(prose, quests)
        return 0

    if args.layout_only:
        print(f"Emitting scene manifest for existing content in {OUTDIR} …", flush=True)
        run_layout_stage(force=True)
        return 0

    if args.objects_only:
        prose = open(os.path.join(OUTDIR, "worldbible.md")).read()
        quests = json.load(open(os.path.join(OUTDIR, "worldbible_quests.json")))
        ppath = os.path.join(OUTDIR, "worldbible_puzzles.json")
        puzzles = json.load(open(ppath)) if os.path.exists(ppath) else {}
        print(f"Authoring object-aware puzzles for existing content in {OUTDIR} …", flush=True)
        run_objects_stage(prose, quests, puzzles)
        return 0

    system = (
        "You are a master worldbuilder and game designer for a text-adventure MUD. "
        "You write vivid, coherent canon and precise, machine-readable quest structures. "
        + RATING_GUIDANCE[args.rating]
    )
    puzzles = {}

    print(f"[1/5] Prose canon via {MODEL} (seed: {args.theme!r}, rating: {args.rating}) …", flush=True)
    prose = generate_prose(args.theme, system)
    with open(os.path.join(OUTDIR, "worldbible.md"), "w") as f:
        f.write(f"# World Bible\n\n*seed: {args.theme} · rating: {args.rating}*\n\n{prose}\n")
    print(f"      {len(prose)} chars -> generated/worldbible.md", flush=True)

    print("[2/5] Quest graph …", flush=True)
    try:
        quests = generate_quests(prose, system)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"      first pass wasn't valid JSON ({e}); asking for a clean re-emit…", flush=True)
        quests = repair_quests({}, [f"previous output was not valid JSON: {e}"], system)

    print("[3/5] Validate -> repair loop …", flush=True)
    report = validate(World.from_dict(quests))
    for attempt in range(args.max_repairs + 1):
        n = len(quests.get("quests", []))
        print(f"  attempt {attempt}: {'OK' if report.ok else 'FAIL'} "
              f"({n} quests, {report.states_explored} states)", flush=True)
        for e in report.errors:
            print(f"      ERROR: {e}", flush=True)
        for w in report.warnings:
            print(f"      warn:  {w}", flush=True)
        if report.ok or attempt == args.max_repairs:
            break
        print("  -> repairing with validator feedback…", flush=True)
        try:
            quests = repair_quests(quests, report.errors, system)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"      repair returned invalid JSON ({e}); keeping previous", flush=True)
        report = validate(World.from_dict(quests))

    with open(os.path.join(OUTDIR, "worldbible_quests.json"), "w") as f:
        json.dump(quests, f, indent=2)
    final = validate(World.from_dict(quests))
    print("\nSaved quest graph -> generated/worldbible_quests.json")
    for w in final.warnings:
        print(f"  lint: {w}", flush=True)
    print(f"FINAL: solvable & soft-lock-free: {'YES' if final.ok else 'NO — not guaranteed winnable'}")

    if args.puzzles:
        print("\n[4/5] Per-quest puzzles …", flush=True)
        try:
            puzzles = generate_puzzles(prose, quests, system)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"      puzzle generation returned invalid JSON ({e}); skipped", flush=True)
            puzzles = {}
        if isinstance(puzzles, dict) and puzzles:
            with open(os.path.join(OUTDIR, "worldbible_puzzles.json"), "w") as f:
                json.dump(puzzles, f, indent=2)
            qids = {q["id"] for q in quests.get("quests", [])}
            types = sorted({p.get("type", "?") for p in puzzles.values() if isinstance(p, dict)})
            missing = qids - set(puzzles)
            print(f"      {len(puzzles)} puzzles -> generated/worldbible_puzzles.json", flush=True)
            print(f"      puzzle types: {', '.join(types)}", flush=True)
            if missing:
                print(f"      WARN: no puzzle for {sorted(missing)}", flush=True)

    if args.critique:
        quests, puzzles = run_critique_stage(prose, quests, puzzles, args.critique_rounds)

    if args.reactions:
        run_reactions_stage(prose, quests)

    if args.layout:
        run_layout_stage()

    if args.objects:
        run_objects_stage(prose, quests, puzzles)

    return 0 if final.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
