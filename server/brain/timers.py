"""Timers and reminders Rocky sets for his human (the set_timer and
cancel_timer tools). When one is due, main.py's watch loop has him say so.

Kept in a small file (config.TIMER_FILE) as well as in memory, so they
survive the brain restarting; each one leaves the file once it has rung.
A timer only rings while the robot is connected: one that came due while
the robot was off, or the brain was down, rings when it's back, as missed.

The model sets them from a worker thread and the watch loop reads them on
the event loop, hence the lock.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

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
    def __init__(self, path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._timers: list[Timer] = []
        self.path = path
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._timers = sorted((Timer(float(t["due"]), str(t.get("about") or ""))
                                   for t in data.get("timers", [])), key=lambda x: x.due)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            print(f"  (timer file unreadable ({e}); starting with none)")
            self._timers = []

    def _save(self) -> None:
        """Write the file (the lock is held), through a temporary file; remove it when there are none."""
        if self.path is None:
            return
        try:
            if not self._timers:
                self.path.unlink(missing_ok=True)
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"about": "Rocky's timers, so a restart doesn't lose them.",
                                       "timers": [asdict(t) for t in self._timers]}, indent=2) + "\n",
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"  (couldn't save the timers: {e})")

    def add(self, minutes: float, about: str = "", now: float | None = None) -> Timer:
        if not 0 < minutes <= MAX_MINUTES:
            raise ValueError(f"minutes must be more than 0 and at most {MAX_MINUTES}")
        t = Timer((now if now is not None else time.time()) + minutes * 60, about.strip()[:80])
        with self._lock:
            if len(self._timers) >= MAX_TIMERS:
                raise ValueError(f"already {MAX_TIMERS} timers set")
            self._timers.append(t)
            self._timers.sort(key=lambda x: x.due)
            self._save()
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
            if gone:
                self._save()
        return gone

    def due(self, now: float | None = None) -> list[Timer]:
        """Take out and return the timers that have rung."""
        now = now if now is not None else time.time()
        with self._lock:
            ready = [t for t in self._timers if t.due <= now]
            if ready:
                self._timers = [t for t in self._timers if t.due > now]
                self._save()
        return ready

    def pending(self) -> list[Timer]:
        with self._lock:
            return list(self._timers)
