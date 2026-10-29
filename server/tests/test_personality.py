"""Rocky's character file and the speaker tuning his voice goes through."""

import json
import unittest

import numpy as np

from brain import config, mouth, personality


class CharacterTests(unittest.TestCase):
    def test_prompt_is_complete(self):
        self.assertIn(config.HUMAN_NAME, personality.SYSTEM_PROMPT)
        self.assertIn(f"One to {config.REPLY_MAX_SENTENCES} sentences", personality.SYSTEM_PROMPT)
        self.assertIn("[neutral]", personality.SYSTEM_PROMPT)

    def test_every_canned_line_exists(self):
        for key in ("wake", "sleep", "track_on", "track_off"):
            self.assertTrue(personality.LINES.get(key), f"missing the {key} line")

    def test_voice_settings(self):
        self.assertEqual(len(config.TTS_VOICE_ID), 32)
        self.assertIn(config.ROBOT_NAME, config.STT_PROMPT)

    def test_rockys_wake_words_follow_his_names(self):
        from brain import wakewords
        words = personality.wake_words(personality.ROCKY)
        for n in config.NAMES:  # ROBOT_NAME's and the aliases' spoken forms
            self.assertIn(f"hey {n}", words)
            self.assertIn(n, words)
        self.assertIn("hey ricky", words)  # and what speech-to-text hears for "hey Rocky"
        name = config.NAMES[-1]
        self.assertEqual(wakewords.match(f"{name.title()}, what time is it?", words), (name, "what time is it?"))
        self.assertIsNone(wakewords.match(f"I told {name.title()} about it", words))


class CharacterFileTests(unittest.TestCase):
    """Other characters (server/characters/*.json) and switching to them."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        self.dir = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(personality, "CHARACTERS_DIR", Path(self.dir.name))
        self.patch.start()
        self.path = Path(self.dir.name)

    def tearDown(self):
        personality.use("rocky")
        self.patch.stop()
        self.dir.cleanup()

    def write(self, name, **fields):
        import json
        c = {"name": "Unit", "identity": "You are Unit, a test robot on {human}'s desk.",
             "style": "- Speak in short, plain sentences.", "examples": ["[neutral] Test complete, {human}."],
             **fields}
        (self.path / f"{name}.json").write_text(json.dumps(c), encoding="utf-8")

    def test_a_character_can_talk_longer(self):
        self.write("long", max_sentences=8)
        personality.use("long")
        self.assertEqual(personality.max_sentences(), 8)
        self.assertIn("One to 8 sentences", personality.SYSTEM_PROMPT)
        self.assertEqual(personality.to_dict(personality.load("long"))["max_sentences"], 8)   # kept when saved
        personality.use("rocky")
        self.assertEqual(personality.max_sentences(), config.REPLY_MAX_SENTENCES)
        self.write("bad", max_sentences=40)
        with self.assertRaises(ValueError):
            personality.load("bad")

    def test_switching_changes_prompt_lines_voice_and_face(self):
        self.write("unit", lines={"wake": "Ready, {human}."}, leads={"search": "Querying."},
                   voice={"piper": "en_US-kristin-medium", "pitch": 1, "intonation": 0}, face="visor")
        personality.use("unit")
        p = personality.SYSTEM_PROMPT
        self.assertTrue(p.startswith(f"You are Unit, a test robot on {config.HUMAN_NAME}'s desk."))
        self.assertIn("you are Unit. Your pronouns are they/them.", p)     # the shared rules name them
        self.assertIn(f"One to {config.REPLY_MAX_SENTENCES} sentences", p)
        self.assertIn("[neutral] Test complete", p)
        self.assertNotIn("Fist my bump", p)                                # nothing of Rocky's leaks in
        self.assertIn("`switch_character` hands the robot over to another character: rocky (", p)
        self.assertIn(f"rocky ({config.ROBOT_NAME}, he/him: ", p)          # so they're spoken of rightly
        self.assertNotIn("{human}", p)
        self.assertEqual(personality.LINES["wake"], f"Ready, {config.HUMAN_NAME}.")
        self.assertEqual(personality.LINES["sleep"], personality.DEFAULT_LINES["sleep"])   # plain default
        key, tune = personality.voice()
        self.assertEqual((key, tune["pitch"], tune["intonation"]), ("en_US-kristin-medium", 1, 0))
        self.assertIn("sha256", tune)                                      # config's entry underneath
        self.assertEqual(personality.face_pack(), "visor")
        self.assertEqual(personality.name(), "Unit")                     # he goes by their name
        personality.use("rocky")
        self.assertTrue(personality.SYSTEM_PROMPT.startswith("You are Rocky"))
        self.assertIn("Your pronouns are he/him.", personality.SYSTEM_PROMPT)
        self.assertEqual(personality.pronoun(1), "him")
        self.assertEqual(personality.face_pack(), config.FACE_PACK)
        self.assertEqual(personality.name(), config.ROBOT_NAME)          # and by his own again

    def test_bad_characters_are_refused(self):
        self.write("novoice", voice={"piper": "nobody"})
        self.write("noexamples", examples=["no emotion tag"])
        for cid in ("novoice", "noexamples", "missing", "../etc"):
            with self.assertRaises(ValueError, msg=cid):
                personality.load(cid)
        self.assertEqual([c.id for c in personality.available()], ["rocky"])   # broken ones are skipped

    def test_a_local_voice_missing_here_stands_in_as_the_default(self):
        self.write("unit", voice={"piper": "en_US-nobody-medium", "pitch": 3, "speaker": "x"})
        key, tune = personality.voice(personality.load("unit"))
        self.assertEqual(key, config.PIPER_VOICE)
        self.assertEqual(tune, config.PIPER_VOICES[config.PIPER_VOICE])   # without the missing voice's tuning

    def test_every_line_of_every_kind_fills_in(self):
        self.write("unit")
        for cid in ("rocky", "unit"):
            personality.use(cid)
            for key, line in personality.LINES.items():
                line.format(about="tea", percent=8, minutes=12, temp=72, at="7:05")   # no stray {placeholders}


class CharacterEditingTests(CharacterFileTests):
    """The live view's Personalities card: save, delete, import and draft."""

    def unit(self, **fields):
        return {"name": "Unit", "identity": "You are Unit.", "examples": "[happy] Hi, {human}.\nNo tag here.", **fields}

    def test_save_names_a_new_file_and_tidies_examples(self):
        cid = personality.save(self.unit())
        self.assertEqual(cid, "unit")
        self.assertEqual(personality.save(self.unit()), "unit-2")          # never overwrites another
        c = personality.load("unit")
        self.assertEqual(c.examples, ["[happy] Hi, {human}.", "[neutral] No tag here."])
        self.assertEqual(personality.save(self.unit(name="Unit Prime"), "unit"), "unit")   # edits in place
        self.assertEqual(personality.load("unit").name, "Unit Prime")
        personality.delete("unit")
        self.assertEqual([c.id for c in personality.available()], ["rocky", "unit-2"])

    def test_rocky_can_be_edited_and_reset(self):
        with self.assertRaises(ValueError):
            personality.delete("rocky")                     # nothing to reset yet
        self.assertIs(personality.load("rocky"), personality.ROCKY)
        personality.save({**personality.to_dict(personality.ROCKY), "style": "Short."}, "rocky")
        self.assertTrue(personality.edited("rocky"))
        self.assertEqual(personality.load("rocky").style, "Short.")
        self.assertEqual([c.id for c in personality.available()].count("rocky"), 1)
        self.assertIn("rocky chan", personality.wake_words(personality.load("rocky")))   # still his names
        personality.delete("rocky")                         # back to the original
        self.assertIs(personality.load("rocky"), personality.ROCKY)
        self.assertEqual(personality.save(self.unit(name="Rocky")), "rocky-2")   # a new one called Rocky is a copy

    def test_the_default_cant_be_deleted(self):
        cid = personality.save(self.unit())
        from unittest import mock
        with mock.patch.object(config, "CHARACTER", cid):
            with self.assertRaises(ValueError):
                personality.delete(cid)
        personality.delete(cid)

    def test_bad_lines_and_voices_are_refused(self):
        for bad in ({"lines": {"wake": "Hi {about}"}},          # wake has nothing to fill in
                    {"lines": {"timer": "Done: {about"}},       # a stray brace
                    {"lines": {"hello": "Hi"}},                 # no such line
                    {"voice": {"piper": "en_US-kristin-medium", "pitch": 40}},
                    {"voice": {"piper": "en_US-kristin-medium", "volume": 1}},
                    {"voice": {"piper": "en_US-kristin-medium", "speaker": "spike"}},   # she's the only one
                    {"voice": {"piper": "en_GB-semaine-medium", "speaker": "nobody"}},
                    {"name": "{human}"}, {"examples": []}, {"pronouns": "xe"}):
            with self.assertRaises(ValueError, msg=bad):
                personality.save(self.unit(**bad))
        cid = personality.save(self.unit(lines={"timer": "{human}, {about} done."},
                                         voice={"piper": "en_US-kristin-medium", "pitch": 1, "noise": None}))
        self.assertEqual(personality.load(cid).lines["timer"], "{human}, {about} done.")
        cid = personality.save(self.unit(voice={"piper": "en_GB-semaine-medium", "speaker": "spike", "pitch": -3}))
        self.assertEqual(personality.voice(personality.load(cid))[1]["speaker"], "spike")

    def test_a_v2_personality_imports(self):
        cid = personality.save({"name": "Juniper", "description": "a sea captain", "voice_id": "0" * 32,
                                "tts_model": "drama-3", "prompt": "You are Juniper, {human}'s robot.",
                                "examples": "[happy] Ahoy!\n[sad] Becalmed.", "lines": {"wake": "Aye?"},
                                "wake_words": ["hey juniper"]})
        c = personality.load(cid)
        self.assertEqual((c.identity, c.examples, c.lines, c.voice),
                         ("You are Juniper, {human}'s robot.", ["[happy] Ahoy!", "[sad] Becalmed."], {"wake": "Aye?"}, {}))

    def test_draft_keeps_what_fits(self):
        from types import SimpleNamespace as NS
        answer = json.dumps({"pronouns": "he", "identity": "You are Juniper, a man.", "style": "- Salty.", "emotions": "Happy mostly.",
                             "examples": ["[happy] Ahoy, {human}!", "Land ho."],
                             "leads": {"search": "Checking the charts.", "dance": "A hornpipe!", "x": "y"},
                             "lines": {"wake": "Aye?", "timer": "{about} is done, {human}.", "sleep": "Bad {temp}"}})
        client = NS(chat=NS(completions=NS(create=lambda **kw: NS(choices=[NS(message=NS(content="Here: " + answer))]))))
        d = personality.draft(client, "Juniper", "an old sea captain")
        self.assertEqual(d["examples"], ["[happy] Ahoy, {human}!", "[neutral] Land ho."])
        self.assertEqual(d["pronouns"], "he")
        self.assertEqual(d["leads"], {"search": "Checking the charts.", "dance": "A hornpipe!"})
        self.assertEqual(d["lines"], {"wake": "Aye?", "timer": "{about} is done, {human}."})   # sleep can't use {temp}
        self.assertEqual(personality.load(personality.save(d)).name, "Juniper")              # it saves as it is
        with self.assertRaises(ValueError):
            personality.draft(client, "Juniper", "short")


class SpeakerTuningTests(unittest.TestCase):
    def test_equalizer_cuts_bass_and_lifts_presence(self):
        sr = mouth.SAMPLE_RATE
        t = np.arange(sr) / sr
        x = (0.3 * np.sin(2 * np.pi * 100 * t) + 0.3 * np.sin(2 * np.pi * 3000 * t)).astype(np.float32)
        y = mouth.Equalizer(300, 6.0).process(x)
        spec_x, spec_y = np.abs(np.fft.rfft(x)) ** 2, np.abs(np.fft.rfft(y)) ** 2
        f = np.fft.rfftfreq(len(x), 1 / sr)
        bass = lambda s: s[(f > 50) & (f < 150)].sum()
        top = lambda s: s[(f > 2900) & (f < 3100)].sum()
        self.assertLess(10 * np.log10(bass(spec_y) / bass(spec_x)), -15)
        self.assertGreater(10 * np.log10(top(spec_y) / top(spec_x)), 4)

    def test_equalizer_off_is_passthrough_and_chunks_join_cleanly(self):
        x = np.random.default_rng(1).normal(0, 0.1, 4000).astype(np.float32)
        self.assertTrue(np.array_equal(mouth.Equalizer(0, 0).process(x), x))
        whole = mouth.Equalizer(300, 6.0).process(x)
        eq = mouth.Equalizer(300, 6.0)
        chunked = np.concatenate([eq.process(x[:1234]), eq.process(x[1234:])])
        self.assertTrue(np.allclose(whole, chunked, atol=1e-6))

    def test_leveler_reads_config_when_built(self):
        saved = (config.TTS_LEVEL, config.TTS_HIGHPASS_HZ, config.TTS_PRESENCE_DB)
        try:
            config.TTS_LEVEL, config.TTS_HIGHPASS_HZ, config.TTS_PRESENCE_DB = 0.22, 300.0, 6.0
            lev = mouth.Leveler()
            self.assertEqual(lev.level, 0.22)
            self.assertEqual(len(lev.eq.stages), 3)
            config.TTS_HIGHPASS_HZ, config.TTS_PRESENCE_DB = 0.0, 0.0
            self.assertEqual(len(mouth.Leveler().eq.stages), 0)
        finally:
            config.TTS_LEVEL, config.TTS_HIGHPASS_HZ, config.TTS_PRESENCE_DB = saved


if __name__ == "__main__":
    unittest.main()
