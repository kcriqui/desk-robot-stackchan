"""When Rocky speaks up on his own, the timers he keeps, and which spoken
phrases count as commands."""

import time
import unittest

from brain import alerts, config, personality
from brain.battery import Reading
from brain.ears import is_command, normalize
from brain.timers import Timers, when


def on_battery(volts, at):
    return Reading(volts, 300, at)


class BatteryWatchTests(unittest.TestCase):
    def feed(self, w, volts, start, minutes_apart=0.2):
        """Three readings at `volts`, `minutes_apart` apart; the last result."""
        out = None
        for i in range(3):
            t = start + i * minutes_apart * 60
            out = w.check(on_battery(volts, t), t) or out
        return out

    def test_warns_more_urgently_as_it_falls(self):
        w = alerts.BatteryWatch()
        self.assertIsNone(self.feed(w, 3.90, 0))                      # ~70%: fine
        self.assertEqual(self.feed(w, 3.67, 100), "battery_low")      # ~18%
        self.assertIsNone(self.feed(w, 3.67, 200))                    # once only at this level
        self.assertEqual(self.feed(w, 3.58, 300), "battery_very_low")  # ~9%
        self.assertIsNone(self.feed(w, 3.58, 400))                    # not again within 10 min
        self.assertEqual(self.feed(w, 3.58, 400 + 11 * 60), "battery_very_low")
        self.assertEqual(self.feed(w, 3.45, 2000), "battery_critical")  # ~4%
        self.assertEqual(self.feed(w, 3.45, 2000 + 3 * 60), "battery_critical")  # every 2 min

    def test_one_odd_reading_is_ignored(self):
        w = alerts.BatteryWatch()
        for i, v in enumerate([3.90, 3.55, 3.90]):  # one sag under load
            self.assertIsNone(w.check(on_battery(v, i), i))

    def test_plugging_in_after_a_warning_says_thanks(self):
        w = alerts.BatteryWatch()
        self.assertEqual(self.feed(w, 3.67, 0), "battery_low")
        self.assertEqual(w.check(Reading(3.9, -400, 100), 100), "battery_saved")
        self.assertIsNone(w.check(Reading(3.9, -400, 110), 110))  # once
        self.assertIsNone(alerts.BatteryWatch().check(Reading(3.9, -400, 0), 0))  # no warning, no thanks

    def test_a_reading_is_looked_at_once(self):
        w = alerts.BatteryWatch()
        r = on_battery(3.67, 5)
        for _ in range(5):
            w.check(r, 5)
        self.assertEqual(len(w.recent), 1)


class TempWatchTests(unittest.TestCase):
    def test_warns_once_then_more_when_hotter(self):
        w, warn = alerts.TempWatch(), config.TEMP_WARN_C
        self.assertIsNone(w.check(warn - 1, 0))
        self.assertEqual(w.check(warn + 1, 10), "hot")
        self.assertIsNone(w.check(warn + 2, 20))
        self.assertEqual(w.check(warn + 6, 30), "very_hot")
        self.assertIsNone(w.check(warn + 6, 60))
        self.assertEqual(w.check(warn + 6, 30 + 601), "very_hot")
        self.assertIsNone(w.check(warn + 1, 700))  # cooling a little doesn't re-warn
        self.assertIsNone(w.check(warn - 6, 800))  # cooled: reset
        self.assertEqual(w.check(warn + 1, 900), "hot")


class TimerTests(unittest.TestCase):
    def test_set_ring_and_cancel(self):
        ts = Timers()
        ts.add(5, "tea", now=0)
        ts.add(1, "", now=0)
        self.assertEqual([t.about for t in ts.pending()], ["", "tea"])  # soonest first
        self.assertEqual([t.about for t in ts.due(now=61)], [""])
        self.assertEqual([t.about for t in ts.cancel("the tea")], ["tea"])
        self.assertEqual(ts.pending(), [])

    def test_the_only_timer_cancels_by_any_name(self):
        ts = Timers()
        ts.add(3, "", now=0)
        self.assertEqual(len(ts.cancel("timer")), 1)

    def test_limits(self):
        ts = Timers()
        for bad in (0, -1, 24 * 60 + 1):
            with self.assertRaises(ValueError):
                ts.add(bad, "x")

    def test_when(self):
        now = time.time()
        self.assertTrue(when(now + 30, now).startswith("in 30 s"))
        self.assertTrue(when(now + 12 * 60, now).startswith("in 12 min"))
        self.assertTrue(when(now + 65 * 60, now).startswith("in 1 h 5 min"))


class LineTests(unittest.TestCase):
    def test_unasked_lines_fill_in(self):
        for key in ("timer", "timer_plain", "battery_low", "battery_very_low", "battery_critical",
                    "battery_saved", "hot", "very_hot"):
            personality.LINES[key].format(about="tea", percent=8, minutes=12, temp=72)


class CommandTests(unittest.TestCase):
    def cmd(self, text, phrases):
        return is_command(normalize(text), phrases)

    def test_sleep_commands(self):
        for text in ("Go to sleep.", "Goodnight!", "Okay, good night.", "Stop listening.",
                     f"{config.ROBOT_NAME}, go to sleep now please.", f"Hey {config.ROBOT_NAME}, sleep."):
            self.assertTrue(self.cmd(text, config.SLEEP_PHRASES), text)

    def test_sentences_that_mention_sleep_are_not_commands(self):
        for text in ("It's only five o'clock, so I will work some more before I go to sleep.",
                     f"{config.ROBOT_NAME}, I need sleep.", "Sleep is very important.",
                     "I said good night to my neighbour yesterday evening."):
            self.assertFalse(self.cmd(text, config.SLEEP_PHRASES), text)

    def test_tracking_commands(self):
        self.assertTrue(self.cmd(f"{config.ROBOT_NAME}, follow me.", config.TRACK_ON_PHRASES))
        self.assertTrue(self.cmd("Stop following me.", config.TRACK_OFF_PHRASES))
        self.assertFalse(self.cmd("Don't look at me like that.", config.TRACK_ON_PHRASES))


if __name__ == "__main__":
    unittest.main()
