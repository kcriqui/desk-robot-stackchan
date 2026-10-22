"""Timers and reminders Rocky sets for his human (the set_timer and
cancel_timer tools). When one is due, main.py's watch loop has him say so.
Kept in memory only: restarting the brain forgets them.

The model sets them from a worker thread and the watch loop reads them on
the event loop, hence the lock.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

MAX_MINUTES = 24 * 60
MAX_TIMERS = 20


@dataclass
class Timer:
    due: float   # time.time() when it rings
    about: str   # what it's for ("" = just a timer)


def when(t: float, now: float | None = None) -> str:
    """'in 40 s (at 17:46)', 'in 12 min (at 17:58)', 'in 1 h 5 min (at 18:51)'."""
    left = max(0, round(t - (now if now is not None else time.time())))
    clock = time.strftime("%H:%M", time.localtime(t))
    if left < 60:
        return f"in {left} s (at {clock})"
    mins = round(left / 60)
    span = f"{mins} min" if mins < 60 else f"{mins // 60} h" + (f" {mins % 60} min" if mins % 60 else "")
    return f"in {span} (at {clock})"


class Timers:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._timers: list[Timer] = []

    def add(self, minutes: float, about: str = "", now: float | None = None) -> Timer:
        if not 0 < minutes <= MAX_MINUTES:
            raise ValueError(f"minutes must be more than 0 and at most {MAX_MINUTES}")
        t = Timer((now if now is not None else time.time()) + minutes * 60, about.strip()[:80])
        with self._lock:
            if len(self._timers) >= MAX_TIMERS:
                raise ValueError(f"already {MAX_TIMERS} timers set")
            self._timers.append(t)
            self._timers.sort(key=lambda x: x.due)
        return t

    def cancel(self, about: str) -> list[Timer]:
        """Cancel the timers whose label matches `about` (either contains the
        other, ignoring case), or all of them for "all". With no match and
        only one timer set, that one ("cancel the timer")."""
        a = about.strip().lower()
        with self._lock:
            if a in ("all", "every", "everything"):
                gone = self._timers
            else:
                gone = [t for t in self._timers if a and (a in t.about.lower() or (t.about and t.about.lower() in a))]
                if not gone and len(self._timers) == 1:
                    gone = self._timers
            self._timers = [t for t in self._timers if t not in gone]
        return gone

    def due(self, now: float | None = None) -> list[Timer]:
        """Take out and return the timers that have rung."""
        now = now if now is not None else time.time()
        with self._lock:
            ready = [t for t in self._timers if t.due <= now]
            self._timers = [t for t in self._timers if t.due > now]
        return ready

    def pending(self) -> list[Timer]:
        with self._lock:
            return list(self._timers)
