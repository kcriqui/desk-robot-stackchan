"""Turning the StackChan's battery readings into what Rocky says about them."""

import unittest

from brain import battery


class BatteryTests(unittest.TestCase):
    def test_percent_follows_the_curve(self):
        self.assertEqual(battery.percent(3.0), 0)
        self.assertEqual(battery.percent(3.80), 50)
        self.assertEqual(battery.percent(4.3), 100)
        self.assertTrue(battery.percent(3.7) < battery.percent(3.9) < battery.percent(4.1))

    def test_on_battery(self):
        r = battery.Reading(3.80, 400, 0.0)
        self.assertTrue(r.on_battery)
        self.assertFalse(r.charging)
        self.assertEqual(r.minutes_left(), round(550 * 0.5 / 400 * 60))
        self.assertIn("about 50%", r.describe(now=5.0))
        self.assertEqual(r.short(), "50% · 3.80 V")

    def test_charging_and_usb_power(self):
        charging = battery.Reading(4.02, -350, 0.0)
        self.assertTrue(charging.charging)
        self.assertIsNone(charging.minutes_left())
        self.assertIn("Charging", charging.describe(now=1.0))
        usb = battery.Reading(4.15, 5, 0.0)
        self.assertFalse(usb.on_battery or usb.charging)
        self.assertIn("USB power", usb.describe(now=1.0))

    def test_low_only_on_battery(self):
        self.assertTrue(battery.Reading(3.55, 300, 0.0).low())
        self.assertFalse(battery.Reading(3.55, -300, 0.0).low())  # charging
        self.assertFalse(battery.Reading(3.95, 300, 0.0).low())

    def test_old_readings_say_so(self):
        self.assertIn("s old", battery.Reading(3.9, 300, 0.0).describe(now=120.0))


if __name__ == "__main__":
    unittest.main()
