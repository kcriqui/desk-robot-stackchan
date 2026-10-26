"""Wake words, heard by the brain: sherpa-onnx's open-vocabulary keyword spotter (a 3.3M-parameter streaming
zipformer, trained on GigaSpeech) on the robot's audio, so any phrase written as text can wake it, and each
personality can have its own. About 4% of one NAS core; a hit comes ~0.3 s after the phrase ends.

The model spells phrases with sub-word pieces (its bpe.model), not by pronunciation, so a phrase should be
written the way it sounds: "hey glad us" finds GLaDOS where "hey glados" doesn't. Every phrase gets a boost
(config.KWS_BOOST) and a lower trigger threshold (config.KWS_THRESHOLD): with the model's defaults, half of the
synthetic "hey Juniper"s were missed.

The model lives in config.KWS_MODEL_DIR (downloaded once from k2-fsa/sherpa-onnx's kws-models release). No model
or no sherpa_onnx package: available() is False and the brain wakes on the transcript alone, as before.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

import numpy as np

from . import config

_FILES = ("tokens.txt", "bpe.model", "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
          "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx", "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx")


def clean(phrase: str) -> str:
    """A phrase as the spotter takes it: lowercase words of letters and apostrophes."""
    return " ".join(re.sub(r"[^a-z' ]+", " ", phrase.lower()).split())


class Spotter:
    """Feed 16 kHz float32 audio from one thread; set the phrases from any. feed() returns the phrases heard."""

    def __init__(self, model_dir: str | Path = config.KWS_MODEL_DIR) -> None:
        import sentencepiece
        import sherpa_onnx

        d = Path(model_dir)
        missing = [f for f in _FILES if not (d / f).exists()]
        if missing:
            raise FileNotFoundError(f"{d}: missing {', '.join(missing)}")
        self._sp = sentencepiece.SentencePieceProcessor(model_file=str(d / "bpe.model"))
        self._kws = sherpa_onnx.KeywordSpotter(
            tokens=str(d / _FILES[0]), encoder=str(d / _FILES[2]), decoder=str(d / _FILES[3]),
            joiner=str(d / _FILES[4]), keywords_file=str(d / "keywords.txt"), num_threads=1)
        self._lock = threading.Lock()
        self._phrases: list[str] = []
        self._stream = None

    def tokens(self, phrase: str) -> str:
        """The model's pieces for a phrase ("hey rocky" -> "▁HE Y ▁RO CK Y")."""
        return " ".join(self._sp.encode(clean(phrase).upper(), out_type=str))

    def set_phrases(self, phrases: list[str]) -> None:
        """Listen for these from now on (a fresh stream, so anything half-heard is dropped)."""
        phrases = list(dict.fromkeys(p for p in map(clean, phrases) if p))
        spec = "/".join(f"{self.tokens(p)} :{config.KWS_BOOST:g} #{config.KWS_THRESHOLD:g} @{i}"
                        for i, p in enumerate(phrases))
        with self._lock:
            self._phrases = phrases
            self._stream = self._kws.create_stream(spec) if phrases else None

    @property
    def phrases(self) -> list[str]:
        return list(self._phrases)

    def reset(self) -> None:
        """Forget what it has heard so far (after a gap in the audio)."""
        with self._lock:
            if self._stream is not None:
                self._kws.reset_stream(self._stream)

    def feed(self, block: np.ndarray) -> list[str]:
        hits = []
        with self._lock:
            s = self._stream
            if s is None:
                return hits
            s.accept_waveform(16000, np.asarray(block, dtype=np.float32))
            while self._kws.is_ready(s):
                self._kws.decode_stream(s)
                tag = self._kws.get_result(s)
                if tag:
                    self._kws.reset_stream(s)  # needed after a hit, or it stops spotting
                    if tag.isdigit() and int(tag) < len(self._phrases):
                        hits.append(self._phrases[int(tag)])
        return hits


def available() -> bool:
    """The package and the model are both there."""
    try:
        import sherpa_onnx  # noqa: F401
        import sentencepiece  # noqa: F401
    except ImportError:
        return False
    return all((Path(config.KWS_MODEL_DIR) / f).exists() for f in _FILES)
