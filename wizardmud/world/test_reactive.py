"""
Tests for the reactive content resolver.

    cd wizardmud/world && ../../.venv/bin/python -m unittest test_reactive -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reactive import Reaction, compose, unknown_flags  # noqa: E402


class TestReactive(unittest.TestCase):

    def setUp(self):
        self.base = "Black water stretches between skeletal trees."
        self.reactions = [
            Reaction("Rune-stones glimmer beneath the surface, legible to you now.",
                     requires=frozenset(["marsh_secrets_learned"])),
            Reaction("A war-band's tracks churn the mud — the marauders pass freely here.",
                     requires=frozenset(["marauder_alliance_forged"])),
            Reaction("The keep still looms intact on the headland.",
                     forbids=frozenset(["keep_stabilized_or_destroyed"])),
        ]

    def test_base_only_when_no_flags(self):
        out = compose(self.base, self.reactions, set())
        self.assertIn("Black water", out)
        self.assertNotIn("Rune-stones", out)              # gated off
        self.assertIn("still looms intact", out)          # forbids-flag absent -> fires

    def test_requires_fires_when_flag_present(self):
        out = compose(self.base, self.reactions, {"marsh_secrets_learned"})
        self.assertIn("Rune-stones", out)
        self.assertNotIn("war-band", out)                 # other requires not met

    def test_forbids_hides_when_flag_present(self):
        out = compose(self.base, self.reactions, {"keep_stabilized_or_destroyed"})
        self.assertNotIn("still looms intact", out)        # forbidden flag present -> hidden

    def test_multiple_fire_together(self):
        out = compose(self.base, self.reactions,
                      {"marsh_secrets_learned", "marauder_alliance_forged",
                       "keep_stabilized_or_destroyed"})
        self.assertIn("Rune-stones", out)
        self.assertIn("war-band", out)
        self.assertNotIn("still looms intact", out)

    def test_unknown_flag_lint(self):
        by_loc = {"marsh": {"base": self.base, "reactions": self.reactions}}
        valid = {"marsh_secrets_learned", "keep_stabilized_or_destroyed"}  # marauder_* missing
        self.assertEqual(unknown_flags(by_loc, valid), {"marauder_alliance_forged"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
