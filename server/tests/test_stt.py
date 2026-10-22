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


if __name__ == "__main__":
    unittest.main()
