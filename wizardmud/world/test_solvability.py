"""
Tests for the narrative solvability validator.

Run standalone (no Evennia needed):
    cd wizardmud/world && ../../.venv/bin/python -m unittest test_solvability -v
or just:
    ../../.venv/bin/python test_solvability.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from solvability import Quest, World, validate  # noqa: E402


def Q(qid, requires=(), grants=(), consumes=(), optional=False):
    return Quest(
        id=qid,
        requires=frozenset(requires),
        grants=frozenset(grants),
        consumes=frozenset(consumes),
        optional=optional,
    )


class TestSolvability(unittest.TestCase):

    def test_solvable_linear(self):
        """A straightforward winnable arc passes clean."""
        world = World(
            quests=[
                Q("get_key", grants=["has_key"]),
                Q("open_door", requires=["has_key"], consumes=["has_key"],
                  grants=["door_open"]),
                Q("reach_treasure", requires=["door_open"], grants=["treasure"]),
            ],
            start=frozenset(),
            goal=frozenset(["treasure"]),
        )
        report = validate(world)
        self.assertTrue(report.ok, msg=str(report))
        self.assertFalse(report.errors)

    def test_unreachable_goal(self):
        """A goal flag nothing grants is caught by the optimistic pre-filter."""
        world = World(
            quests=[Q("get_key", grants=["has_key"])],
            goal=frozenset(["dragon_slain"]),  # nothing grants this
        )
        report = validate(world)
        self.assertFalse(report.ok)
        self.assertTrue(any("never obtainable" in e for e in report.errors), str(report))

    def test_self_softlock_resource_consumed_twice(self):
        """One key, two REQUIRED doors that each consume it -> unwinnable."""
        world = World(
            quests=[
                Q("get_key", grants=["has_key"]),
                Q("door_a", requires=["has_key"], consumes=["has_key"], grants=["a_open"]),
                Q("door_b", requires=["has_key"], consumes=["has_key"], grants=["b_open"]),
            ],
            goal=frozenset(["a_open", "b_open"]),
        )
        report = validate(world)
        self.assertFalse(report.ok, msg=str(report))
        # passes the optimistic check (which ignores consumption) but the
        # state search proves no ordering wins:
        self.assertTrue(any("unreachable" in e for e in report.errors), str(report))

    def test_sidequest_interference(self):
        """An OPTIONAL quest can spend the key the required path needs."""
        world = World(
            quests=[
                Q("get_key", grants=["has_key"]),
                Q("open_door", requires=["has_key"], consumes=["has_key"],
                  grants=["door_open"]),
                Q("reach_treasure", requires=["door_open"], grants=["treasure"]),
                # sidequest that also eats the (only) key:
                Q("pick_lock_for_fun", requires=["has_key"], consumes=["has_key"],
                  grants=["lock_picked"], optional=True),
            ],
            goal=frozenset(["treasure"]),
        )
        report = validate(world)
        self.assertFalse(report.ok, msg=str(report))
        joined = " ".join(report.errors)
        self.assertIn("soft-lock", joined)
        self.assertIn("pick_lock_for_fun", joined)
        self.assertIn("optional", joined)  # correctly attributed to the sidequest

    def test_benign_sidequest(self):
        """A sidequest that touches no main-path resource is fine."""
        world = World(
            quests=[
                Q("get_key", grants=["has_key"]),
                Q("open_door", requires=["has_key"], consumes=["has_key"],
                  grants=["door_open"]),
                Q("reach_treasure", requires=["door_open"], grants=["treasure"]),
                Q("explore_garden", grants=["saw_garden"], optional=True),
            ],
            goal=frozenset(["treasure"]),
        )
        report = validate(world)
        self.assertTrue(report.ok, msg=str(report))

    def test_multiple_keys_two_doors_ok(self):
        """Two keys for two doors -> solvable; resource accounting respects counts."""
        world = World(
            quests=[
                Q("get_key_1", grants=["has_key"]),
                Q("get_key_2", grants=["has_key"]),  # a second unit of the resource
                Q("door_a", requires=["has_key"], consumes=["has_key"], grants=["a_open"]),
                Q("door_b", requires=["has_key"], consumes=["has_key"], grants=["b_open"]),
            ],
            goal=frozenset(["a_open", "b_open"]),
        )
        report = validate(world)
        self.assertTrue(report.ok, msg=str(report))


    def test_inert_reward_warning(self):
        """A grant nothing uses is flagged as an inert reward (warning, not error)."""
        world = World(
            quests=[
                Q("get_key", grants=["has_key"]),
                Q("open_door", requires=["has_key"], consumes=["has_key"], grants=["door_open"]),
                Q("reach_treasure", requires=["door_open"], grants=["treasure"]),
                Q("admire_view", grants=["saw_view"], optional=True),  # inert reward
            ],
            goal=frozenset(["treasure"]),
        )
        report = validate(world)
        self.assertTrue(report.ok)  # still solvable; the lint is only a warning
        self.assertTrue(
            any("inert reward" in w and "saw_view" in w for w in report.warnings),
            msg=str(report),
        )

    def test_optional_goal_makes_reward_meaningful(self):
        """A sidequest grant that feeds an optional_goal is NOT inert."""
        world = World(
            quests=[
                Q("get_key", grants=["has_key"]),
                Q("open_door", requires=["has_key"], consumes=["has_key"], grants=["door_open"]),
                Q("reach_treasure", requires=["door_open"], grants=["treasure"]),
                Q("free_the_prisoner", grants=["prisoner_freed"], optional=True),
            ],
            goal=frozenset(["treasure"]),
            optional_goals=frozenset(["prisoner_freed"]),  # bonus objective
        )
        report = validate(world)
        self.assertTrue(report.ok)
        self.assertFalse(
            any("inert reward" in w for w in report.warnings), msg=str(report)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
