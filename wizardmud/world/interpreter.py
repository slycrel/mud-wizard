"""
Natural-language intent interpreter — the LLM as a *parser over authoritative state*.

This is what makes the game feel like a game instead of a form you fill in: the player
types a plain in-world action ("search the collapsed library for a hidden way") and this
maps it to ONE game action against the *currently possible* objectives, then judges it.
The player never names a quest id or fills a slot — the engine interprets them.

The golden rule is intact, just with the LLM doing one more job. It only *proposes*:
which objective an action pursues (or none) and whether it succeeds. The command layer
applies the result through the validated quest graph (worldbible_loader), so a success
can only ever grant that objective's already-proven flags — interpretation can be
flavorful but can never corrupt state or make the game unwinnable.

Self-contained HTTP (reuses puzzle_judge's client) so runtime code doesn't depend on the
offline generator. Call via `deferToThread` so the blocking request never stalls the MUD.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from puzzle_judge import _chat, _extract_json, RATING_GUIDANCE, WIZARD_MODEL  # noqa: E402

_KINDS = {"move", "action", "talk", "look", "other"}
_VERDICTS = {"success", "partial", "fail"}


def _objective_block(quests_with_puzzles) -> str:
    """Render the objectives available at the player's spot, with secret solutions the
    judge may use but must never reveal."""
    if not quests_with_puzzles:
        return "OBJECTIVES HERE: none active at this spot right now.\n"
    out = ["OBJECTIVES THE PLAYER COULD PURSUE HERE "
           "(secret — for your judgment only, never reveal ids or solutions):"]
    for q, pz in quests_with_puzzles:
        out.append(
            f"  - id={q.id}\n"
            f"      it is: {q.summary}\n"
            f"      the challenge: {pz.get('setup', '')} {pz.get('challenge', '')}\n"
            f"      a solution: {pz.get('solution', '(use judgment)')}"
        )
    return "\n".join(out) + "\n"


def build_messages(text, *, location, location_desc, exits, quests_with_puzzles,
                   canon="", rating="mature"):
    system = (
        "You are the game master AND the parser of a dark-fantasy text MUD. You translate the "
        "player's free-form input into exactly ONE game action. The game's code is the only "
        "source of truth: never invent places, exits, objects, items, NPCs, or mechanics beyond "
        "what is given to you, and never reveal secret solutions, objective ids, or game "
        "mechanics in your narration. " + RATING_GUIDANCE.get(rating, RATING_GUIDANCE["mature"])
    )
    user = (
        (f"WORLD TONE:\n{canon[:700]}\n\n" if canon else "")
        + f"WHERE THE PLAYER STANDS: {location}\n{location_desc}\n\n"
        + f"EXITS FROM HERE: {', '.join(exits) if exits else '(none)'}\n\n"
        + _objective_block(quests_with_puzzles)
        + f'\nTHE PLAYER TYPED:\n  "{text}"\n\n'
        + "Decide the single best interpretation. Reply with ONLY this JSON:\n"
        '{"kind": "move | action | talk | look | other", '
        '"direction": "<one of the exits, exactly> or null", '
        '"quest_id": "<one objective id> or null", '
        '"verdict": "success | partial | fail | null", '
        '"narration": "2-4 sentences, second person, in the world\'s voice, what happens"}\n\n'
        "Rules:\n"
        "- move: they want to travel a direction/place matching an exit -> set direction to that exit.\n"
        "- look: they just want to take in their surroundings.\n"
        "- talk: they address or question someone here (e.g. the Wizard), OR ask what to do, "
        "where to go, or for help -> narration is the Wizard's brief in-character guidance, "
        "pointing toward a deed they might attempt here or a direction they might travel — never "
        "a list of commands or a menu.\n"
        "- action: they attempt to DO something. Pick the one objective their action pursues "
        "(quest_id), or null if it fits none here. Judge HYBRID: 'success' if it matches a "
        "solution OR is a genuinely clever, plausible in-world alternative that achieves it; "
        "'partial' if on the right track but incomplete; 'fail' if wrong, nonsensical, or idle. "
        "If quest_id is null, set verdict null and narrate the attempt having no real effect.\n"
        "- narration: vivid but tight; NEVER reveal solutions/ids/flags/mechanics; use no pipe "
        "(|) characters."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def interpret(text, *, location, location_desc, exits, quests_with_puzzles,
              canon="", rating="mature", model=WIZARD_MODEL) -> dict:
    """Map free-form input to one normalized action. Degrades to a safe 'other' with gentle
    flavor if the model misbehaves — never raises into the game loop."""
    try:
        raw = _chat(build_messages(
            text, location=location, location_desc=location_desc, exits=exits,
            quests_with_puzzles=quests_with_puzzles, canon=canon, rating=rating),
            max_tokens=800, temperature=0.5, model=model)
        v = _extract_json(raw)
        kind = str(v.get("kind", "other")).lower().strip()
        if kind not in _KINDS:
            kind = "other"
        verdict = str(v.get("verdict", "")).lower().strip()
        verdict = verdict if verdict in _VERDICTS else None
        direction = v.get("direction") or None
        quest_id = v.get("quest_id") or None
        if isinstance(quest_id, str) and quest_id.lower() in ("null", "none", ""):
            quest_id = None
        return {
            "kind": kind,
            "direction": direction if isinstance(direction, str) else None,
            "quest_id": quest_id if isinstance(quest_id, str) else None,
            "verdict": verdict,
            "narration": (v.get("narration") or "").strip()
            or "Nothing here answers to that.",
        }
    except Exception as e:
        return {"kind": "other", "direction": None, "quest_id": None, "verdict": None,
                "narration": "The moment slips past, hazy and unformed. Try putting it another way.",
                "_error": f"{type(e).__name__}: {e}"}
