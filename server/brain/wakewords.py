"""Which phrases wake the robot, and whose they are: every personality's own (Character.wake_words), which also
switch to that personality, plus generic ones (config.WAKE_WORDS, like "computer") that wake whoever is in use.

The transcript decides (match()), loosely when the keyword spotter (kws.py, off by default) heard the phrase,
since speech-to-text mangles names: "hey glad us" for "hey GLaDOS". A phrase must start what was said (after
at most two filler words: "okay so, hey Rocky"), so "my computer keeps crashing" and "I told Rocky" don't wake
it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from . import config

_FILLER = {"hey", "hi", "oh", "okay", "ok", "um", "uh", "so", "and", "well", "yo"}


@dataclass(frozen=True)
class Entry:
    phrase: str        # as clean() writes it
    owner: str | None  # the character it belongs to; None = generic, wakes whoever is in use


def clean(text: str) -> str:
    """Lowercase words of letters and apostrophes: how phrases are stored and compared."""
    return " ".join(re.sub(r"[^a-z' ]+", " ", text.lower()).split())


def generic() -> list[str]:
    return [p for p in map(clean, config.WAKE_WORDS.split(",")) if p]


def table(owned: list[tuple[str, list[str]]]) -> list[Entry]:
    """Every wake phrase and its owner, from (character id, its phrases) pairs. A phrase claimed twice stays with
    the first (generic ones first)."""
    out: dict[str, Entry] = {}
    for p in generic():
        out.setdefault(p, Entry(p, None))
    for owner, phrases in owned:
        for p in map(clean, phrases):
            if p:
                out.setdefault(p, Entry(p, owner))
    return list(out.values())


def owner(entries: list[Entry], phrase: str) -> str | None:
    return next((e.owner for e in entries if e.phrase == phrase), None)


def _words(text: str) -> list[re.Match]:
    return list(re.finditer(r"[a-z']+", text.lower()))


def match(text: str, phrases: list[str], loose: bool = False) -> tuple[str, str] | None:
    """The wake phrase that starts `text` and what follows it: ("hey glados", "what time is it?"), or None.
    It's compared letter by letter, spaces ignored ("rockychan" is "rocky chan"); loose (the spotter already
    heard it) accepts a rougher likeness, for names speech-to-text gets wrong."""
    words = _words(text)
    need = 0.6 if loose else 0.82
    best: tuple[float, str, int] | None = None
    for start in (0, 1, 2):
        if start and (len(words) < start or words[start - 1].group() not in _FILLER):
            break
        for p in phrases:
            squashed = p.replace(" ", "")
            n = len(p.split())
            for k in {max(1, n - 1), n, n + 1}:
                window = words[start:start + k]
                if len(window) < k:
                    continue
                heard = "".join(w.group() for w in window)
                # It starts like the phrase and is about as long: "my computer" isn't "computer".
                if heard[0] != squashed[0] or not 0.7 <= len(heard) / len(squashed) <= 1.4:
                    continue
                r = SequenceMatcher(None, squashed, heard).ratio()
                if r >= need and (best is None or (r, start + k) > best[0:3:2]):  # ties: the longer match
                    best = (r, p, start + k)
    if best is None:
        return None
    _, phrase, used = best
    return phrase, text[words[used - 1].end():].lstrip(" ,.!?:;-").strip()
