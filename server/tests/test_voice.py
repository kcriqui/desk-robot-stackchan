"""Which voice speaks, and the Piper voices' settings. No downloads: Piper
itself is optional and isn't needed to run these."""

import unittest
from pathlib import Path
from unittest import mock

from brain import config, mouth


class VoiceChoiceTests(unittest.TestCase):
    def pick(self, setting, fish, piper):
        with mock.patch.object(config, "TTS_ENGINE", setting), \
             mock.patch.object(mouth, "fish_available", return_value=fish), \
             mock.patch.object(mouth, "piper_available", return_value=piper):
            return mouth.engine()

    def test_auto_prefers_fish_then_piper(self):
        self.assertEqual(self.pick("auto", True, True), "fish")
        self.assertEqual(self.pick("auto", False, True), "piper")
        self.assertEqual(self.pick("auto", False, False), "builtin")

    def test_a_forced_engine_is_used_as_is(self):
        self.assertEqual(self.pick("piper", True, True), "piper")
        self.assertEqual(self.pick("builtin", True, True), "builtin")


class PiperVoiceTests(unittest.TestCase):
    def test_the_chosen_voice_is_set_up(self):
        self.assertIn(config.PIPER_VOICE, config.PIPER_VOICES)

    def test_every_voice_is_pinned_and_tuned(self):
        self.assertRegex(config.PIPER_REVISION, r"^[0-9a-f]{40}$")
        for key, v in config.PIPER_VOICES.items():
            self.assertEqual(len(key.split("-")), 3, f"{key}: expected lang-name-quality")
            if "path" in v:  # a local voice (config.PIPER_LOCAL_DIR)
                self.assertTrue(Path(v["path"] + ".json").is_file(), key)
                continue
            self.assertEqual(set(v["sha256"]), {".onnx", ".onnx.json"}, key)
            for h in v["sha256"].values():
                self.assertRegex(h, r"^[0-9a-f]{64}$", key)
            self.assertGreater(v["speed"], 0, key)
            self.assertLessEqual(abs(v["pitch"]), 12, key)

    def test_pieces_cover_the_buffer(self):
        pcm = bytes(range(256)) * 50
        self.assertEqual(b"".join(mouth._pieces(pcm)), pcm)
        self.assertTrue(all(len(p) <= 4096 for p in mouth._pieces(pcm)))


if __name__ == "__main__":
    unittest.main()
