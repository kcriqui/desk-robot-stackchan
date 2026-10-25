"""Settings changed from the live view's Settings tab, kept in server/data/settings.json (git-ignored).

From cgro00/desk-robot V2's settings.py (MIT), for this brain: each setting is a value in config.py (or the speaker
volume), so the rest of the code reads config as before. The file holds only what someone changed, applied over
config.py and server/.env at start and as soon as it's changed; "back to the default" takes a setting out again.
Unknown keys and bad values in the file are dropped. The robot keeps its own copy of the clock and screen part in
flash (the `clock` and `screen` messages), so they hold with the brain away.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config

PATH = Path(__file__).resolve().parent.parent / "data" / "settings.json"

# section: {key: (config attribute, kind, extra)}. kind: bool, int, float, str (extra = max length), choice (extra =
# the choices). int / float extra = (min, max). A None attribute is the brain's own (main.py).
SCHEMA: dict[str, dict[str, tuple]] = {
    "robot": {
        "human_name":    ("HUMAN_NAME", "str", 40),
        "awake_seconds": ("AWAKE_SECONDS", "float", (10.0, 600.0)),
        "volume":        (None, "float", (0.0, 1.0)),
        "body_language": ("BODY_LANGUAGE", "bool", None),
        "near_stop":     ("NEAR_STOP", "bool", None),        # a hand over the sensor stops it talking
        "near_level":    ("NEAR_LEVEL", "int", (1, 2047)),   # the proximity reading that counts as near
        "character":     ("CHARACTER", "str", 40),       # remembered when switched
        "face_pack":     ("FACE_PACK", "str", 48),       # for characters that don't choose one
    },
    "clock": {
        "h24":           ("CLOCK_24H", "bool", None),
        "timezone":      ("CLOCK_TZ", "str", 64),        # a zone like America/Los_Angeles; empty = the brain's
        "show_weather":  ("CLOCK_WEATHER", "bool", None),
        "weather_minutes": ("CLOCK_WEATHER_MINUTES", "int", (10, 180)),
    },
    "weather": {
        "location":      ("HOME_LOCATION", "str", 80),
        "units":         ("TEMP_UNIT", "choice", ("fahrenheit", "celsius")),
    },
    "screen": {
        "brightness":    ("SCREEN_BRIGHTNESS", "int", (0, 100)),        # 0 = the display's own default
        "clock_brightness": ("CLOCK_BRIGHTNESS", "int", (0, 100)),      # 0 = as bright as awake
        "night_mode":    ("NIGHT_MODE", "choice", ("off", "dim", "dark")),
        "dark_lux":      ("DARK_LUX", "int", (1, 1000)),     # the room is dark below this (the light sensor)
        "night_brightness": ("NIGHT_BRIGHTNESS", "int", (1, 100)),
        "idle_screen":   ("IDLE_SCREEN", "choice", ("clock", "pong", "tetris", "frogger", "roam", "life")),
    },
    "leds": {
        "brightness":    ("LED_BRIGHTNESS", "int", (0, 100)),
        "asleep":        ("LED_ASLEEP", "choice", ("off", "glow")),
    },
    "listening": {
        "VAD_THRESHOLD":    ("VAD_THRESHOLD", "float", (0.1, 0.95)),
        "TURN_THRESHOLD":   ("TURN_THRESHOLD", "float", (0.1, 0.95)),
        "TURN_MAX_SILENCE": ("TURN_MAX_SILENCE", "float", (0.5, 6.0)),
    },
    "speaker": {
        "TTS_LEVEL":        ("TTS_LEVEL", "float", (0.08, 0.40)),
        "TTS_HIGHPASS_HZ":  ("TTS_HIGHPASS_HZ", "float", (0.0, 600.0)),
        "TTS_PRESENCE_DB":  ("TTS_PRESENCE_DB", "float", (0.0, 12.0)),
    },
}
VOLUME_DEFAULT = 0.7  # the firmware's SPEAKER_VOLUME
# What config.py and server/.env say, before any saved setting: the defaults to go back to.
DEFAULTS = {sec: {k: (getattr(config, a) if a else VOLUME_DEFAULT) for k, (a, _, _) in keys.items()}
            for sec, keys in SCHEMA.items()}


def clean_value(section: str, key: str, value):
    """The value, checked. Raises ValueError with a message for the page."""
    if section not in SCHEMA or key not in SCHEMA[section]:
        raise ValueError(f"no setting {section}.{key}")
    _, kind, extra = SCHEMA[section][key]
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        return value
    if kind in ("int", "float"):
        if isinstance(value, bool):
            raise ValueError(f"{key} must be a number")
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number") from None
        lo, hi = extra
        if not lo <= v <= hi:
            raise ValueError(f"{key} must be between {lo:g} and {hi:g}")
        return int(round(v)) if kind == "int" else round(v, 3)
    if kind == "choice":
        if value not in extra:
            raise ValueError(f"{key} must be one of: {', '.join(extra)}")
        return value
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    value = value.strip()
    if len(value) > extra:
        raise ValueError(f"{key} must be at most {extra} characters")
    return value


def load() -> dict:
    """The saved settings, checked: {section: {key: value}}, only what was changed."""
    try:
        raw = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict = {}
    for sec, keys in (raw.items() if isinstance(raw, dict) else []):
        for k, v in (keys.items() if isinstance(keys, dict) else []):
            try:
                out.setdefault(sec, {})[k] = clean_value(sec, k, v)
            except ValueError:
                print(f"(settings.json: dropped {sec}.{k})")
    return out


def _save(saved: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
    tmp.replace(PATH)


def _apply(section: str, key: str, value) -> None:
    attr = SCHEMA[section][key][0]
    if attr is None:
        return
    if section == "robot" and key == "human_name" and value == "":
        value = DEFAULTS[section][key]  # empty = HUMAN_NAME from server/.env
    setattr(config, attr, value)


def apply_saved() -> dict:
    """At start: the saved settings over config.py. Returns them (main.py takes the volume)."""
    saved = load()
    for sec, keys in saved.items():
        for k, v in keys.items():
            _apply(sec, k, v)
    return saved


def change(section: str, key: str, value) -> object:
    """Set one (value None = back to the default) and save. Returns the value in effect."""
    saved = load()
    if value is None:
        saved.get(section, {}).pop(key, None)
        if section in saved and not saved[section]:
            del saved[section]
        value = DEFAULTS[section][key]
    else:
        value = clean_value(section, key, value)
        saved.setdefault(section, {})[key] = value
    _save(saved)
    _apply(section, key, value)
    return value


def current(volume: float) -> dict:
    """Every setting as it is now, its default, and whether it was changed, for the page."""
    saved = load()
    out = {}
    for sec, keys in SCHEMA.items():
        out[sec] = {}
        for k, (attr, kind, extra) in keys.items():
            v = getattr(config, attr) if attr else volume
            if sec == "robot" and k == "human_name":
                v = saved.get(sec, {}).get(k, "")  # the page shows what was typed: empty = .env's
            out[sec][k] = {"value": v, "default": DEFAULTS[sec][k], "changed": k in saved.get(sec, {}),
                           "kind": kind, "extra": list(extra) if isinstance(extra, tuple) else extra}
    return out

