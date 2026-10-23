"""Looking things up: the live weather (weather.py) and web search
(web.py). No network: both services are faked."""

import unittest
from types import SimpleNamespace as NS
from unittest import mock

from brain import config, personality, thinking, weather, web

PORTLANDS = {"results": [
    {"name": "Portland", "admin1": "Oregon", "country": "United States", "country_code": "US",
     "timezone": "America/Los_Angeles", "latitude": 45.52, "longitude": -122.68},
    {"name": "Portland", "admin1": "Maine", "country": "United States", "country_code": "US",
     "timezone": "America/New_York", "latitude": 43.66, "longitude": -70.26},
]}
FORECAST = {
    "current": {"time": "2026-10-22T17:45", "temperature_2m": 56.2, "apparent_temperature": 52.0,
                "relative_humidity_2m": 71, "weather_code": 2, "wind_speed_10m": 6.4, "precipitation": 0.0},
    "daily": {"time": ["2026-10-22", "2026-10-23", "2026-10-24"], "weather_code": [3, 61, 0],
              "temperature_2m_max": [62.1, 58.0, 60.4], "temperature_2m_min": [47.0, 43.2, 42.0],
              "precipitation_probability_max": [3, 80, None]},
}


def fake_get(url, params):
    if "geocoding" in url:
        return PORTLANDS if params["name"] == "Portland" else {}
    return FORECAST


class WeatherTests(unittest.TestCase):
    def setUp(self):
        weather._places.clear()
        p = mock.patch.object(weather, "_get", side_effect=fake_get)
        self.get = p.start()
        self.addCleanup(p.stop)

    def test_state_or_country_after_the_comma_picks_the_place(self):
        self.assertEqual(weather.find_place("Portland, ME").region, "Maine")
        self.assertEqual(weather.find_place("Portland, Oregon").region, "Oregon")
        self.assertEqual(weather.find_place("Portland").region, "Oregon")   # the biggest, first
        self.assertIsNone(weather.find_place("Xyzzyville"))

    def test_places_are_looked_up_once(self):
        weather.find_place("Portland, OR")
        weather.find_place("portland,  or")
        self.assertEqual(self.get.call_count, 1)

    def test_report_now_and_three_days(self):
        r = weather.report("Portland, OR")
        self.assertTrue(r.startswith("Degrees Fahrenheit. Portland, Oregon (local time 17:45): "
                                     "partly cloudy, 56 degrees (feels like 52)"))
        self.assertIn("Today: overcast, high 62, low 47, 3% chance of rain.", r)
        self.assertIn("Tomorrow: light rain, high 58, low 43, 80% chance of rain.", r)
        self.assertIn("Saturday: clear, high 60, low 42.", r)   # no rain figure: none given

    def test_home_is_the_default_place(self):
        with mock.patch.object(config, "HOME_LOCATION", ""):
            self.assertIn("No home location is set", weather.report(""))
        with mock.patch.object(config, "HOME_LOCATION", "Portland, ME"):
            self.assertIn("Portland, Maine", weather.report(""))
        self.assertIn("No place called 'Xyzzyville' found", weather.report("Xyzzyville"))

    def test_offline_home_is_just_unknown(self):
        self.get.side_effect = OSError("no network")
        with mock.patch.object(config, "HOME_LOCATION", "Portland, OR"):
            self.assertIsNone(weather.home())


def response(stop, *texts, searches=1):
    return NS(stop_reason=stop, content=[NS(type="text", text=t) for t in texts],
              usage=NS(input_tokens=100, server_tool_use=NS(web_search_requests=searches)))


class FakeMessages:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def create(self, **kw):
        self.calls.append(kw)
        return self.replies.pop(0)


def searcher(*replies):
    s = web.WebSearch.__new__(web.WebSearch)   # no real client
    s.client = NS(messages=FakeMessages(replies))
    return s


class WebSearchTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(weather, "home", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def test_answer_is_the_text_joined_up(self):
        s = searcher(response("end_turn", "Apple was at ", " $345.10 ", "as of 11 AM."))
        self.assertEqual(s.ask("AAPL price?"), "Apple was at $345.10 as of 11 AM.")
        call = s.client.messages.calls[0]
        self.assertEqual(call["tools"][0]["type"], "web_search_20250305")
        self.assertEqual(call["tools"][0]["max_uses"], config.SEARCH_MAX_USES)
        self.assertNotIn("user_location", call["tools"][0])

    def test_a_paused_search_is_continued(self):
        paused = response("pause_turn", "Looking")
        s = searcher(paused, response("end_turn", "Found it."))
        self.assertEqual(s.ask("q"), "Found it.")
        second = s.client.messages.calls[1]["messages"]
        self.assertEqual([m["role"] for m in second], ["user", "assistant"])
        self.assertIs(second[1]["content"], paused.content)   # sent back unchanged

    def test_nothing_useful(self):
        self.assertEqual(searcher(response("refusal")).ask("q"), "The search found nothing useful.")
        self.assertEqual(searcher(response("end_turn", "  ")).ask("q"), "The search found nothing useful.")

    def test_home_localizes_the_search(self):
        home = weather.Place("Portland", "Oregon", "United States", "US", "America/Los_Angeles", 45.5, -122.7)
        with mock.patch.object(weather, "home", return_value=home):
            s = searcher(response("end_turn", "ok"))
            s.ask("is the library open now?")
        call = s.client.messages.calls[0]
        self.assertEqual(call["tools"][0]["user_location"],
                         {"type": "approximate", "city": "Portland", "region": "Oregon",
                          "country": "US", "timezone": "America/Los_Angeles"})
        self.assertIn("lives in Portland, Oregon", call["system"])

    def test_only_offered_with_anthropic(self):
        with mock.patch.object(config, "LLM_BASE_URL", "https://openrouter.ai/api/v1"):
            self.assertFalse(web.available())
        with mock.patch.object(config, "WEB_SEARCH", False):
            self.assertFalse(web.available())


class ClockTests(unittest.TestCase):
    """What the robot's clock screen is sent (brain/clock.py)."""

    def test_time_zone_rule_from_the_zone_file(self):
        import os
        import tempfile
        from brain import clock
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "America"))
            with open(os.path.join(d, "America", "Los_Angeles"), "wb") as f:
                f.write(b"TZif2\x00binary...\nTZif2\x00more\nPST8PDT,M3.2.0,M11.1.0\n")
            with mock.patch.object(clock.zoneinfo, "TZPATH", (d,)), \
                    mock.patch.dict(os.environ, {"TZ": "America/Los_Angeles"}), \
                    mock.patch.object(config, "CLOCK_TZ", ""):
                self.assertEqual(clock.posix_tz(), "PST8PDT,M3.2.0,M11.1.0")
                with mock.patch.object(config, "CLOCK_TZ", "CET-1CEST,M3.5.0,M10.5.0/3"):
                    self.assertEqual(clock.posix_tz(), "CET-1CEST,M3.5.0,M10.5.0/3")   # the override wins

    def test_no_zone_database_falls_back_to_the_offset(self):
        import os
        from brain import clock
        with mock.patch.object(clock.zoneinfo, "TZPATH", ()), mock.patch.dict(os.environ, {"TZ": "America/X"}), \
                mock.patch.object(config, "CLOCK_TZ", ""):
            self.assertRegex(clock.posix_tz(), r"^LOC-?\d+(:\d\d)?$")

    def test_weather_message(self):
        from brain import clock
        place = weather.Place("Alviso", "California", "United States", "US", "America/Los_Angeles", 37.4, -122.0)
        reply = {"current": {"temperature_2m": 64.6, "weather_code": 61, "is_day": 0},
                 "daily": {"temperature_2m_max": [67.2], "temperature_2m_min": [53.5]}}
        with mock.patch.object(weather, "home", return_value=place), \
                mock.patch.object(weather, "_get", return_value=reply), \
                mock.patch.object(config, "TEMP_UNIT", "fahrenheit"):
            self.assertEqual(clock.weather_message(), {"type": "weather", "temp": 65, "hi": 67, "lo": 54, "code": 61,
                                                       "day": False, "unit": "F", "sky": "light rain"})
        with mock.patch.object(weather, "home", return_value=None):
            self.assertIsNone(clock.weather_message())   # no HOME_LOCATION: nothing to show


class WiringTests(unittest.TestCase):
    def test_tools_and_prompt(self):
        names = {t["function"]["name"] for t in thinking.TOOLS}
        self.assertLessEqual({"web_search", "check_weather"}, names)
        self.assertIn("`check_weather`", personality.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
