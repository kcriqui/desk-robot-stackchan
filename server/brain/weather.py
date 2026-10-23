"""The weather (the check_weather tool), live from Open-Meteo: free, no key
or account, well under a second. Web search alone tends to find a reading
hours old. Only the place name and its coordinates leave the house.

Places are found with Open-Meteo's geocoder. "Portland, OR" or "Paris,
France" picks the match whose state or country fits the part after the
comma. HOME_LOCATION (server/.env) is the place used when none is named,
and it also tells web search where home is (web.py).
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

import certifi

from . import config

_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes, as Open-Meteo reports them.
_WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}

# US state abbreviations, so "Portland, OR" finds Oregon, not Maine.
_STATES = dict(zip(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
    "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split(),
    ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
     "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
     "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
     "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico",
     "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
     "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont",
     "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming", "District of Columbia"]))


@dataclass
class Place:
    name: str          # "Portland"
    region: str        # "Oregon" ("" if none)
    country: str       # "United States"
    country_code: str  # "US"
    timezone: str      # "America/Los_Angeles"
    lat: float
    lon: float

    def __str__(self) -> str:
        return ", ".join(p for p in (self.name, self.region or self.country) if p)


def _get(url: str, params: dict) -> dict:
    ctx = ssl.create_default_context(cafile=certifi.where())  # see mouth.py: Windows lacks some roots
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                 headers={"User-Agent": "desk-robot"})
    with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
        return json.load(resp)


_places: dict[str, Place | None] = {}


def find_place(text: str) -> Place | None:
    """The best match for a place name, or None. Remembered per name."""
    key = " ".join(text.lower().split())
    if key in _places:
        return _places[key]
    name, _, hint = (p.strip() for p in text.partition(","))
    hint = _STATES.get(hint.upper(), hint).lower()
    results = _get(_GEOCODE, {"name": name, "count": 10, "language": "en", "format": "json"}).get("results") or []
    if hint:  # "Portland, OR": prefer the match in that state or country
        fitting = [r for r in results if hint in (str(r.get("admin1", "")).lower(),
                                                    str(r.get("country", "")).lower(),
                                                    str(r.get("country_code", "")).lower())]
        results = fitting or results
    place = None
    if results:
        r = results[0]  # the geocoder sorts by population
        place = Place(r["name"], r.get("admin1", ""), r.get("country", ""), r.get("country_code", ""),
                      r.get("timezone", ""), float(r["latitude"]), float(r["longitude"]))
    _places[key] = place
    return place


def home() -> Place | None:
    """HOME_LOCATION, looked up once; None if it's unset or can't be found."""
    if not config.HOME_LOCATION:
        return None
    try:
        return find_place(config.HOME_LOCATION)
    except (OSError, ValueError, KeyError):  # offline, or an odd reply
        return None


def report(place_text: str = "") -> str:
    """The weather for a named place, or home: now, and the next three days,
    as text for the model to put in its own words."""
    place = find_place(place_text) if place_text.strip() else home()
    if place is None:
        return (f"No place called {place_text!r} found." if place_text.strip() else
                "No home location is set (HOME_LOCATION in server/.env), so ask where.")
    f = config.TEMP_UNIT
    wind = "mph" if f == "fahrenheit" else "km/h"
    data = _get(_FORECAST, {
        "latitude": place.lat, "longitude": place.lon, "timezone": "auto", "forecast_days": 3,
        "temperature_unit": f, "wind_speed_unit": "mph" if f == "fahrenheit" else "kmh",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    })
    c, d = data["current"], data["daily"]
    # "degrees", not "F": the voice would say "fifty-three eff".
    lines = [f"Degrees {f.capitalize()}. {place} (local time {c['time'][-5:]}): "
             f"{_WMO.get(c['weather_code'], 'unknown sky')}, {c['temperature_2m']:.0f} degrees "
             f"(feels like {c['apparent_temperature']:.0f}), "
             f"humidity {c['relative_humidity_2m']}%, wind {c['wind_speed_10m']:.0f} {wind}"
             + (f", {c['precipitation']} mm falling" if c.get("precipitation") else "") + "."]
    for i, day in enumerate(d["time"]):
        label = ("Today", "Tomorrow")[i] if i < 2 else time.strftime("%A", time.strptime(day, "%Y-%m-%d"))
        rain = d["precipitation_probability_max"][i]
        lines.append(f"{label}: {_WMO.get(d['weather_code'][i], 'unknown')}, high {d['temperature_2m_max'][i]:.0f}, "
                     f"low {d['temperature_2m_min'][i]:.0f}" + (f", {rain}% chance of rain" if rain is not None else "") + ".")
    return " ".join(lines)


def now_at_home() -> dict | None:
    """Now and today at home, for the robot's clock screen (clock.py): temp, hi, lo (whole degrees), the WMO code,
    day or night, the unit ("F" or "C") and the sky in words. None without a home."""
    place = home()
    if place is None:
        return None
    f = config.TEMP_UNIT
    data = _get(_FORECAST, {
        "latitude": place.lat, "longitude": place.lon, "timezone": "auto", "forecast_days": 1,
        "temperature_unit": f,
        "current": "temperature_2m,weather_code,is_day",
        "daily": "temperature_2m_max,temperature_2m_min",
    })
    c, d = data["current"], data["daily"]
    code = int(c["weather_code"])
    return {"temp": round(c["temperature_2m"]), "hi": round(d["temperature_2m_max"][0]),
            "lo": round(d["temperature_2m_min"][0]), "code": code, "day": bool(c.get("is_day", 1)),
            "unit": "F" if f == "fahrenheit" else "C", "sky": _WMO.get(code, "")}
