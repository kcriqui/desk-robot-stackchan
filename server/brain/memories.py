"""Rocky's long-term memory: facts about his human he keeps between
conversations and brain restarts (the remember and forget tools, the console
and the live view). The conversation itself is still forgotten on restart;
these facts are not.

They live in a JSON file (config.MEMORY_FILE, git-ignored), and every model
call gets them in the system prompt. A fact may carry an end date, for things
that stop being true ("has a cold"); it's dropped once that day has passed.
Forgotten facts are kept at the end of the file, so a misheard "forget
everything" can be undone by hand. Hand edits are picked up while running.

Each fact says who it's about (a person's name, or "" for everyone: the
household, the robot itself) and which personality learned it. All
personalities share them (it's one robot), except a fact marked `only`,
which belongs to the personality that learned it (a running joke, an
in-character promise). Facts from before these fields are about the owner.

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
    about: str = ""   # whose fact: a person's name; "" = everyone
    by: str = ""      # the personality (character id) that learned it, or it's kept to; "" = added by hand
    only: bool = False  # only `by` knows it

    def seen_by(self, char_id: str) -> bool:
        return not (self.only and self.by) or self.by == char_id


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _clean(text: str) -> str:
    """A fact's words, tidied and checked. Raises ValueError for one he shouldn't keep."""
    # The model sometimes copies the "(learned ...)" note from its prompt into the fact.
    text = _DATE_NOTE.sub("", " ".join(str(text).split()))
    if not text:
        raise ValueError("nothing to remember")
    if len(text) > MAX_CHARS:
        raise ValueError(f"too long: keep it under {MAX_CHARS} characters")
    if _SECRET.search(text):
        raise ValueError("passwords, PINs and account or card numbers are never kept")
    return text


def _name(about: str) -> str:
    """Whom a fact is about, as a name; "" (everyone) for "everyone", "all" and the like."""
    about = " ".join(str(about).split())[:40]
    return "" if about.lower() in ("", "everyone", "everybody", "all", "household", "general", "anyone") else about


def _matches(about: str, text: str) -> bool:
    """Every meaningful word of `about` starts some word of `text`, give or
    take an ending ("cat names" matches "cats are named")."""
    words = [w for w in about.split() if w not in _STOP]
    have = text.split()
    return bool(words) and all(any(h.startswith(w[:4] if len(w) > 3 else w) for h in have) for w in words)


class Memories:
    def __init__(self, path: Path | str, owner: str = "") -> None:
        self.path = Path(path)
        self.owner = owner  # whom facts are about when nobody says (and the old facts)
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

    def people(self) -> list[str]:
        """Everyone some fact is about, the owner first."""
        names = {f.about for f in self.facts() if f.about} | ({self.owner} if self.owner else set())
        return sorted(names, key=lambda n: (n != self.owner, n.lower()))

    def prompt(self, today: dt.date | None = None, char_id: str = "") -> str:
        """The block added to the system prompt, the facts grouped by whom they're about; "" when the
        personality `char_id` knows nothing yet."""
        facts = [f for f in self.facts(today) if f.seen_by(char_id)]
        if not facts:
            return ""
        groups: dict[str, list[str]] = {}
        for f in facts:
            groups.setdefault(f.about, []).append(
                f"- {f.text} (learned {f.added}" + (f", true until {f.until})" if f.until else ")"))
        order = sorted(groups, key=lambda a: (a == "", a != self.owner, a.lower()))
        return ("What you remember from earlier conversations (your long-term memory; use it naturally, never "
                "recite the list). Facts about one person are about them, whoever is talking:\n"
                + "\n".join(f"About {a}:\n" + "\n".join(groups[a]) if a else
                            "About everyone (the household, you):\n" + "\n".join(groups[a]) for a in order))

    # ── changing ────────────────────────────────────────────────────────────

    def add(self, text: str, days: float | None = None, today: dt.date | None = None, about: str | None = None,
            by: str = "", only: bool = False) -> tuple[Fact, bool]:
        """Remember a fact, for good or for `days` days (today counts as the
        first), about a person (None: the owner; "": everyone), learned by the
        personality `by`. Returns (the fact, whether it's new): the same fact
        again only refreshes it. Raises ValueError for one he shouldn't keep."""
        text = _clean(text)
        today = today or dt.date.today()
        until = ""
        if days is not None:
            if not 0 < days <= MAX_DAYS:
                raise ValueError(f"days must be more than 0 and at most {MAX_DAYS}")
            until = (today + dt.timedelta(days=max(1, round(days)) - 1)).isoformat()
        about = self.owner if about is None else _name(about)
        key = _norm(text)
        with self._lock:
            self._reload_if_edited()
            for f in self._facts:
                if _norm(f.text) == key and f.about.lower() == about.lower():
                    f.added, f.until = today.isoformat(), until
                    self._save()
                    return f, False
            if len(self._facts) >= MAX_FACTS:
                raise ValueError(f"memory full ({MAX_FACTS} facts): forget something first")
            fact = Fact(max((f.id for f in self._facts), default=0) + 1, text, today.isoformat(), until,
                        about, by, bool(only and by))
            self._facts.append(fact)
            self._save()
            return fact, True

    def edit(self, fid: int, text: str | None = None, about: str | None = None, until: str | None = None,
             keep_to: str | None = None) -> Fact:
        """Change a fact (the live view): its words, whom it's about, its end date ("" = for good) or which
        personality alone knows it (keep_to: a character id; "" = all of them). Raises ValueError for a bad
        change or no such fact."""
        text = _clean(text) if text is not None else None
        if until:
            dt.date.fromisoformat(until)  # a ValueError for a bad date
        with self._lock:
            self._reload_if_edited()
            f = next((f for f in self._facts if f.id == fid), None)
            if f is None:
                raise ValueError(f"no memory #{fid}")
            if text is not None:
                f.text = text
            if about is not None:
                f.about = _name(about)
            if until is not None:
                f.until = until
            if keep_to is not None:
                f.only = bool(keep_to)
                if keep_to:
                    f.by = keep_to
            self._save()
            return f

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
            self._facts = [Fact(int(f["id"]), str(f["text"]), str(f["added"]), str(f.get("until") or ""),
                                str(f["about"]) if "about" in f else self.owner, str(f.get("by") or ""),
                                bool(f.get("only")))
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
            "about": ("The robot's long-term memory. Edit freely; the brain picks up changes. Per fact: about = "
                      "whose (\"\" = everyone), by = the personality that learned it, only = only that one knows."),
            "facts": [asdict(f) for f in self._facts],
            "forgotten": self._forgotten,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
        self._mtime = self.path.stat().st_mtime
