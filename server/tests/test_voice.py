"""Which voice speaks, and the Piper voices' settings. No downloads: Piper
itself is optional and isn't needed to run these."""

import json
import unittest
from pathlib import Path
from unittest import mock

from brain import config, mouth, personality


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


FISH = "0123456789abcdef0123456789abcdef"


def char(voice):
    return personality.parse("someone", {"name": "Someone", "identity": "You are someone.",
                                          "examples": ["[neutral] Hello."], "voice": voice})


class FishVoiceTests(unittest.TestCase):
    """A Fish Audio voice per character. No network: Fish's reply is faked."""

    def setUp(self):
        for p in (mock.patch.dict("os.environ", {"FISH_AUDIO_API_KEY": "k"}),
                  mock.patch.object(config, "TTS_ENGINE", "auto"),
                  mock.patch.object(mouth, "piper_available", return_value=True)):
            p.start()
            self.addCleanup(p.stop)

    def test_its_id_or_page_address(self):
        self.assertEqual(char({"fish": FISH.upper()}).voice["fish"], FISH)
        self.assertEqual(char({"fish": f"https://fish.audio/m/{FISH}/"}).voice["fish"], FISH)
        with self.assertRaises(ValueError):
            char({"fish": "not a voice"})
        with self.assertRaises(ValueError):
            char({"fish": FISH, "fish_speed": 3})

    def test_a_fish_voice_alone_has_piper_standing_in(self):
        c = char({"fish": FISH})
        self.assertEqual(c.voice["piper"], config.PIPER_VOICE)
        self.assertNotIn("fish", char({"piper": config.PIPER_VOICE, "fish": ""}).voice)

    def test_who_speaks_through_fish(self):
        fishy, piper_only, plain = char({"fish": FISH}), char({"piper": config.PIPER_VOICE}), char({})
        self.assertEqual(mouth.engine(fishy), "fish")
        self.assertEqual(mouth.engine(piper_only), "piper")
        self.assertEqual(mouth.engine(plain), "piper")       # Rocky keeps Piper while it's installed...
        with mock.patch.object(mouth, "piper_available", return_value=False):
            self.assertEqual(mouth.fish_voice(plain), config.TTS_VOICE_ID)  # ...else upstream's Fish Rocky
        with mock.patch.object(config, "TTS_ENGINE", "fish"):
            self.assertEqual(mouth.fish_voice(piper_only), config.TTS_VOICE_ID)
            self.assertEqual(mouth.fish_voice(fishy), FISH)
        with mock.patch.dict("os.environ", {"FISH_AUDIO_API_KEY": ""}):
            self.assertEqual(mouth.engine(fishy), "piper")   # no key yet

    def test_the_request(self):
        sent = {}

        class Reply:
            def __init__(self, req, **kw):
                sent["req"] = req
                self.parts = [b"\x01\x02\x03", b"\x04"]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return self.parts.pop(0) if self.parts else b""

        with mock.patch.object(mouth.urllib.request, "urlopen", Reply), \
                mock.patch.object(config, "FISH_MODEL", "s2.1-pro-free"):
            pcm = b"".join(mouth._fish_stream("Hello.", char({"fish": FISH, "fish_speed": 1.1})))
        self.assertEqual(pcm, b"\x01\x02\x03\x04")   # a split sample is carried over
        req = sent["req"]
        self.assertEqual(req.get_header("Model"), "s2.1-pro-free")
        body = json.loads(req.data)
        self.assertEqual((body["reference_id"], body["prosody"], body["sample_rate"]), (FISH, {"speed": 1.1}, 16000))

    def test_piper_speaks_when_fish_fails(self):
        def broken(text, c=None):
            raise OSError("no internet")
            yield

        with mock.patch.object(personality, "CHARACTER", char({"fish": FISH})), \
                mock.patch.object(mouth, "_fish_stream", broken), \
                mock.patch.object(mouth, "_synthesize_piper", return_value=b"\x00\x10" * 3000) as piper:
            out = b"".join(mouth.stream("Hello there."))
        piper.assert_called_once()
        self.assertEqual(len(out), 6000)


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
