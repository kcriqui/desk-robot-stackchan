"""The brain's recent output, for the live view's Log tab (adapted from cgro00/desk-robot V2's launcher, MIT).

install() tees everything printed (stdout and stderr) into a ring of the last LINES lines, numbered, so the page can
ask for what's new since the last line it has. It's kept in memory only, never written to disk; the terminal (or
`docker logs`) still gets every word as before.
"""

from __future__ import annotations

import collections
import sys
import threading

LINES = 3000

_lock = threading.Lock()
_lines: collections.deque[tuple[int, str]] = collections.deque(maxlen=LINES)
_seq = 0


def add(text: str) -> None:
    global _seq
    with _lock:
        for line in text.splitlines() or [""]:
            _seq += 1
            _lines.append((_seq, line.rstrip()))


def since(after: int) -> tuple[int, list[str]]:
    """(the newest line's number, the lines after `after`). A page that's ahead (the brain restarted) gets them all."""
    with _lock:
        if after > _seq:
            after = 0
        return _seq, [t for s, t in _lines if s > after]


class _Tee:
    """A stream that also keeps whole lines in the log."""

    def __init__(self, stream) -> None:
        self._stream = stream
        self._part = ""
        self._plock = threading.Lock()

    def write(self, s: str) -> int:
        n = self._stream.write(s)
        with self._plock:
            self._part += s
            if "\n" in self._part:
                done, self._part = self._part.rsplit("\n", 1)
                add(done)
        return n

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name):  # isatty, fileno, encoding... from the real stream
        return getattr(self._stream, name)


def install() -> None:
    if not isinstance(sys.stdout, _Tee):
        sys.stdout = _Tee(sys.stdout)
    if not isinstance(sys.stderr, _Tee):
        sys.stderr = _Tee(sys.stderr)
