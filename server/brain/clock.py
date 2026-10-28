"""What the robot's clock screen needs from the brain (firmware-stackchan
clock_screen.h): the time zone as a POSIX rule, and the weather at home.

The robot gets the time itself (SNTP) but can't read time zone names, so it's
given the rule, e.g. "PST8PDT,M3.2.0,M11.1.0" for America/Los_Angeles. That is
the last line of the zone's file in the system's zone database (TZif v2+).
Where there's no database (Windows without the tzdata package), the current
offset stands in, without daylight saving; CLOCK_TZ in server/.env overrides
either way.
"""

from __future__ import annotations

import os
import re
import time
import zoneinfo

from . import config, weather


def _zone_name() -> str:
    """The IANA zone of this computer (TZ, as in the NAS's container), else of home."""
    tz = os.environ.get("TZ", "").strip()
    if tz and "/" in tz:
        return tz
    home = weather.home()
    return home.timezone if home is not None else ""


def is_zone_name(text: str) -> bool:
    """America/Los_Angeles, Etc/GMT+5: a zone database name, not a POSIX rule (CET-1CEST,M3.5.0,M10.5.0/3)."""
    return bool(re.fullmatch(r"[A-Za-z_]+(/[A-Za-z0-9_+-]+)+", text or ""))


def _zone_files(name: str):
    """The zone's TZif file contents: from the system's zone folders, then the tzdata package (Windows has no
    folders; without the package it has no rules at all)."""
    for base in zoneinfo.TZPATH:
        try:
            with open(os.path.join(base, *name.split("/")), "rb") as f:
                yield f.read()
        except OSError:
            continue
    try:
        from importlib import resources

        yield resources.files("tzdata.zoneinfo").joinpath(*name.split("/")).read_bytes()
    except (ImportError, OSError, ValueError):
        pass


def posix_tz() -> str:
    """The robot's time zone as a POSIX rule: CLOCK_TZ (a zone name like America/Los_Angeles, or a rule as it is),
    else this computer's."""
    if config.CLOCK_TZ and not is_zone_name(config.CLOCK_TZ):
        return config.CLOCK_TZ
    name = config.CLOCK_TZ or _zone_name()
    if name:
        for data in _zone_files(name):
            try:
                footer = data.rstrip(b"\n").rsplit(b"\n", 1)[-1].decode("ascii")
            except UnicodeDecodeError:
                continue
            if footer and not footer.startswith("TZif") and len(footer) <= 60:
                return footer
    # No zone database: the offset in effect now, with no daylight-saving rule.
    offset = time.altzone if time.localtime().tm_isdst > 0 else time.timezone  # seconds west of UTC
    hours, minutes = divmod(abs(offset) // 60, 60)
    sign = "-" if offset < 0 else ""
    return f"LOC{sign}{hours}" + (f":{minutes:02d}" if minutes else "")


def weather_message() -> dict | None:
    """The `weather` message for the robot: now and today at home. None without a home (or offline)."""
    now = weather.now_at_home()
    if now is None:
        return None
    return {"type": "weather", **now}
