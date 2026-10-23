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


def posix_tz() -> str:
    if config.CLOCK_TZ:
        return config.CLOCK_TZ
    name = _zone_name()
    if name:
        for base in zoneinfo.TZPATH:
            path = os.path.join(base, *name.split("/"))
            try:
                with open(path, "rb") as f:
                    footer = f.read().rstrip(b"\n").rsplit(b"\n", 1)[-1].decode("ascii")
                if footer and not footer.startswith("TZif") and len(footer) <= 60:
                    return footer
            except (OSError, UnicodeDecodeError):
                continue
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
