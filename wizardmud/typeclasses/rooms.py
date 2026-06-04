"""
Room

Rooms are simple containers that has no location of their own.

"""

from evennia.objects.objects import DefaultRoom

from .objects import ObjectParent


class Room(ObjectParent, DefaultRoom):
    """
    Rooms are like any Object, except their location is None
    (which is default). They also use basetype_setup() to
    add locks so they cannot be puppeted or picked up.
    (to change that, use at_object_creation instead)

    See mygame/typeclasses/objects.py for a list of
    properties and methods available on all Objects.
    """

    pass


class WorldbibleRoom(Room):
    """A room tied to a generated worldbible location (``db.wb_location``).

    Its description is *reactive*: ``look`` composes the location's base text with every
    flag-gated fragment the looking player has unlocked (the same content `survey` shows),
    so the room visibly acknowledges what they've done. Built by the `worldinit` command
    from a Layout. State stays authoritative in the quest graph — this only narrates.
    """

    def get_display_desc(self, looker, **kwargs):
        loc = self.db.wb_location
        if loc:
            try:
                # Runtime imports: keep typeclasses decoupled from the command/world layer.
                from commands.quest_cmds import get_wb
                from world.worldbible_loader import Progress
                wb = get_wb()
                p = Progress.from_dict(looker.attributes.get("progress", default=None))
                if not p.flags and not p.done:
                    p.flags = set(wb.world.start)  # same start-flag seeding as `quests`
                text = wb.location_text(loc, p)
                if text:
                    return text
            except Exception:
                pass  # fall back to the plain stored desc if anything is unavailable
        return super().get_display_desc(looker, **kwargs)
