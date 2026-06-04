"""
Scene manifest — how the validated worldbible becomes a *walkable* game world.

The quest graph (solvability.py) and the reactive text (reactive.py) describe *what*
the world contains; this module describes *where it lives in-game*: which rooms exist,
how they connect, where the start is, and which NPCs stand where. The runtime
`worldinit` builder command (commands/build_cmds.py) consumes a Layout to materialize
Evennia rooms/exits/NPCs, so a freshly generated world can be *dived into* instead of
dropping the player in empty Limbo.

Two ways to get a Layout, in priority order:
  1. An authored/generated ``world/generated/worldbible_layout.json`` (richer: canon-
     accurate exit directions, NPC placements, room names) — written by worldbible.py's
     layout stage and editable by hand.
  2. ``derive_layout(wb)`` — a deterministic fallback that builds a connected hub-and-
     spoke map from the worldbible alone (no LLM, no authoring), so ANY generated world
     is always playable even with no layout file.

Pure Python (no Evennia) so it unit-tests standalone and the generator can call it.
The golden rule still holds: the Layout only places *scenery*; quest flags, win state,
and progression remain authoritative in the validated graph.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

GEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated")
LAYOUT_FILE = "worldbible_layout.json"

# Cardinal wheel for the deterministic fallback; (dir, opposite) pairs cycle in order.
_DIRS = [("north", "south"), ("east", "west"), ("south", "north"),
         ("west", "east"), ("up", "down"), ("down", "up")]

WIZARD_TYPECLASS = "typeclasses.llm_npcs.WizardNPC"


def pretty_name(location: str) -> str:
    """'crumbling_keep' -> 'The Crumbling Keep' (a readable in-game room name)."""
    words = location.replace("_", " ").split()
    if not words:
        return location
    title = " ".join(w.capitalize() for w in words)
    return title if words[0].lower() in ("the", "a", "an") else f"The {title}"


@dataclass
class Layout:
    """A materializable scene manifest. Serializes 1:1 to worldbible_layout.json."""
    start: str
    rooms: dict = field(default_factory=dict)   # location -> {"name": str}
    exits: list = field(default_factory=list)    # [{"from","to","dir","back"}]
    npcs: list = field(default_factory=list)      # [{"key","typeclass","location","desc"}]
    objects: list = field(default_factory=list)   # manipulable things (see below)
    # objects: [{"key","location","kind": item|wearable|container|fixture, "desc",
    #            "is_open"?, "locked"?, "key"?(unlock-item key), "contains"?: [ {nested obj} ]}]

    def to_dict(self):
        return {"start": self.start, "rooms": self.rooms, "exits": self.exits,
                "npcs": self.npcs, "objects": self.objects}

    @staticmethod
    def from_dict(d):
        d = d or {}
        return Layout(
            start=d.get("start", ""),
            rooms=dict(d.get("rooms", {})),
            exits=list(d.get("exits", [])),
            npcs=list(d.get("npcs", [])),
            objects=list(d.get("objects", [])),
        )

    def location_order(self):
        """Stable display order: start first, then the rest as authored/derived."""
        seen, order = set(), []
        for loc in [self.start, *self.rooms.keys()]:
            if loc and loc not in seen:
                seen.add(loc)
                order.append(loc)
        return order


def _ordered_locations(wb):
    """All locations, in first-appearance order across quests, then reaction-only ones.
    Quest order puts the natural starting area first."""
    order = []
    for q in wb.world.quests:
        if q.location and q.location not in order:
            order.append(q.location)
    for loc in wb.reactions.keys():
        if loc not in order:
            order.append(loc)
    return order


def _start_location(wb, locations):
    """The room the player begins in: the location of the first non-optional quest whose
    preconditions are exactly the world's start flags. Falls back to the first location."""
    start_flags = set(wb.world.start)
    for q in wb.world.quests:
        if not q.optional and set(q.requires) == start_flags and q.location:
            return q.location
    for q in wb.world.quests:
        if set(q.requires) <= start_flags and q.location:
            return q.location
    return locations[0] if locations else ""


def derive_layout(wb) -> Layout:
    """Build a connected, playable Layout from a Worldbible with no authoring or LLM.

    Hub-and-spoke: the start room connects to every other room via the cardinal wheel,
    guaranteeing the whole map is reachable on foot. The Wizard (game master) stands at
    the start. Authored layout files can refine directions, names, and NPC placement.
    """
    locations = _ordered_locations(wb)
    start = _start_location(wb, locations)
    rooms = {loc: {"name": pretty_name(loc)} for loc in locations}

    exits, spokes = [], [loc for loc in locations if loc != start]
    for i, loc in enumerate(spokes):
        fwd, back = _DIRS[i % len(_DIRS)]
        exits.append({"from": start, "to": loc, "dir": fwd, "back": back})

    npcs = [{
        "key": "The Wizard",
        "typeclass": WIZARD_TYPECLASS,
        "location": start,
        "desc": "A robed figure wreathed in faint storm-light, watching you with "
                "ancient, knowing eyes. Ask and the Wizard will answer — speak with "
                "|wtalk The Wizard = <your words>|n.",
    }] if start else []

    return Layout(start=start, rooms=rooms, exits=exits, npcs=npcs)


def load_or_derive(wb, gen_dir: str = GEN_DIR) -> Layout:
    """Prefer an authored/generated layout file; otherwise derive one deterministically."""
    path = os.path.join(gen_dir, LAYOUT_FILE)
    if os.path.exists(path):
        with open(path) as fh:
            return Layout.from_dict(json.load(fh))
    return derive_layout(wb)


def write_layout(layout: Layout, gen_dir: str = GEN_DIR) -> str:
    """Persist a Layout as the generated manifest. Returns the path written."""
    path = os.path.join(gen_dir, LAYOUT_FILE)
    with open(path, "w") as fh:
        json.dump(layout.to_dict(), fh, indent=2)
    return path
