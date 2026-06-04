"""
Characters

Characters are (by default) Objects setup to be puppeted by Accounts.
They are what you "see" in game. The Character class in this module
is setup to be the "default" character type created by the default
creation commands.

"""

from evennia.objects.objects import DefaultCharacter

from .objects import ObjectParent


class Character(ObjectParent, DefaultCharacter):
    """
    The Character just re-implements some of the Object's methods and hooks
    to represent a Character entity in-game.

    See mygame/typeclasses/objects.py for a list of
    properties and methods available on all Object child classes like this.

    """

    def at_post_puppet(self, **kwargs):
        """After the normal login look, give a one-time orientation crib if we're standing
        in a generated worldbible room — so a new player knows the (non-obvious) commands
        instead of guessing 'take'/'enter' and getting nothing."""
        super().at_post_puppet(**kwargs)
        loc = self.location
        if loc and getattr(loc.db, "wb_location", None) and not self.db._wb_oriented:
            self.db._wb_oriented = True
            self.msg(
                "\n|cThe Wizard's voice settles around you:|n \"You stand within the tale. "
                "Simply say what you do — in your own words — and the world will answer.\"\n"
                "|wFor instance:|n\n"
                "  |wsearch the ruined library for a hidden way|n\n"
                "  |wgo south|n   ·   |wlook around|n   ·   |wask the Wizard what I should do|n\n"
                "No special syntax — just describe your actions naturally.\n"
            )
