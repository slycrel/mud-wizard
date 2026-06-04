"""
Reactive content resolver — the world acknowledges what the player has done.

Quality-based-narrative (Fallen London) for wizardmud: a location/object has a base
description plus **flag-gated fragments**. At display time we compose the base with
every fragment whose condition matches the player's current flags, so the world reflects
their progress ("the rune-stones you deciphered now glimmer beneath the water").

Deterministic + pure (no Evennia, no LLM) so it unit-tests standalone and runs instantly
at look-time. Fragments are authored offline by the worldbible generator (Hermes) and
gated on the SAME flags the quest graph uses, so reactions fire as quests complete.

Reactions JSON shape (world/generated/worldbible_reactions.json):
    {
      "whispering_marsh": {
        "base": "Black water stretches between skeletal trees.",
        "reactions": [
          {"text": "Rune-stones glimmer beneath the surface, legible to you now.",
           "requires": ["marsh_secrets_learned"], "forbids": []}
        ]
      }
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Reaction:
    text: str
    requires: frozenset[str] = frozenset()  # ALL must be present in the player's flags
    forbids: frozenset[str] = frozenset()   # NONE may be present

    def fires(self, flags: set[str]) -> bool:
        return self.requires <= flags and not (self.forbids & flags)

    @staticmethod
    def from_dict(d: dict) -> "Reaction":
        return Reaction(
            text=d.get("text", ""),
            requires=frozenset(d.get("requires", [])),
            forbids=frozenset(d.get("forbids", [])),
        )


def compose(base: str, reactions, flags) -> str:
    """Base description + every fragment whose condition matches `flags`."""
    flags = set(flags)
    parts = [base.strip()] if base else []
    for r in reactions:
        if r.fires(flags):
            parts.append(r.text.strip())
    return " ".join(p for p in parts if p).strip()


def load_reactions(path: str) -> dict:
    """Load the reactions file into {location: {"base": str, "reactions": [Reaction]}}."""
    with open(path) as fh:
        raw = json.load(fh)
    out = {}
    for loc, entry in raw.items():
        out[loc] = {
            "base": entry.get("base", ""),
            "reactions": [Reaction.from_dict(r) for r in entry.get("reactions", [])],
        }
    return out


def referenced_flags(reactions_by_loc: dict) -> set[str]:
    """Every flag any fragment gates on (requires ∪ forbids)."""
    flags = set()
    for entry in reactions_by_loc.values():
        for r in entry["reactions"]:
            flags |= r.requires | r.forbids
    return flags


def unknown_flags(reactions_by_loc: dict, valid_flags: set[str]) -> set[str]:
    """Lint: fragment flags that no quest/start/goal ever sets — those fragments can
    never fire (or never turn off). Returns the offending flag names."""
    return referenced_flags(reactions_by_loc) - set(valid_flags)
