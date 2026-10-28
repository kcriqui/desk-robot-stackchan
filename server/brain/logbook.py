"""The brain's output, timestamped: on screen, in the live view's Log tab, and in a file a day (adapted from
cgro00/desk-robot V2's launcher, MIT).

install() tees everything printed (stdout and stderr): each line starts with the time it began (HH:MM:SS) on the
terminal (or `docker logs`) as before, goes into a ring of the last LINES lines, numbered, so the page can ask for
what's new since the last line it has, and is appended to server/data/logs/brain-YYYY-MM-DD.log, the last
config.LOG_DAYS days kept, so a night can be looked back on after the brain has restarted.
"""

from __future__ import annotations

import collections
import os
import sys
import threading
import time

LINES = 3000
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "logs")

_lock = threading.Lock()
_lines: collections.deque[tuple[int, str]] = collections.deque(maxlen=LINES)
_seq = 0
_dir: str | None = None  # install() sets it: where the day files go (None: no files, as in the tests)
_days = 14
_file = None
_file_day = ""
RETRY_S = 10.0  # after a failed write, the wait before opening the file again
_retry_at = 0.0


def stamp(at: float | None = None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(at))


def add(text: str) -> None:
    """Whole lines, already stamped, into the ring and today's file."""
    global _seq
    with _lock:
        for line in text.splitlines() or [""]:
            _seq += 1
            _lines.append((_seq, line.rstrip()))
            _to_file(line.rstrip())


def _to_file(line: str) -> None:
    """Append a line to today's file (under _lock). A failed write drops the file and tries a fresh one RETRY_S
    later, quietly (printing the error would only come back here): on the PC the logs are on the NAS share, whose
    open file goes stale when the PC sleeps, and giving up for good lost a whole night (2026-10-27)."""
    global _file, _file_day, _retry_at
    if _dir is None or time.monotonic() < _retry_at:
        return
    day = time.strftime("%Y-%m-%d")
    try:
        if day != _file_day:
            if _file is not None:
                _file.close()
            os.makedirs(_dir, exist_ok=True)
            _file = open(os.path.join(_dir, f"brain-{day}.log"), "a", encoding="utf-8", errors="replace")
            _file_day = day
            for old in sorted(f for f in os.listdir(_dir) if f.startswith("brain-") and f.endswith(".log"))[:-_days]:
                os.remove(os.path.join(_dir, old))
            _file.write(f"{stamp()} ---- {day} ----\n")
        _file.write(line + "\n")
        _file.flush()
    except OSError:
        try:
            if _file is not None:
                _file.close()
        except OSError:
            pass
        _file, _file_day, _retry_at = None, "", time.monotonic() + RETRY_S


def since(after: int) -> tuple[int, list[str]]:
    """(the newest line's number, the lines after `after`). A page that's ahead (the brain restarted) gets them all."""
    with _lock:
        if after > _seq:
            after = 0
        return _seq, [t for s, t in _lines if s > after]


class _Tee:
    """A stream that stamps each line with the time it began and also keeps whole lines in the log. Text without
    a newline yet still goes to the screen at once."""

    def __init__(self, stream) -> None:
        self._stream = stream
        self._part = ""
        self._plock = threading.Lock()

    def write(self, s: str) -> int:
        with self._plock:
            out = []
            for piece in s.splitlines(keepends=True):
                if not self._part:
                    self._part = stamp() + " "
                    out.append(self._part)
                out.append(piece)
                if piece.endswith("\n"):
                    add(self._part + piece[:-1])
                    self._part = ""
                else:
                    self._part += piece
            self._stream.write("".join(out))
        return len(s)

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name):  # isatty, fileno, encoding... from the real stream
        return getattr(self._stream, name)


def install(log_dir: str | None = LOG_DIR, days: int = 14) -> None:
    global _dir, _days
    _dir, _days = log_dir, max(1, days)
    if not isinstance(sys.stdout, _Tee):
        sys.stdout = _Tee(sys.stdout)
    if not isinstance(sys.stderr, _Tee):
        sys.stderr = _Tee(sys.stderr)
