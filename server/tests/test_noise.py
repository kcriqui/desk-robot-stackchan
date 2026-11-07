"""The noisy-room settings: the voice filter (main._known_voice)."""

import time
import unittest

from brain import config, main
from brain.speakers import Guess


class FakeVoices:
    def __init__(self, last):
        self.last = last

    def people(self):
        return ["Sam"]


class KnownVoiceTest(unittest.TestCase):
    def setUp(self):
        self.saved = (config.VOICE_ONLY, config.VOICE_ONLY_MIN, main.voices, main.voice_ok_at)
        config.VOICE_ONLY, config.VOICE_ONLY_MIN = True, 0.3
        main.voice_ok_at = 0.0

    def tearDown(self):
        config.VOICE_ONLY, config.VOICE_ONLY_MIN, main.voices, main.voice_ok_at = self.saved

    def check(self, last, heard_at=None):
        main.voices = FakeVoices(last)
        now = time.time()
        return main._known_voice(now - 2, now - 0.5, heard_at or now)

    def test_off_lets_everything_through(self):
        config.VOICE_ONLY = False
        self.assertTrue(self.check(Guess(None, True, -0.2, 2.0, time.time())))

    def test_known_voice_passes(self):
        self.assertTrue(self.check(Guess("Sam", True, 0.5, 2.0, time.time())))

    def test_unknown_voice_is_ignored(self):
        self.assertFalse(self.check(Guess(None, True, 0.1, 2.0, time.time())))

    def test_short_turn_follows_the_last_one(self):
        self.assertTrue(self.check(Guess("Sam", True, 0.5, 2.0, time.time())))
        old = Guess("Sam", True, 0.5, 2.0, time.time() - 60)  # no fingerprint for this turn: the last guess is old
        self.assertTrue(self.check(old))
        main.voice_ok_at = time.time() - 60
        self.assertFalse(self.check(old))

    def test_no_voices_known_lets_everything_through(self):
        main.voices = FakeVoices(None)
        main.voices.people = lambda: []
        now = time.time()
        self.assertTrue(main._known_voice(now - 2, now - 0.5, now))


if __name__ == "__main__":
    unittest.main()
