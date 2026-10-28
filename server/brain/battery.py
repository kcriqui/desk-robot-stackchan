"""The StackChan body's battery: one 550 mAh lithium-ion cell, measured by
the body's INA226. The robot sends a reading every ~10 s (docs/protocol.md):
voltage, and current in mA, positive while discharging, negative while
charging.

Charge is estimated from the voltage on this battery's own curve, measured
under the robot's load (2026-10-28: run to empty from 4.02 V to 3.02 V at
200-290 mA, 574 mAh counted from the current; .devtools/battery_chart.py).
It reads a little high while charging. The generic resting-cell curve it
replaces said 19% where half the charge was left: the cell holds a long flat
stretch at 3.6-3.7 V, then falls from 3.45 V to 3.0 V in about 10 minutes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

CAPACITY_MAH = 650  # rated 550; ~574 mAh measured from 4.02 V, ~650 from full
LOW_PERCENT = 15  # below this (on battery), he's told so with every question

# Voltage under the robot's load → charge left, measured (see above). 0% is where the robot powers itself off.
_CURVE = [(3.30, 0), (3.45, 4), (3.55, 10), (3.60, 17), (3.65, 30), (3.69, 42), (3.73, 50), (3.76, 60),
          (3.87, 70), (3.95, 80), (4.02, 88), (4.12, 100)]
_IDLE_MA = 20  # within ±this, the battery isn't really in use: USB powers the robot


def percent(volts: float) -> int:
    if volts <= _CURVE[0][0]:
        return 0
    for (v0, p0), (v1, p1) in zip(_CURVE, _CURVE[1:]):
        if volts <= v1:
            return round(p0 + (p1 - p0) * (volts - v0) / (v1 - v0))
    return 100


@dataclass
class Reading:
    volts: float
    milliamps: float
    at: float  # time.time() when it arrived

    @property
    def charging(self) -> bool:
        return self.milliamps < -_IDLE_MA

    @property
    def on_battery(self) -> bool:
        return self.milliamps > _IDLE_MA

    @property
    def percent(self) -> int:
        return percent(self.volts)

    def minutes_left(self) -> int | None:
        """At the current draw, from the estimated charge; None unless on battery."""
        if not self.on_battery:
            return None
        return round(CAPACITY_MAH * self.percent / 100 / self.milliamps * 60)

    def short(self) -> str:
        """For the console page: "62% · 3.85 V", "charging · 4.02 V"."""
        if self.charging:
            return f"charging · {self.volts:.2f} V"
        if not self.on_battery:
            return f"USB power · {self.volts:.2f} V"
        return f"{self.percent}% · {self.volts:.2f} V"

    def describe(self, now: float | None = None) -> str:
        """For the model, as check_battery's result."""
        age = (now or time.time()) - self.at
        stale = f" (this reading is {age:.0f} s old)" if age > 30 else ""
        if self.charging:
            return (f"Charging from USB: {self.volts:.2f} V, taking {-self.milliamps:.0f} mA. "
                    f"The charge level can't be read accurately while charging.{stale}")
        if not self.on_battery:
            return (f"Running on USB power; the battery isn't in use "
                    f"({self.volts:.2f} V, about {self.percent}% if that's its resting level).{stale}")
        return (f"Running on battery: about {self.percent}% ({self.volts:.2f} V), drawing "
                f"{self.milliamps:.0f} mA, so roughly {self.minutes_left()} minutes left at this rate. "
                f"The battery is small ({CAPACITY_MAH} mAh).{stale}")

    def low(self) -> bool:
        return self.on_battery and self.percent < LOW_PERCENT
