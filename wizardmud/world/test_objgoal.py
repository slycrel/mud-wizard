"""Tests for object-goal evaluation, lint, and the generator apply step. Pure / no network."""

import json
import os
import tempfile
import unittest

import objgoal


def snap(**objs):
    """Build a snapshot; each obj is name -> dict of state flags (exists defaults True)."""
    out = {}
    for name, st in objs.items():
        s = {"exists": True, "open": False, "locked": False, "held": False,
             "worn": False, "in_room": True}
        s.update(st)
        out[name.replace("_", " ")] = s
    return out


class TestEvaluate(unittest.TestCase):
    def test_all_must_hold(self):
        goal = {"all": [{"obj": "iron chest", "is": "open"},
                        {"obj": "tarnished amulet", "is": "held"}]}
        s = snap(iron_chest={"open": True}, tarnished_amulet={"held": True})
        self.assertTrue(objgoal.evaluate(goal, s))
        s2 = snap(iron_chest={"open": True}, tarnished_amulet={"held": False})
        self.assertFalse(objgoal.evaluate(goal, s2))

    def test_any(self):
        goal = {"any": [{"obj": "lever", "is": "open"}, {"obj": "door", "is": "open"}]}
        self.assertTrue(objgoal.evaluate(goal, snap(lever={"open": False}, door={"open": True})))
        self.assertFalse(objgoal.evaluate(goal, snap(lever={"open": False}, door={"open": False})))

    def test_negations_and_worn(self):
        self.assertTrue(objgoal.evaluate({"all": [{"obj": "chest", "is": "closed"}]},
                                         snap(chest={"open": False})))
        self.assertTrue(objgoal.evaluate({"all": [{"obj": "gate", "is": "unlocked"}]},
                                         snap(gate={"locked": False})))
        self.assertTrue(objgoal.evaluate({"all": [{"obj": "cloak", "is": "worn"}]},
                                         snap(cloak={"worn": True})))

    def test_substring_match(self):
        # goal says "amulet", snapshot has "tarnished amulet"
        self.assertTrue(objgoal.evaluate({"all": [{"obj": "amulet", "is": "held"}]},
                                         snap(tarnished_amulet={"held": True})))

    def test_empty_goal_never_satisfied(self):
        self.assertFalse(objgoal.evaluate(None, snap()))
        self.assertFalse(objgoal.evaluate({}, snap()))
        self.assertFalse(objgoal.evaluate({"all": []}, snap()))

    def test_missing_object(self):
        self.assertFalse(objgoal.evaluate({"all": [{"obj": "ghost", "is": "held"}]}, snap()))


class TestLint(unittest.TestCase):
    OBJECTS = [
        {"key": "rune-etched key", "kind": "item"},
        {"key": "iron chest", "kind": "container", "locked": True, "key_item": "rune-etched key",
         "contains": [{"key": "storm-cloak", "kind": "wearable"}]},
        {"key": "lever", "kind": "fixture"},
    ]

    def test_reachable_is_clean(self):
        goals = {"q1": {"all": [{"obj": "iron chest", "is": "open"},
                                {"obj": "storm-cloak", "is": "worn"}]}}
        self.assertEqual(objgoal.lint(goals, self.OBJECTS), [])

    def test_unknown_object_warned(self):
        warns = objgoal.lint({"q1": {"all": [{"obj": "mirror", "is": "open"}]}}, self.OBJECTS)
        self.assertTrue(any("unknown object" in w for w in warns))

    def test_unreachable_locked_container_warned(self):
        objs = [{"key": "vault", "kind": "container", "locked": True, "key_item": "gold key"}]
        warns = objgoal.lint({"q1": {"all": [{"obj": "vault", "is": "open"}]}}, objs)
        self.assertTrue(any("does not exist" in w for w in warns))

    def test_non_takeable_held_warned(self):
        warns = objgoal.lint({"q1": {"all": [{"obj": "lever", "is": "held"}]}}, self.OBJECTS)
        self.assertTrue(any("not takeable" in w for w in warns))


class TestApplyObjectsResult(unittest.TestCase):
    def test_writes_layout_and_merges_goals(self):
        import worldbible
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "worldbible_layout.json"), "w") as fh:
                json.dump({"start": "keep", "rooms": {"keep": {"name": "Keep"}},
                           "exits": [], "npcs": [], "objects": []}, fh)
            with open(os.path.join(d, "worldbible_puzzles.json"), "w") as fh:
                json.dump({"q1": {"setup": "x"}}, fh)
            result = {
                "objects": [{"key": "iron chest", "location": "keep", "kind": "container",
                             "locked": True, "key_item": "key",
                             "contains": [{"key": "key", "kind": "item"}]}],
                "object_goals": {"q1": {"all": [{"obj": "iron chest", "is": "open"}]}},
            }
            n_obj, n_goals, warns = worldbible.apply_objects_result(result, gen_dir=d)
            self.assertEqual((n_obj, n_goals), (1, 1))
            lay = json.load(open(os.path.join(d, "worldbible_layout.json")))
            self.assertEqual(lay["objects"][0]["key"], "iron chest")
            pz = json.load(open(os.path.join(d, "worldbible_puzzles.json")))
            self.assertIn("object_goal", pz["q1"])


if __name__ == "__main__":
    unittest.main()
