"""
Tests for the runtime worldbible loader + progression / stuck-alternative logic.

    cd wizardmud/world && ../../.venv/bin/python -m unittest test_worldbible_loader -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from solvability import Quest, World  # noqa: E402
from worldbible_loader import Worldbible, Progress, FAIL_THRESHOLD  # noqa: E402


def Q(qid, requires=(), grants=(), consumes=(), optional=False):
    return Quest(id=qid, requires=frozenset(requires), grants=frozenset(grants),
                 consumes=frozenset(consumes), optional=optional)


def linear_world():
    world = World(
        quests=[
            Q("q_a", grants=["a"]),
            Q("q_b", requires=["a"], grants=["b"]),
            Q("q_goal", requires=["b"], grants=["win"]),
        ],
        goal=frozenset(["win"]),
    )
    puzzles = {"q_a": {"type": "riddle", "hints": ["look closer", "it's the moon"]}}
    return Worldbible(world, puzzles)


class TestProgression(unittest.TestCase):

    def test_gating_and_win(self):
        wb = linear_world()
        p = Progress()
        self.assertEqual([q.id for q in wb.available(p)], ["q_a"])  # only q_a startable
        wb.complete(p, "q_a")
        self.assertIn("a", p.flags)
        self.assertEqual([q.id for q in wb.available(p)], ["q_b"])  # q_b now unlocked
        wb.complete(p, "q_b")
        wb.complete(p, "q_goal")
        self.assertTrue(wb.is_won(p))

    def test_cannot_recomplete_or_skip(self):
        wb = linear_world()
        p = Progress()
        self.assertFalse(wb.complete(p, "q_b"))   # locked: requires 'a'
        wb.complete(p, "q_a")
        self.assertTrue(wb.complete(p, "q_a") is False)  # already done

    def test_hints_then_guaranteed_alternative(self):
        """The core safety valve: fail + exhaust hints -> a bypass is offered, and
        taking it grants the SAME validated flags, so the player always progresses."""
        wb = linear_world()
        p = Progress()
        # not stuck yet
        self.assertFalse(wb.bypass_available(p, "q_a"))
        # fail a couple times
        wb.record_fail(p, "q_a")
        wb.record_fail(p, "q_a")
        # still has hints to give -> no bypass yet
        self.assertFalse(wb.bypass_available(p, "q_a"))
        h1, rem1 = wb.next_hint(p, "q_a")
        self.assertEqual(h1, "look closer")
        self.assertEqual(rem1, 1)
        h2, rem2 = wb.next_hint(p, "q_a")
        self.assertEqual(rem2, 0)
        none, _ = wb.next_hint(p, "q_a")
        self.assertIsNone(none)
        # hints exhausted + >= FAIL_THRESHOLD fails -> alternative now available
        self.assertGreaterEqual(p.attempts["q_a"], FAIL_THRESHOLD)
        self.assertTrue(wb.bypass_available(p, "q_a"))
        # taking the alternative grants the quest's real flags -> progress guaranteed
        self.assertTrue(wb.complete(p, "q_a", via="bypass"))
        self.assertIn("a", p.flags)
        self.assertEqual([q.id for q in wb.available(p)], ["q_b"])

    def test_progress_serialization_roundtrip(self):
        p = Progress(flags={"a", "b"}, done={"q_a"}, attempts={"q_b": 1}, hints_used={"q_b": 2})
        p2 = Progress.from_dict(p.to_dict())
        self.assertEqual(p.flags, p2.flags)
        self.assertEqual(p.done, p2.done)
        self.assertEqual(p.attempts, p2.attempts)


class TestGeneratedWorldbible(unittest.TestCase):
    """Smoke test against the actually-generated content (skips if not present)."""

    def test_load_real_worldbible(self):
        gen = os.path.join(os.path.dirname(__file__), "generated", "worldbible_quests.json")
        if not os.path.exists(gen):
            self.skipTest("no generated worldbible yet")
        wb = Worldbible.load()
        p = Progress(flags=set(wb.world.start))
        self.assertTrue(wb.available(p), "start state should unlock at least one quest")
        self.assertTrue(wb.world.goal, "should have a win condition")
        # every available quest at start should have a puzzle spec (if puzzles exist)
        if wb.puzzles:
            for q in wb.available(p):
                self.assertIn(q.id, wb.puzzles, f"{q.id} missing a puzzle")


if __name__ == "__main__":
    unittest.main(verbosity=2)
