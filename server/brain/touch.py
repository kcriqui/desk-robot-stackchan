"""The touch strip on top of the robot's head (StackChan firmware 0.6.9.33+), from the robot's raw touches to
gestures: main.py's on_touch has the character answer them.

The robot sends a tap (with its zones and how hard, 1-3) when a short touch lifts, each stroke along the head
(a swipe, either way), and a hand resting there for 1 s and 3 s (a hold). Here:
  pat      one tap: a fist bump
  double   two taps close together: its attention, like the wake word (it wakes for it)
  bonk     a hard tap (TOUCH_BONK_LEVEL): indignation
  pet      one or two strokes
  scratch  several quick strokes: a bigger, happier answer
  lean     the first stroke, at once: it leans into the hand
  hold     a hand resting there: content, eyes half shut; tuck: still there after 3 s: goodnight, and asleep
  release  the hand lifted after a hold
A single tap waits TOUCH_DOUBLE_SECONDS in case a second follows, and strokes until none has come for
TOUCH_PET_SETTLE_SECONDS, so poll() hands those over a moment later.
"""

from __future__ import annotations

from . import config


class Touches:
    def __init__(self) -> None:
        self._tap_at: float | None = None  # a single tap, waiting to see if it's a double
        self._swipes: list[float] = []      # strokes so far, waiting for them to stop

    def feed(self, event: dict, now: float) -> list[str]:
        """A touch from the robot: the gestures it settles now."""
        g = event.get("g")
        if g == "tap":
            try:
                level = int(event.get("level") or 0)
            except (TypeError, ValueError):
                level = 0
            if level >= config.TOUCH_BONK_LEVEL:
                self._tap_at = None
                return ["bonk"]
            if self._tap_at is not None and now - self._tap_at <= config.TOUCH_DOUBLE_SECONDS:
                self._tap_at = None
                return ["double"]
            self._tap_at = now
            return []
        if g == "swipe":
            self._swipes.append(now)
            return ["lean"] if len(self._swipes) == 1 else []
        if g == "hold":
            return ["tuck"] if str(event.get("s")) == "3" else ["hold"]
        if g == "release":
            return ["release"]
        return []

    def poll(self, now: float) -> list[str]:
        """The gestures that have waited long enough to be sure (call a few times a second)."""
        out = []
        if self._tap_at is not None and now - self._tap_at > config.TOUCH_DOUBLE_SECONDS:
            self._tap_at = None
            out.append("pat")
        if self._swipes and now - self._swipes[-1] > config.TOUCH_PET_SETTLE_SECONDS:
            out.append("scratch" if len(self._swipes) >= config.TOUCH_SCRATCH_STROKES else "pet")
            self._swipes = []
        return out
