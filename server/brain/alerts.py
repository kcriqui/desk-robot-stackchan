"""When Rocky should speak up about his battery or his temperature.

Pure decisions, no I/O, so they can be tested: main.py's watch loop feeds in
each reading and says the line whose key comes back (personality.LINES).
"""

from __future__ import annotations

from collections import deque

from . import config
from .battery import Reading


class BatteryWatch:
    """Warns as the battery runs down: at each level of config.BATTERY_WARNINGS,
    more often the lower it gets. The level is judged on the median of the
    last three readings, since the voltage jumps with the load (talking,
    moving). Plugging in after a warning gets a thank-you."""

    def __init__(self) -> None:
        self.recent: deque[int] = deque(maxlen=3)
        self.level: int | None = None  # index of the last warning given
        self.last_at = 0.0
        self.seen_at: float | None = None  # the reading already looked at

    def check(self, r: Reading | None, now: float) -> str | None:
        if r is None or r.at == self.seen_at:
            return None
        self.seen_at = r.at
        if not r.on_battery:  # plugged in: charging, or USB powering him
            self.recent.clear()
            if self.level is not None:
                self.level = None
                return "battery_saved"
            return None
        self.recent.append(r.percent)
        if len(self.recent) < self.recent.maxlen:
            return None
        p = sorted(self.recent)[1]
        warnings = config.BATTERY_WARNINGS
        level = None
        for i, (below, _, _) in enumerate(warnings):
            if p < below:
                level = i  # the lowest level reached
        if level is None:
            if self.level is not None and p >= warnings[self.level][0] + 3:
                self.level = None  # charged up again somehow; warn afresh next time
            return None
        repeat = warnings[level][1]
        if (self.level is None or level > self.level
                or (level == self.level and repeat and now - self.last_at >= repeat * 60)):
            self.level, self.last_at = level, now
            return warnings[level][2]
        return None


class TempWatch:
    """Warns once when the chip passes config.TEMP_WARN_C, and again every
    10 minutes once it's 5 degrees hotter. Resets 5 degrees below the limit."""

    REPEAT_S = 600

    def __init__(self) -> None:
        self.level = 0  # 0 = fine, 1 = warned hot, 2 = warned very hot
        self.last_at = 0.0

    def check(self, celsius: float | None, now: float) -> str | None:
        if celsius is None:
            return None
        warn = config.TEMP_WARN_C
        if celsius < warn - 5:
            self.level = 0
            return None
        level = 2 if celsius >= warn + 5 else 1 if celsius >= warn else 0
        if level == 0:
            return None
        if level > self.level or (level == 2 and now - self.last_at >= self.REPEAT_S):
            self.level, self.last_at = level, now
            return "hot" if level == 1 else "very_hot"
        return None
