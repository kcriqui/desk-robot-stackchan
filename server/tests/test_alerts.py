"""When Rocky speaks up on his own, the timers he keeps, and which spoken
phrases count as commands."""

import time
import unittest
import unittest.mock

from brain import alerts, config, personality
from brain.battery import Reading
from brain.ears import is_command, normalize, strip_leading_word
from brain.timers import Timers, when


def on_battery(volts, at):
    return Reading(volts, 240, at)  # the curve's own draw: the voltages mean what the curve says


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
        self.assertIsNone(self.feed(w, 3.87, 0))                      # ~70%: fine
        self.assertEqual(self.feed(w, 3.60, 100), "battery_low")      # ~17%
        self.assertIsNone(self.feed(w, 3.60, 200))                    # once only at this level
        self.assertEqual(self.feed(w, 3.52, 300), "battery_very_low")  # ~9%
        self.assertIsNone(self.feed(w, 3.52, 400))                    # not again within 10 min
        self.assertEqual(self.feed(w, 3.52, 400 + 11 * 60), "battery_very_low")
        self.assertEqual(self.feed(w, 3.45, 2000), "battery_critical")  # ~4%
        self.assertEqual(self.feed(w, 3.45, 2000 + 3 * 60), "battery_critical")  # every 2 min

    def test_one_odd_reading_is_ignored(self):
        w = alerts.BatteryWatch()
        for i, v in enumerate([3.90, 3.55, 3.90]):  # one sag under load
            self.assertIsNone(w.check(on_battery(v, i), i))

    def test_plugging_in_after_a_warning_says_thanks(self):
        w = alerts.BatteryWatch()
        self.assertEqual(self.feed(w, 3.60, 0), "battery_low")
        self.assertEqual(w.check(Reading(3.9, -400, 100), 100), "battery_saved")
        self.assertIsNone(w.check(Reading(3.9, -400, 110), 110))  # once
        self.assertIsNone(alerts.BatteryWatch().check(Reading(3.9, -400, 0), 0))  # no warning, no thanks

    def test_a_reading_is_looked_at_once(self):
        w = alerts.BatteryWatch()
        r = on_battery(3.60, 5)
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


class PowerOffWatchTests(unittest.TestCase):
    IDLE = config.IDLE_POWER_OFF_MINUTES * 60

    def run_until(self, w, volts, asleep, timers, start, end, step=10):
        """Feed a reading every `step` s from `start` to `end`; the first key."""
        for t in range(start, end + 1, step):
            key = w.check(on_battery(volts, t), asleep, timers, t)
            if key:
                return key, t
        return None, None

    def test_powers_off_after_long_asleep_on_battery(self):
        w = alerts.PowerOffWatch()
        key, at = self.run_until(w, 3.90, True, False, 0, self.IDLE + 60)
        self.assertEqual(key, "power_off_idle")
        self.assertGreaterEqual(at, self.IDLE)

    def test_waking_restarts_the_clock(self):
        w = alerts.PowerOffWatch()
        self.run_until(w, 3.90, True, False, 0, self.IDLE - 60)
        w.check(on_battery(3.90, self.IDLE - 50), False, False, self.IDLE - 50)   # he woke
        key, _ = self.run_until(w, 3.90, True, False, self.IDLE - 40, self.IDLE + 60)
        self.assertIsNone(key)

    def test_never_on_usb(self):
        w = alerts.PowerOffWatch()
        for t in range(0, self.IDLE + 600, 10):
            self.assertIsNone(w.check(Reading(3.40, 2, t), True, False, t))   # USB: battery idle

    def test_a_timer_keeps_him_on_unless_the_battery_is_low(self):
        w = alerts.PowerOffWatch()
        key, _ = self.run_until(w, 3.87, True, True, 0, self.IDLE + 600)   # ~70%
        self.assertIsNone(key)
        w = alerts.PowerOffWatch()
        key, _ = self.run_until(w, 3.59, True, True, 0, self.IDLE + 60)    # ~15%
        self.assertEqual(key, "power_off_idle_timers")

    def test_nearly_flat_shuts_down_even_awake_with_timers(self):
        w = alerts.PowerOffWatch()
        key, at = self.run_until(w, 3.40, False, True, 0, 100)
        self.assertEqual((key, at), ("power_off_battery", 20))   # three readings in
        w = alerts.PowerOffWatch()   # one dip (talking) isn't enough
        w.check(on_battery(3.85, 0), False, False, 0)
        w.check(on_battery(3.45, 10), False, False, 10)
        self.assertIsNone(w.check(on_battery(3.85, 20), False, False, 20))

    def test_stale_readings_dont_count(self):
        w = alerts.PowerOffWatch()
        r = on_battery(3.40, 0)
        for t in (0, 100, 200, 300):
            self.assertIsNone(w.check(r, False, False, t))

    def test_on_a_power_bank_only_when_the_pack_is_gone_and_the_battery_below_half(self):
        half = on_battery(3.70, 0).percent
        self.assertLess(half, config.POWER_BANK_OFF_PERCENT)   # the reading used below
        with unittest.mock.patch.object(config, "POWER_BANK", True):
            w = alerts.PowerOffWatch()
            key, _ = self.run_until(w, 3.87, True, False, 0, self.IDLE + 600)   # ~70%, asleep: no idle power-off
            self.assertIsNone(key)
            w = alerts.PowerOffWatch()
            for t in range(0, self.IDLE, 10):   # the pack is still going: USB
                self.assertIsNone(w.check(Reading(3.70, -300, t), True, False, t))
            w = alerts.PowerOffWatch()
            key, _ = self.run_until(w, 3.70, False, False, 0, 600)   # pack gone, below half, but awake
            self.assertIsNone(key)
            key, at = self.run_until(w, 3.70, True, True, 610, 700)  # ...then asleep: off, timers or not
            self.assertEqual((key, at), ("power_off_bank", 610))
            w = alerts.PowerOffWatch()
            key, _ = self.run_until(w, 3.40, False, True, 0, 100)   # nearly flat: off at once, as ever
            self.assertEqual(key, "power_off_battery")


class TimerTests(unittest.TestCase):
    def test_kept_in_a_file_till_rung(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "timers.json"
            ts = Timers(path)
            ts.add(5, "tea", now=0)
            ts.add(1, "", now=0)
            again = Timers(path)                      # the brain restarted
            self.assertEqual([(t.due, t.about) for t in again.pending()], [(60, ""), (300, "tea")])
            self.assertEqual([t.about for t in again.due(now=100)], [""])
            self.assertEqual([t.about for t in Timers(path).pending()], ["tea"])
            again.cancel("tea")
            self.assertFalse(path.exists(), "no file once none are left")
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(Timers(path).pending(), [])  # a damaged file: none, not a crash

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
                    "battery_saved", "hot", "very_hot", "power_off_idle", "power_off_idle_timers",
                    "power_off_battery"):
            personality.LINES[key].format(about="tea", percent=8, minutes=12, temp=72)


class RobotWakeWordTests(unittest.TestCase):
    """The robot's wake word never reaches the model (ears.strip_leading_word)."""

    def test_repeats_and_punctuation_go(self):
        s = lambda t: strip_leading_word(t, "computer")
        self.assertEqual(s("Computer computer computer"), "")   # Moonshine repeating a lone word
        self.assertEqual(s("Computer, how is your battery now?"), "how is your battery now?")
        self.assertEqual(s("Computer computer, what time is it?"), "what time is it?")
        self.assertEqual(s("Computer."), "")

    def test_the_word_in_a_sentence_stays(self):
        s = lambda t: strip_leading_word(t, "computer")
        self.assertEqual(s("computer games are fun"), "computer games are fun")
        self.assertEqual(s("And computer."), "And computer.")
        self.assertEqual(s("is my computer on"), "is my computer on")

    def test_loose_right_after_the_robot_heard_it(self):
        self.assertEqual(strip_leading_word("Computer how is it", "computer", loose=True), "how is it")


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
