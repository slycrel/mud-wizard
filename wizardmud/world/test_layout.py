"""Tests for the scene-manifest layer (layout.py) — pure Python, no Evennia/network."""

import unittest

from worldbible_loader import Worldbible
import layout as L


class TestLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wb = Worldbible.load()

    def test_pretty_name(self):
        self.assertEqual(L.pretty_name("crumbling_keep"), "The Crumbling Keep")
        # underscores -> spaces; the hyphenated form lives in the authored layout file.
        self.assertEqual(L.pretty_name("storm_wracked_coast"), "The Storm Wracked Coast")
        self.assertEqual(L.pretty_name("the_void"), "The Void")  # no double "The"

    def test_derive_picks_start_from_start_flags(self):
        lay = L.derive_layout(self.wb)
        # investigate_keep is the only non-optional quest gated solely on the start flag.
        self.assertEqual(lay.start, "crumbling_keep")

    def test_derive_is_fully_connected(self):
        """Every non-start location is reachable on foot from the start (hub-and-spoke)."""
        lay = L.derive_layout(self.wb)
        reachable = {lay.start}
        for e in lay.exits:
            if e["from"] == lay.start:
                reachable.add(e["to"])
        self.assertEqual(reachable, set(lay.rooms.keys()))

    def test_derive_places_wizard_at_start(self):
        lay = L.derive_layout(self.wb)
        self.assertTrue(lay.npcs)
        wiz = lay.npcs[0]
        self.assertEqual(wiz["location"], lay.start)
        self.assertIn("WizardNPC", wiz["typeclass"])

    def test_exits_have_return_paths(self):
        lay = L.derive_layout(self.wb)
        for e in lay.exits:
            self.assertTrue(e.get("dir") and e.get("back"), "each exit needs a return path")

    def test_roundtrip(self):
        lay = L.derive_layout(self.wb)
        again = L.Layout.from_dict(lay.to_dict())
        self.assertEqual(again.start, lay.start)
        self.assertEqual(again.rooms, lay.rooms)
        self.assertEqual(again.exits, lay.exits)

    def test_authored_file_loads_and_matches_canon_geography(self):
        """The committed layout file overrides derivation and matches the prose
        ('the marsh sprawls southward from the keep')."""
        lay = L.load_or_derive(self.wb)
        self.assertEqual(lay.start, "crumbling_keep")
        marsh = [e for e in lay.exits if e["to"] == "whispering_marsh"]
        self.assertTrue(marsh and marsh[0]["dir"] == "south")

    def test_location_order_start_first(self):
        lay = L.load_or_derive(self.wb)
        self.assertEqual(lay.location_order()[0], lay.start)


if __name__ == "__main__":
    unittest.main()
