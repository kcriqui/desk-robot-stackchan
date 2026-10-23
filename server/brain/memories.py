"""Rocky's long-term memory: facts about his human he keeps between
conversations and brain restarts (the remember and forget tools, the console
and the live view). The conversation itself is still forgotten on restart;
these facts are not.

They live in a JSON file (config.MEMORY_FILE, git-ignored), and every model
call gets them in the system prompt. A fact may carry an end date, for things
that stop being true ("has a cold"); it's dropped once that day has passed.
Forgotten facts are kept at the end of the file, so a misheard "forget
everything" can be undone by hand. Hand edits are picked up while running.

The model's tools run on a worker thread, the console on the event loop and
the live view on the HTTP thread, hence the lock.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_FACTS = 100
MAX_CHARS = 200
MAX_DAYS = 365
KEEP_FORGOTTEN = 50

# Things he must not keep, however they're asked for.
_SECRET = re.compile(r"\b(password|passcode|passphrase|pin (code|number)|credit card|card number|"
                     r"social security|bank account|account number)\b", re.I)
_DATE_NOTE = re.compile(r"\s*\((learned|true until)\b[^)]*\)\s*$", re.I)
_STOP = {"a", "an", "the", "and", "or", "of", "to", "for", "with", "about", "that", "this", "is",
         "are", "was", "my", "his", "her", "their", "your", "me", "you", "i", "it", "fact", "facts"}


@dataclass
class Fact:
    id: int
    text: str
    added: str        # ISO date he learned it
    until: str = ""   # ISO date it stops being true; "" = for good


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _matches(about: str, text: str) -> bool:
    """Every meaningful word of `about` starts some word of `text`, give or
    take an ending ("cat names" matches "cats are named")."""
    words = [w for w in about.split() if w not in _STOP]
    have = text.split()
    return bool(words) and all(any(h.startswith(w[:4] if len(w) > 3 else w) for h in have) for w in words)


class Memories:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._facts: list[Fact] = []
        self._forgotten: list[dict] = []
        self._mtime: float | None = None  # the file's, when last read or written
        self._load()

    # ── reading ─────────────────────────────────────────────────────────────

    def facts(self, today: dt.date | None = None) -> list[Fact]:
        """The facts still true today, oldest first. Expired ones are
        dropped (and the file rewritten) as a side effect."""
        today = today or dt.date.today()
        with self._lock:
            self._reload_if_edited()
            live = [f for f in self._facts if not f.until or dt.date.fromisoformat(f.until) >= today]
            if len(live) != len(self._facts):
                self._forgotten.extend({**asdict(f), "forgotten": today.isoformat(), "why": "expired"}
                                       for f in self._facts if f not in live)
                self._facts = live
                self._save()
            return list(live)

    def prompt(self, today: dt.date | None = None) -> str:
        """The block added to the system prompt; "" when he knows nothing yet."""
        facts = self.facts(today)
        if not facts:
            return ""
        lines = [f"- {f.text} (learned {f.added}" + (f", true until {f.until})" if f.until else ")")
                 for f in facts]
        return ("What you remember from earlier conversations (your long-term memory; use it "
                "naturally, never recite the list):\n" + "\n".join(lines))

    # ── changing ────────────────────────────────────────────────────────────

    def add(self, text: str, days: float | None = None, today: dt.date | None = None) -> tuple[Fact, bool]:
        """Remember a fact, for good or for `days` days (today counts as the
        first). Returns (the fact, whether it's new): the same fact again
        only refreshes its dates. Raises ValueError for one he shouldn't keep."""
        # The model sometimes copies the "(learned ...)" note from its prompt into the fact.
        text = _DATE_NOTE.sub("", " ".join(str(text).split()))
        if not text:
            raise ValueError("nothing to remember")
        if len(text) > MAX_CHARS:
            raise ValueError(f"too long: keep it under {MAX_CHARS} characters")
        if _SECRET.search(text):
            raise ValueError("passwords, PINs and account or card numbers are never kept")
        today = today or dt.date.today()
        until = ""
        if days is not None:
            if not 0 < days <= MAX_DAYS:
                raise ValueError(f"days must be more than 0 and at most {MAX_DAYS}")
            until = (today + dt.timedelta(days=max(1, round(days)) - 1)).isoformat()
        key = _norm(text)
        with self._lock:
            self._reload_if_edited()
            for f in self._facts:
                if _norm(f.text) == key:
                    f.added, f.until = today.isoformat(), until
                    self._save()
                    return f, False
            if len(self._facts) >= MAX_FACTS:
                raise ValueError(f"memory full ({MAX_FACTS} facts): forget something first")
            fact = Fact(max((f.id for f in self._facts), default=0) + 1, text, today.isoformat(), until)
            self._facts.append(fact)
            self._save()
            return fact, True

    def forget(self, about: str, today: dt.date | None = None) -> list[Fact]:
        """Forget the facts matching `about`: "all", a fact's id, or words
        it contains. Returns what was forgotten."""
        a = _norm(about)
        with self._lock:
            self._reload_if_edited()
            if a in ("all", "everything", "every thing", "all of it"):
                gone = list(self._facts)
            elif a.isdigit():
                gone = [f for f in self._facts if f.id == int(a)]
            else:
                gone = [f for f in self._facts if _matches(a, _norm(f.text))]
            if gone:
                day = (today or dt.date.today()).isoformat()
                self._forgotten.extend({**asdict(f), "forgotten": day} for f in gone)
                self._facts = [f for f in self._facts if f not in gone]
                self._save()
            return gone

    # ── the file ────────────────────────────────────────────────────────────

    def _reload_if_edited(self) -> None:
        """Pick up the file if someone edited it by hand (the lock is held)."""
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if mtime != self._mtime:
            self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            self._mtime = self.path.stat().st_mtime
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._facts = [Fact(int(f["id"]), str(f["text"]), str(f["added"]), str(f.get("until") or ""))
                           for f in data.get("facts", [])]
            for f in self._facts:  # bad dates fail here, not later mid-reply
                dt.date.fromisoformat(f.added)
                if f.until:
                    dt.date.fromisoformat(f.until)
            self._forgotten = list(data.get("forgotten", []))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            # Keep the damaged file for a human to look at; start empty rather
            # than refuse to run.
            bad = self.path.with_name(self.path.name + ".bad")
            print(f"  (memory file unreadable ({e}); moved to {bad.name}, starting empty)")
            try:
                os.replace(self.path, bad)
            except OSError:
                pass
            self._facts, self._forgotten, self._mtime = [], [], None

    def _save(self) -> None:
        """Write the whole file (the lock is held). Through a temporary file,
        so a crash mid-write can't leave half a file."""
        self._forgotten = self._forgotten[-KEEP_FORGOTTEN:]
        data = {
            "about": "Rocky's long-term memory. Edit freely; the brain picks up changes.",
            "facts": [asdict(f) for f in self._facts],
            "forgotten": self._forgotten,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
        self._mtime = self.path.stat().st_mtime
