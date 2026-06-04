"""
Hybrid puzzle judging for the live wizard (EVA-Qwen).

The Wizard IS the game master, so the Wizard judges the player's attempt — accepting
the canonical solution OR a genuinely clever in-world alternative, giving partial
credit on near-misses, and rejecting nonsense. It returns a structured verdict plus
in-character narration.

State stays authoritative elsewhere (worldbible_loader): this module only decides
success/partial/fail + narrates. The command layer applies the quest's validated
flags on success — so judging can be flavorful without ever corrupting game state.

Self-contained (own HTTP call) — runtime game code shouldn't depend on the offline
generator. In-game, call `judge_attempt(...)` via `twisted.internet.threads.deferToThread`
so the blocking request never freezes the MUD's reactor.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

LM_STUDIO = os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234/v1")
WIZARD_MODEL = os.environ.get("WIZARD_MODEL", "eva-qwen2.5-32b-v0.2-mlx")

RATING_GUIDANCE = {
    "pg13": "Keep it PG-13.",
    "mature": "Mature themes (violence, intrigue, moral ambiguity) are fine; tasteful, not gratuitous.",
    "explicit": "Adult content is permitted where it serves the story.",
}


def _chat(messages, max_tokens=1500, temperature=0.3, timeout=300, model=WIZARD_MODEL):
    body = json.dumps({
        "model": model, "messages": messages,
        "max_tokens": max_tokens, "temperature": temperature, "stream": False,
    }).encode()
    req = urllib.request.Request(
        LM_STUDIO + "/chat/completions", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)["choices"][0]["message"]["content"]


def _extract_json(text):
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        return json.loads(m.group(1))
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j != -1 and j > i:
        return json.loads(text[i:j + 1])
    return json.loads(text)


def build_messages(puzzle: dict, attempt: str, canon: str = "", rating: str = "mature"):
    system = (
        "You are the Wizard, game master of a dark-fantasy text MUD. You judge a player's "
        "attempt to overcome a challenge — fairly, like a good tabletop GM, rewarding clever "
        "thinking but not nonsense. " + RATING_GUIDANCE.get(rating, RATING_GUIDANCE["mature"])
    )
    user = (
        (f"World canon (tone/consistency):\n{canon[:800]}\n\n" if canon else "")
        + "The challenge before the player:\n"
        + f"  Setup: {puzzle.get('setup', '')}\n"
        + f"  Challenge: {puzzle.get('challenge', '')}\n"
        + f"  Canonical solution (SECRET — for your judgment only, never reveal): "
        + f"{puzzle.get('solution', '')}\n\n"
        + f'The player attempts:\n  "{attempt}"\n\n'
        + "Judge HYBRID: 'success' if the attempt matches the canonical solution OR is a "
        "genuinely clever, plausible alternative that achieves the goal in-world. 'partial' if "
        "they're on the right track but incomplete. 'fail' if it's wrong, nonsensical, or "
        "doesn't really engage. Reply with ONLY this JSON:\n"
        '{"verdict": "success | partial | fail", '
        '"narration": "2-3 sentences, in character as the Wizard, describing what happens '
        '(never reveal the canonical solution, even on failure)", '
        '"reason": "one short GM rationale"}'
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def judge_attempt(puzzle: dict, attempt: str, canon: str = "", rating: str = "mature",
                  model: str = WIZARD_MODEL) -> dict:
    """Return {'verdict': success|partial|fail, 'narration': str, 'reason': str}.
    Falls back to a safe 'fail' verdict if the model misbehaves (never raises into the game)."""
    try:
        raw = _chat(build_messages(puzzle, attempt, canon, rating), model=model)
        v = _extract_json(raw)
        verdict = str(v.get("verdict", "fail")).lower().strip()
        if verdict not in ("success", "partial", "fail"):
            verdict = "fail"
        return {
            "verdict": verdict,
            "narration": v.get("narration", "").strip()
            or "The Wizard considers your attempt in silence.",
            "reason": v.get("reason", "").strip(),
        }
    except Exception as e:  # network, JSON, anything — degrade gracefully
        return {
            "verdict": "fail",
            "narration": "The Wizard frowns, distracted, and the moment slips away. Try again.",
            "reason": f"judge error: {type(e).__name__}: {e}",
        }


def narrate_alternative(puzzle: dict, canon: str = "", rating: str = "mature",
                        model: str = WIZARD_MODEL) -> str:
    """The Wizard narrates a guaranteed alternative route past a puzzle the player is
    stuck on. The flag OUTCOME is applied by the caller (the quest's validated grants),
    so this is pure narration — it cannot break solvability. Returns prose (not JSON)."""
    msgs = [
        {"role": "system", "content": (
            "You are the Wizard, a merciful but mysterious game master in a dark-fantasy MUD. "
            + RATING_GUIDANCE.get(rating, RATING_GUIDANCE["mature"])
        )},
        {"role": "user", "content": (
            (f"World canon:\n{canon[:600]}\n\n" if canon else "")
            + "The player is stuck on this challenge:\n"
            + f"  Setup: {puzzle.get('setup', '')}\n"
            + f"  Challenge: {puzzle.get('challenge', '')}\n"
            + f"  What they ultimately gain: {puzzle.get('reward_flavor', '')}\n\n"
            "Narrate, in 2-3 in-character sentences, an ALTERNATIVE way they get past it — an "
            "ally's intervention, a lucky discovery, a different route — so the story moves on. "
            "Do not reveal the original intended solution. Output ONLY the narration prose."
        )},
    ]
    try:
        return _chat(msgs, max_tokens=600, temperature=0.7).strip()
    except Exception:
        return puzzle.get("reward_flavor") or "You find another way through, and the path opens."
