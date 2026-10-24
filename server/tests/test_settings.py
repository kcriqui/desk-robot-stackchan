"""The Settings tab's saved settings (brain/settings.py)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import clock, config, settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "settings.json"
        patches = [patch.object(settings, "PATH", self.path)]
        # Whatever a test sets on config goes back afterwards.
        patches += [patch.object(config, a, getattr(config, a)) for keys in settings.SCHEMA.values()
                    for a, _, _ in keys.values() if a]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.dir.cleanup)

    def test_a_change_is_applied_kept_and_undone(self):
        self.assertEqual(settings.change("clock", "h24", True), True)
        self.assertTrue(config.CLOCK_24H)
        self.assertEqual(json.loads(self.path.read_text()), {"clock": {"h24": True}})   # only what changed
        settings.change("screen", "night_mode", "dim")
        config.CLOCK_24H, config.NIGHT_MODE = False, "off"   # a fresh start...
        settings.apply_saved()                                 # ...takes them from the file
        self.assertEqual((config.CLOCK_24H, config.NIGHT_MODE), (True, "dim"))
        self.assertEqual(settings.change("clock", "h24", None), settings.DEFAULTS["clock"]["h24"])   # the default
        self.assertEqual(json.loads(self.path.read_text()), {"screen": {"night_mode": "dim"}})

    def test_values_are_checked(self):
        for sec, key, bad in (("screen", "dark_lux", 0), ("screen", "brightness", 150), ("robot", "near_level", 5000),
                              ("weather", "units", "kelvin"), ("clock", "h24", "yes"), ("robot", "awake_seconds", True),
                              ("nope", "x", 1), ("robot", "human_name", "x" * 41)):
            with self.assertRaises(ValueError, msg=(sec, key, bad)):
                settings.change(sec, key, bad)
        self.assertFalse(self.path.exists())   # nothing bad was saved

    def test_a_bad_file_is_dropped_bit_by_bit(self):
        self.path.write_text(json.dumps({"clock": {"h24": True, "nope": 1}, "screen": {"brightness": 500}}))
        self.assertEqual(settings.load(), {"clock": {"h24": True}})
        self.path.write_text("not json")
        self.assertEqual(settings.load(), {})

    def test_empty_name_means_the_env_one(self):
        settings.change("robot", "human_name", "Sam")
        self.assertEqual(config.HUMAN_NAME, "Sam")
        settings.change("robot", "human_name", "")
        self.assertEqual(config.HUMAN_NAME, settings.DEFAULTS["robot"]["human_name"])
        self.assertEqual(settings.current(0.7)["robot"]["human_name"]["value"], "")

    def test_a_zone_name_becomes_the_robots_rule(self):
        settings.change("clock", "timezone", "America/New_York")
        try:
            self.assertTrue(clock.posix_tz().startswith("EST5EDT"), clock.posix_tz())
        except AssertionError:
            import zoneinfo
            if not zoneinfo.available_timezones():   # Windows without tzdata: nothing to read the rule from
                self.skipTest("no time zone database here")
            raise
        settings.change("clock", "timezone", "PST8PDT,M3.2.0,M11.1.0")   # a rule passes as it is
        self.assertEqual(clock.posix_tz(), "PST8PDT,M3.2.0,M11.1.0")


if __name__ == "__main__":
    unittest.main()
