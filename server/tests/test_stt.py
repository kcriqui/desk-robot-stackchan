"""The speech-to-text settings. No downloads: the models aren't loaded."""

import unittest

from brain import config, moonshine


class SttTests(unittest.TestCase):
    def test_engine_settings(self):
        self.assertIn(config.STT_ENGINE, ("whisper", "moonshine"))
        self.assertIn(config.MOONSHINE_MODEL, moonshine.FILES)
        self.assertGreaterEqual(config.TURN_THREADS, 1)

    def test_moonshine_files_are_pinned(self):
        self.assertRegex(moonshine.REVISION, r"^[0-9a-f]{40}$")
        self.assertRegex(moonshine.TOKENIZER_SHA256, r"^[0-9a-f]{64}$")
        for model, files in moonshine.FILES.items():
            self.assertIn(model, moonshine._SHAPES)
            self.assertEqual(set(files), {"encoder_model.onnx", "decoder_model_merged.onnx"})
            for sha in files.values():
                self.assertRegex(sha, r"^[0-9a-f]{64}$")


class CollapseRepeatsTests(unittest.TestCase):
    def test_a_run_on_utterance_is_said_once(self):
        c = moonshine.collapse_repeats
        self.assertEqual(c("Go to sleep go to sleep"), "Go to sleep")
        self.assertEqual(c("Stop here stop here stop here stop here"), "Stop here")
        self.assertEqual(c("Go to sleep. Go to sleep."), "Go to sleep.")

    def test_real_repeats_are_kept(self):
        c = moonshine.collapse_repeats
        for text in ("Rocky rocky rocky rocky", "Happy happy happy", "What time is it?",
                     "One, two, three, four, five."):
            self.assertEqual(c(text), text)


if __name__ == "__main__":
    unittest.main()
