"""Recognising voices and learning them in an interview (speakers.py), with made-up fingerprints: no model
needed. A turn's "audio" here is its fingerprint's index repeated for as many samples as the turn is long."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from brain import config, speakers

RATE = speakers.RATE


def unit(v):
    v = np.array(v, dtype=np.float32)
    return v / np.linalg.norm(v)


# Kevin, Sam, a stranger, and a turn halfway between Kevin and Sam
VECS = [unit([1, 0, 0, 0]), unit([0, 1, 0, 0]), unit([0, 0, 1, 0]), unit([1, 1, 0, 0])]
KEVIN, SAM, STRANGER, BOTH = range(4)


def turn(who: int, seconds: float = 3.0) -> np.ndarray:
    return np.full(int(seconds * RATE), who, dtype=np.float32)


def fake_fingerprint(audio):
    return None if len(audio) < speakers.MIN_SECONDS * RATE else VECS[int(audio[0])]


class VoicesTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.object(speakers, "fingerprint", fake_fingerprint)
        p.start()
        self.addCleanup(p.stop)
        self.v = speakers.Voices(Path(self.dir.name) / "speakers.json")
        self.v.keep("Kevin", [(VECS[KEVIN], 3.0)], "test")

    def test_a_known_voice_and_a_stranger(self):
        self.assertEqual((self.v.hear(turn(KEVIN)).name, self.v.last.sure), ("Kevin", True))
        self.assertIn("sounds like Kevin", self.v.describe())
        self.v.last = None
        g = self.v.hear(turn(STRANGER))
        self.assertEqual((g.name, g.sure), (None, True))
        self.assertIn("not a voice you know", self.v.describe())

    def test_a_turn_too_short_says_nothing(self):
        self.assertIsNone(self.v.hear(turn(KEVIN, 0.5)))

    def test_an_unclear_turn_goes_to_whoever_was_talking(self):
        self.v.keep("Sam", [(VECS[SAM], 3.0)], "test")
        self.v.hear(turn(SAM))
        g = self.v.hear(turn(BOTH))          # as like Kevin as Sam: too close to call alone
        self.assertEqual((g.name, g.sure), ("Sam", False))
        self.assertIn("probably Sam", self.v.describe())

    def test_an_interview_learns_a_voice(self):
        self.v.start_learning("Sam")
        self.assertIn("interviewing Sam", self.v.describe())
        self.v.hear(turn(KEVIN))             # Kevin answering for Sam doesn't count
        self.assertIn("sounded like Kevin", self.v.describe())
        self.assertEqual(self.v.learning(), ("Sam", 0.0))
        for _ in range(speakers.LEARN_MIN_TURNS - 1):
            self.v.hear(turn(SAM, 20))       # a long answer counts as MAX_SECONDS (all it fingerprints)
        self.assertEqual(self.v.learning(), ("Sam", (speakers.LEARN_MIN_TURNS - 1) * speakers.MAX_SECONDS))
        while self.v.learning():
            self.v.hear(turn(SAM, 20))
        self.assertEqual(self.v.people(), ["Kevin", "Sam"])
        self.assertIn("just finished learning Sam's voice", self.v.describe())
        self.assertNotIn("finished", self.v.describe())   # said once
        self.assertEqual(self.v.people(), ["Kevin", "Sam"])
        self.v.last = None
        self.assertEqual(self.v.hear(turn(SAM)).name, "Sam")
        self.assertEqual(speakers.Voices(self.v.path).people(), ["Kevin", "Sam"])   # kept in the file

    def test_an_interview_can_be_called_off(self):
        self.v.start_learning("Sam")
        self.v.hear(turn(SAM))
        self.assertEqual(self.v.stop_learning(), "Sam")
        self.assertEqual(self.v.people(), ["Kevin"])

    def test_learning_drops_turns_unlike_the_rest(self):
        turns, secs = self.v.keep("Sam", [(VECS[SAM], 3.0)] * 4 + [(VECS[STRANGER], 3.0)], "test")
        self.assertEqual((turns, secs), (4, 12.0))

    def test_forget(self):
        self.assertEqual(self.v.forget("kevin"), "Kevin")
        self.assertIsNone(self.v.forget("Kevin"))
        self.assertEqual(self.v.people(), [])
        self.v.hear(turn(KEVIN))
        self.assertEqual(self.v.describe(), "")          # knowing no one, it says nothing about voices


if __name__ == "__main__":
    unittest.main()
