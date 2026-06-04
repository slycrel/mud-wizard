"""
World-bootstrap command — turn the generated worldbible into a walkable game world.

`worldinit` materializes the scene manifest (world/layout.py) into live Evennia objects:
reactive rooms (one per worldbible location), exits connecting them, and the Wizard game
master — then drops the builder into the start room with an in-character intro. Without
this, a fresh login lands in empty default Limbo with none of the generated content wired
in; this is the "dive in" step.

Safe to re-run: every object it makes is tagged (category ``wb``) and reused on a second
pass, so `worldinit` repairs/extends an existing world rather than duplicating it.

Builder-only (`cmd:perm(Builder)`). The golden rule holds — this places *scenery*;
quest flags and win state remain authoritative in the validated graph.
"""

from evennia import Command, create_object, search_tag

from world.worldbible_loader import Worldbible
from world import layout as layout_mod

ROOM_TYPECLASS = "typeclasses.rooms.WorldbibleRoom"
EXIT_TYPECLASS = "typeclasses.exits.Exit"
TAG_CAT = "wb"  # tag category for everything worldinit creates (idempotency + cleanup)


def _existing(identifier):
    """Return the first object tagged (identifier, category='wb'), or None."""
    matches = search_tag(identifier, category=TAG_CAT)
    return matches[0] if matches else None


class CmdWorldInit(Command):
    """
    Build (or repair) the live game world from the generated worldbible, then enter it.

    Creates a reactive room per worldbible location, exits between them, and the Wizard
    game master, and moves you to the start room. Re-running is safe — existing pieces
    are reused, not duplicated.

    Usage:
        worldinit
    """

    key = "worldinit"
    aliases = ["worldbuild", "bootstrap"]
    locks = "cmd:perm(Builder)"
    help_category = "Building"

    def func(self):
        caller = self.caller
        wb = Worldbible.load()
        layout = layout_mod.load_or_derive(wb)
        if not layout.start:
            caller.msg("|rNo worldbible locations found — generate content first "
                       "(world/worldbible.py).|n")
            return

        created, reused = [], []

        # --- rooms ------------------------------------------------------
        rooms = {}
        for loc in layout.location_order():
            room = _existing(loc)
            if room:
                reused.append(room.key)
            else:
                name = layout.rooms.get(loc, {}).get("name") or layout_mod.pretty_name(loc)
                room = create_object(ROOM_TYPECLASS, key=name)
                room.tags.add(loc, category=TAG_CAT)
                created.append(room.key)
            room.db.wb_location = loc
            base = wb.reactions.get(loc, {}).get("base", "")
            if base:
                room.db.desc = base  # static fallback; look() overlays reactive fragments
            rooms[loc] = room

        # --- exits ------------------------------------------------------
        def ensure_exit(src, dst, direction):
            if not direction or not src or not dst:
                return
            if any(e.destination == dst and e.key == direction for e in src.exits):
                return
            ex = create_object(EXIT_TYPECLASS, key=direction, location=src, destination=dst)
            ex.tags.add(f"{src.db.wb_location}>{dst.db.wb_location}:{direction}", category=TAG_CAT)
            created.append(f"exit {direction}")

        for e in layout.exits:
            src, dst = rooms.get(e.get("from")), rooms.get(e.get("to"))
            ensure_exit(src, dst, e.get("dir"))
            ensure_exit(dst, src, e.get("back"))

        # --- NPCs -------------------------------------------------------
        for npc in layout.npcs:
            ident = f"npc:{npc.get('key')}"
            home = rooms.get(npc.get("location"))
            if not home:
                continue
            obj = _existing(ident)
            if obj:
                if obj.location != home:
                    obj.move_to(home, quiet=True, move_type="teleport")
                reused.append(obj.key)
            else:
                obj = create_object(npc.get("typeclass", layout_mod.WIZARD_TYPECLASS),
                                    key=npc.get("key", "The Wizard"), location=home)
                obj.tags.add(ident, category=TAG_CAT)
                created.append(obj.key)
            if npc.get("desc"):
                obj.db.desc = npc["desc"]

        # --- place the builder and brief them ---------------------------
        start_room = rooms[layout.start]
        caller.home = start_room
        if caller.location != start_room:
            caller.move_to(start_room, quiet=True, move_type="teleport")

        verb = "Built" if created else "Verified"
        summary = (f"|w{verb} the world:|n {len(rooms)} room(s)"
                   + (f", new: {', '.join(created)}" if created else " (all already present)"))
        caller.msg(summary)
        caller.msg(self._intro(wb))
        caller.execute_cmd("look")

    @staticmethod
    def _intro(wb):
        title = ""
        for line in (wb.canon or "").splitlines():
            if line.startswith("# World Bible:"):
                title = line.replace("# World Bible:", "").strip()
                break
        head = f"|c~ {title} ~|n\n" if title else ""
        return (
            f"\n{head}"
            "|yThe Wizard's voice settles around you:|n \"You stand at the threshold of "
            "the tale. The world remembers what you do — so |wact|n.\"\n\n"
            "|wHow to play:|n\n"
            "  |wquests|n                       what you can pursue now (and bonus paths)\n"
            "  |wapproach <quest>|n             face a challenge\n"
            "  |wattempt <quest> = <action>|n   try it — the Wizard judges your idea\n"
            "  |whint <quest>|n / |wbypass <quest>|n   help if you get stuck (never a dead end)\n"
            "  |wsurvey|n / |wlook|n                  the world reflects your deeds\n"
            "  |wtalk The Wizard = <words>|n    speak with the game master\n"
            "  |winspect <thing>|n              picture it (web client + Draw Things)\n"
        )
