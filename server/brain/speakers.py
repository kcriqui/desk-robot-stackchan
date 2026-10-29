"""Who's talking: a voice fingerprint (a WeSpeaker ResNet34 speaker embedding, via sherpa-onnx) of every turn heard,
compared with the voices the robot has learned. It learns a voice by interviewing the person (the learn_voice
tool): their answers are collected until there's LEARN_VOICE_SECONDS of their speech, and the average fingerprint
is kept in config.SPEAKERS_FILE (git-ignored).

Scores are cosine similarities after taking away the average voice (speaker_background.json, from
.devtools/speaker_background.py): measured 2026-10-29 through the robot's mic, Kevin's turns score ~0.8 against his
learned voice (5% under 0.6, shorter turns lower), other voices ~0.2 (5% over 0.4). A short "yeah" says little, so
a turn too unsure on its own goes to whoever was talking a moment ago if it fits them.

Ears call hear() from their worker thread (before the text is passed on, ~45 ms for a 3 s turn); main.py asks
describe() for the situation it tells the model, and the tools run on another thread, hence the lock.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import config

MODEL_NAME = "wespeaker_en_voxceleb_resnet34.onnx"
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/" + MODEL_NAME)
MODEL_SHA256 = "5ef208a9da1453335308a6b6f4e6dfbd7e183a38b604de0a57664f45d257fe94"
RATE = 16000
MIN_SECONDS = 0.8      # shorter turns aren't fingerprinted
MAX_SECONDS = 6.0      # longer ones: the last 6 s (enough for a fingerprint, and the cost grows with length)
LEARN_MIN_TURNS = 5
LEARN_GIVE_UP = 300    # s without an answer: the interview is over
RECENT = 90            # s a speaker counts as "just talking"

_extractor = None
_background: np.ndarray | None = None
_model_lock = threading.Lock()


def available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        return False
    return True


def _model():
    global _extractor
    with _model_lock:
        if _extractor is None:
            import sherpa_onnx

            from .turn import _fetch

            path = _fetch(MODEL_URL, MODEL_NAME, MODEL_SHA256)
            _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(path), num_threads=2))
        return _extractor


def _average_voice() -> np.ndarray:
    global _background
    if _background is None:
        bg = json.loads(Path(__file__).with_name("speaker_background.json").read_text(encoding="utf-8"))
        _background = np.array(bg["mean"], dtype=np.float32)
    return _background


def raw_embedding(audio: np.ndarray) -> np.ndarray:
    """The model's own embedding of 16 kHz mono float audio."""
    ext = _model()
    st = ext.create_stream()
    st.accept_waveform(RATE, audio.astype(np.float32))
    st.input_finished()
    return np.array(ext.compute(st), dtype=np.float32)


def _unit(e: np.ndarray) -> np.ndarray:
    return e / (np.linalg.norm(e) + 1e-9)


def fingerprint(audio: np.ndarray) -> np.ndarray | None:
    """A turn's voice as a unit vector with the average voice taken away, or None for a turn too short to tell."""
    if len(audio) < MIN_SECONDS * RATE:
        return None
    e = _unit(raw_embedding(audio[-int(MAX_SECONDS * RATE):]))
    return _unit(e - _average_voice())


@dataclass
class Guess:
    name: str | None   # None: not a voice it knows (or can't tell)
    sure: bool         # with no name: sure it's no one it knows
    score: float
    seconds: float
    at: float


class Voices:
    """The learned voices, the latest guess, and an interview in progress."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or config.SPEAKERS_FILE)
        self._lock = threading.Lock()
        self._people: dict[str, dict] = {}
        self._mtime = None
        self.last: Guess | None = None
        self._learning: dict | None = None   # {name, prints: [(vector, seconds)], skipped, at}
        self._just_learned: str | None = None
        self._load()

    # ----- the learned voices -----

    def _load(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._people, self._mtime = {}, None
            return
        if mtime == self._mtime:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._people = {n: p for n, p in data.get("people", {}).items() if len(p.get("voice", [])) > 0}
        except (OSError, ValueError) as e:
            print(f"({self.path.name}: {e}; no voices known)")
            self._people = {}
        self._mtime = mtime

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"people": self._people}, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
        self._mtime = self.path.stat().st_mtime

    def people(self) -> list[str]:
        with self._lock:
            self._load()
            return sorted(self._people)

    def keep(self, name: str, prints: list[tuple[np.ndarray, float]], how: str) -> tuple[int, float]:
        """Learn a voice from fingerprints of its turns, (vector, seconds) each. Turns far from the rest (someone
        else answering) are dropped. Returns the turns and seconds used."""
        vecs = np.array([v for v, _ in prints])
        secs = np.array([s for _, s in prints])
        voice = _unit((vecs * secs[:, None]).sum(0))
        fits = vecs @ voice >= config.VOICE_MAYBE - 0.1
        if fits.sum() >= max(2, len(prints) // 2):
            vecs, secs = vecs[fits], secs[fits]
            voice = _unit((vecs * secs[:, None]).sum(0))
        with self._lock:
            self._load()
            self._people[name] = {"voice": [round(float(x), 5) for x in voice], "seconds": round(float(secs.sum()), 1),
                                  "turns": int(len(vecs)), "learned": dt.date.today().isoformat(), "how": how}
            self._save()
        return int(len(vecs)), float(secs.sum())

    def forget(self, name: str) -> str | None:
        with self._lock:
            self._load()
            match = next((n for n in self._people if n.lower() == name.strip().lower()), None)
            if match:
                del self._people[match]
                self._save()
            return match

    def scores(self, vec: np.ndarray) -> dict[str, float]:
        with self._lock:
            self._load()
            return {n: float(vec @ np.array(p["voice"], dtype=np.float32)) for n, p in self._people.items()}

    # ----- every turn -----

    def hear(self, audio: np.ndarray) -> Guess | None:
        """Fingerprint a turn: collect it for an interview in progress, or guess whose it is."""
        try:
            vec = fingerprint(audio)
        except Exception as e:  # never take the ears down with it
            print(f"(voices: {e})")
            return None
        if vec is None:
            return None
        seconds = min(len(audio) / RATE, MAX_SECONDS)
        now = time.time()
        guess = self._guess(vec, seconds, now)
        with self._lock:
            self.last = guess
            ln = self._learning
        if ln is not None and now - ln["at"] < LEARN_GIVE_UP:
            self._collect(ln, vec, seconds, guess, now)
        print(f"  (voice: {guess.name or 'unknown'}{'' if guess.sure else '?'} {guess.score:.2f})")
        return guess

    def _guess(self, vec: np.ndarray, seconds: float, now: float) -> Guess:
        ranked = sorted(self.scores(vec).items(), key=lambda kv: -kv[1])
        if not ranked:
            return Guess(None, False, 0.0, seconds, now)
        name, best = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else -1.0
        prev = self.last if self.last is not None and now - self.last.at < RECENT else None
        clear = best - second >= config.VOICE_MARGIN
        if best >= config.VOICE_SURE and clear:
            return Guess(name, True, best, seconds, now)
        if best >= config.VOICE_MAYBE and clear:
            return Guess(name, prev is not None and prev.name == name, best, seconds, now)
        if prev is not None and prev.name:  # too little to tell: still the one talking, if it fits them at all
            fit = dict(ranked)[prev.name]
            if fit >= config.VOICE_MAYBE - 0.1:
                return Guess(prev.name, False, fit, seconds, now)
        return Guess(None, seconds >= 1.5 and best < config.VOICE_MAYBE - 0.1, best, seconds, now)

    # ----- interviews -----

    def start_learning(self, name: str) -> None:
        with self._lock:
            self._learning = {"name": name, "prints": [], "skipped": None, "at": time.time()}
            self._just_learned = None

    def stop_learning(self) -> str | None:
        with self._lock:
            ln, self._learning = self._learning, None
        return ln["name"] if ln else None

    def _collect(self, ln: dict, vec: np.ndarray, seconds: float, guess: Guess, now: float) -> None:
        if guess.name and guess.name.lower() != ln["name"].lower() and guess.sure:
            ln["skipped"] = guess.name  # someone it knows answered instead
            return
        ln["prints"].append((vec, seconds))
        ln["skipped"], ln["at"] = None, now
        got = sum(s for _, s in ln["prints"])
        if got >= config.LEARN_VOICE_SECONDS and len(ln["prints"]) >= LEARN_MIN_TURNS:
            turns, secs = self.keep(ln["name"], ln["prints"], "interview")
            print(f"  (voice learned: {ln['name']}, {secs:.0f} s from {turns} turns)")
            with self._lock:
                self._learning, self._just_learned = None, ln["name"]

    def learning(self) -> tuple[str, float] | None:
        """The interview in progress: whose voice and the seconds of it so far."""
        with self._lock:
            ln = self._learning
            if ln is None:
                return None
            if time.time() - ln["at"] >= LEARN_GIVE_UP:
                self._learning = None
                return None
            return ln["name"], sum(s for _, s in ln["prints"])

    # ----- what the model is told -----

    def describe(self) -> str:
        """A sentence or two for the situation: whose voice the latest turn was, or how an interview is going."""
        with self._lock:
            done, self._just_learned = self._just_learned, None
            ln = self._learning
            skipped = ln["skipped"] if ln else None
        if done:
            return (f" You have just finished learning {done}'s voice and will recognise it from now on: tell "
                    f"{done} so, warmly, and end the interview.")
        now_learning = self.learning()
        if now_learning:
            name, got = now_learning
            text = (f" You are interviewing {name} to learn their voice: {got:.0f} of {config.LEARN_VOICE_SECONDS:.0f} "
                    f"seconds of their speech so far. Ask {name} one friendly, open question at a time that takes a "
                    "few sentences to answer (a story, a favourite place, how their day went), react briefly, and "
                    "keep your own turns short. You'll be told when you have enough.")
            if skipped:
                text += f" That last answer sounded like {skipped}, not {name}: it didn't count; ask {name} to answer."
            return text
        known = self.people()
        g = self.last
        if not known or g is None or time.time() - g.at > 30:
            return ""
        if g.name:
            return (f" Voice: this sounds like {g.name}." if g.sure else
                    f" Voice: probably {g.name}, not certain.")
        if g.sure:
            return (f" Voice: not a voice you know (you know {', '.join(known)}). If it's someone new, you may ask "
                    "who they are, once; with their OK you can learn their voice (learn_voice).")
        return ""
