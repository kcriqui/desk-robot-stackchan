"""Wake words (brain/wakewords.py, kws.py): which phrases there are and whose, how a transcript confirms one, and
the characters' own. The spotter itself runs only where its model is (the NAS's cache)."""

import unittest
from unittest.mock import patch

from brain import config, kws, personality, wakewords

PHRASES = ["hey rocky", "rocky chan", "hey rocky chan", "hey glad us", "glad os", "hey juniper", "computer"]


class MatchTests(unittest.TestCase):
    def m(self, text, loose=False):
        return wakewords.match(text, PHRASES, loose)

    def test_the_phrase_and_the_question_after_it(self):
        self.assertEqual(self.m("Hey Rocky, what time is it?"), ("hey rocky", "what time is it?"))
        self.assertEqual(self.m("Hey Rocky Chan, what's 9 times 16?"), ("hey rocky chan", "what's 9 times 16?"))
        self.assertEqual(self.m("Computer"), ("computer", ""))
        self.assertEqual(self.m("Okay computer, set a timer."), ("computer", "set a timer."))

    def test_spellings_speech_to_text_uses(self):
        self.assertEqual(self.m("RockyChan, are you awake?")[0], "rocky chan")
        self.assertEqual(self.m("Rocky-chan, hi")[0], "rocky chan")
        self.assertEqual(self.m("Hey Gladys, tell me a joke.")[0], "hey glad us")

    def test_loose_only_after_the_spotter_heard_it(self):
        self.assertIsNone(self.m("Hey, Jennifer, how are you"))
        self.assertEqual(self.m("Hey, Jennifer, how are you", loose=True), ("hey juniper", "how are you"))

    def test_it_must_start_what_was_said(self):
        self.assertIsNone(self.m("My computer keeps crashing.", loose=True))
        self.assertIsNone(self.m("I told Rocky about it"))
        self.assertIsNone(self.m("I like rocks", loose=True))
        self.assertIsNone(self.m("I said hey Rocky"))  # mid-sentence: not a wake

    def test_up_to_two_fillers_first(self):
        self.assertEqual(self.m("Okay so, hey Rocky, what time is it?"), ("hey rocky", "what time is it?"))
        self.assertIsNone(self.m("Okay so well, hey Rocky"))  # three is talking, not waking


class TableTests(unittest.TestCase):
    def test_generic_first_then_each_characters(self):
        with patch.object(config, "WAKE_WORDS", "Computer, hey robot"):
            t = wakewords.table([("rocky", ["hey rocky", "computer"]), ("glados", ["hey glad us"])])
        self.assertEqual([(e.phrase, e.owner) for e in t],
                         [("computer", None), ("hey robot", None), ("hey rocky", "rocky"), ("hey glad us", "glados")])
        self.assertEqual(wakewords.owner(t, "hey glad us"), "glados")
        self.assertIsNone(wakewords.owner(t, "computer"))


class CharacterWakeWordTests(unittest.TestCase):
    BASE = {"name": "Juniper", "identity": "An old sea captain.", "examples": ["[happy] Ahoy!"]}

    def test_given_cleaned_and_kept(self):
        c = personality.parse("juniper", {**self.BASE, "wake_words": ["Hey  Juniper", "captain juniper", "hey juniper"]})
        self.assertEqual(c.wake_words, ["hey juniper", "captain juniper"])
        self.assertEqual(personality.to_dict(c)["wake_words"], ["hey juniper", "captain juniper"])
        self.assertEqual(personality.parse("j", {**self.BASE, "wake_words": "hey june, june"}).wake_words,
                         ["hey june", "june"])

    def test_none_given_is_hey_and_the_name(self):
        c = personality.parse("juniper", self.BASE)
        self.assertEqual(c.wake_words, [])
        self.assertEqual(personality.wake_words(c), ["hey juniper"])
        self.assertIn("rocky chan", personality.wake_words(personality.ROCKY))

    def test_bad_ones_are_refused(self):
        for bad in (["hey 2pac"], ["x" * 40], [f"w{i}" for i in range(7)], [3]):
            with self.assertRaises(ValueError, msg=bad):
                personality.parse("j", {**self.BASE, "wake_words": bad})


class SayAsTests(unittest.TestCase):
    """Names are said as they sound, not spelled out (config.SAY_AS, mouth.clean_for_tts)."""

    def test_glados_said_as_a_word(self):
        from brain import mouth
        self.assertEqual(mouth.clean_for_tts("I am GLaDOS."), "I am Gladoss.")
        self.assertEqual(mouth.clean_for_tts("GLaDOS's test chamber"), "Gladoss's test chamber.")
        self.assertEqual(mouth.clean_for_tts("Gladiators are fine"), "Gladiators are fine.")


@unittest.skipUnless(kws.available(), "no keyword model here (it's in the NAS's cache)")
class SpotterTests(unittest.TestCase):
    def test_tokens_and_silence(self):
        import numpy as np
        s = kws.Spotter()
        self.assertEqual(s.tokens("Hey Rocky!"), "▁HE Y ▁RO CK Y")
        s.set_phrases(["hey rocky", "Hey Rocky", "computer"])
        self.assertEqual(s.phrases, ["hey rocky", "computer"])
        self.assertEqual(s.feed(np.zeros(16000, dtype=np.float32)), [])


if __name__ == "__main__":
    unittest.main()
