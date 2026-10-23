"""Moonshine speech-to-text (config.STT_ENGINE = "moonshine").

Whisper always works through a 30-second window, so even "Rocky, what time
is it?" costs as much as a long sentence. Moonshine's work grows with the
audio, which makes short questions ~7-10x faster: on the NAS's i5-2500K a
2.6 s question took 185-281 ms (tiny/base) against 1.85 s for Whisper
base.en. It can't be told his name the way Whisper is (config.STT_PROMPT).

Adapted from useful-moonshine-onnx (github.com/moonshine-ai/moonshine, MIT,
Copyright (c) 2024 Useful Sensors) so it runs on the onnxruntime and
tokenizers the brain already has, without that package's audio-file
dependencies (librosa, scipy, ...). The model files download once into
DESK_ROBOT_CACHE, pinned to a revision and checked against a SHA-256 like
the turn-taking models (turn._fetch).
"""

from __future__ import annotations

import numpy as np

from . import config
from .turn import _fetch

REVISION = "48b4e427b587bcf67797a5be706d6ddc4a298149"  # moonshine-ai/moonshine
_BASE_URL = f"https://huggingface.co/moonshine-ai/moonshine/resolve/{REVISION}/onnx/merged"
FILES = {  # model -> {file: sha256}; float precision
    "tiny": {
        "encoder_model.onnx": "cbbf580f703b2af2137e0f6d14cd87f31cc67bd858bfd8715403a9489982d1a5",
        "decoder_model_merged.onnx": "4131cef00b62942e9cdef691101f2cc7dbbcd828d71eee8c6c46c28fd051d6cb",
    },
    "base": {
        "encoder_model.onnx": "153e128e7abd64a74ee47f2c3f585c3171c4d46cbb368b032827934c4e01e779",
        "decoder_model_merged.onnx": "58778763ca8438963190244d6b26572bdca2cedec56a4b91e828f3f2d69ef3c5",
    },
}
TOKENIZER_SHA256 = "ad3a2ceb0e84e4da57451d86fa337f8116dcff5f5d106434f8aa0b0de89718b9"  # base/float, used by both
_SHAPES = {"tiny": (6, 8, 36), "base": (8, 8, 52)}  # decoder layers, key/value heads, head size
_START, _END = 1, 2  # decoder start / end-of-text tokens
TOKENS_PER_SECOND = 6.5  # cap on output length, so silence can't make it ramble


class MoonshineTranscriber:
    """Same interface as ears.Transcriber: transcribe(16 kHz float32) -> text."""

    def __init__(self, model: str = config.MOONSHINE_MODEL) -> None:
        import onnxruntime as ort  # slow imports, keep them lazy
        import tokenizers

        if model not in FILES:
            raise ValueError(f"MOONSHINE_MODEL={model!r}: use one of {', '.join(FILES)}")
        self.layers, self.heads, self.head_dim = _SHAPES[model]
        paths = {name: _fetch(f"{_BASE_URL}/{model}/float/{name}", f"moonshine-{model}-{name}", sha)
                 for name, sha in FILES[model].items()}
        tok = _fetch(f"{_BASE_URL}/base/float/tokenizer.json", "moonshine-tokenizer.json", TOKENIZER_SHA256)
        so = ort.SessionOptions()
        so.log_severity_level = 3  # errors only
        if config.STT_THREADS:
            so.intra_op_num_threads = config.STT_THREADS
        cpu = ["CPUExecutionProvider"]
        self.encoder = ort.InferenceSession(str(paths["encoder_model.onnx"]), sess_options=so, providers=cpu)
        self.decoder = ort.InferenceSession(str(paths["decoder_model_merged.onnx"]), sess_options=so, providers=cpu)
        self.encoder_inputs = {i.name for i in self.encoder.get_inputs()}
        self.decoder_inputs = {i.name for i in self.decoder.get_inputs()}
        self.tokenizer = tokenizers.Tokenizer.from_file(str(tok))

    def transcribe(self, audio: np.ndarray) -> str:
        audio = np.asarray(audio, dtype=np.float32)[np.newaxis, :]
        if audio.shape[1] < 1600:  # under 0.1 s: nothing to say
            return ""
        mask = np.ones_like(audio, dtype=np.int64)
        enc = {"input_values": audio}
        if "attention_mask" in self.encoder_inputs:
            enc["attention_mask"] = mask
        hidden = self.encoder.run(None, enc)[0]

        empty = np.zeros((0, self.heads, 1, self.head_dim), dtype=np.float32)
        past = {f"past_key_values.{i}.{a}.{b}": empty
                for i in range(self.layers) for a in ("decoder", "encoder") for b in ("key", "value")}
        tokens: list[int] = []
        ids = [[_START]]
        for step in range(max(8, int(audio.shape[1] / 16000 * TOKENS_PER_SECOND))):
            dec = {"input_ids": ids, "encoder_hidden_states": hidden, "use_cache_branch": [step > 0], **past}
            if "encoder_attention_mask" in self.decoder_inputs:
                dec["encoder_attention_mask"] = mask
            logits, *present = self.decoder.run(None, dec)
            nxt = int(logits[0, -1].argmax())
            if nxt == _END:
                break
            tokens.append(nxt)
            ids = [[nxt]]
            # The encoder's keys/values are computed on the first step only.
            for k, v in zip(past, present):
                if step == 0 or "decoder" in k:
                    past[k] = v
        return collapse_repeats(self.tokenizer.decode(tokens, skip_special_tokens=True).strip())


def collapse_repeats(text: str) -> str:
    """Moonshine sometimes runs on, repeating the whole utterance: "go to sleep
    go to sleep" for "go to sleep" (seen 4 times in ~60 utterances; Whisper
    heard each once), sometimes stopping partway through a copy. A
    transcript that is one phrase of 2+ different words said 2+ times
    becomes one copy. One repeated word ("Rocky rocky rocky") is left alone,
    since people say that, unless it's 4+ copies or the robot's wake word: a
    lone "Computer." often comes out as "Computer computer (computer)"
    (Whisper heard each once), and nobody says that."""
    words = text.split()
    key = [w.strip(".,!?;:").lower() for w in words]
    if len(set(key)) == 1 and (len(words) >= 4 or (len(words) >= 2 and key[0] == config.ROBOT_WAKE_WORD)):
        return words[0].rstrip(",;:")  # "Computer computer ... computer" (x7, from one "Computer.")
    for size in range(2, len(words) // 2 + 1):
        # whole copies, then maybe a cut-off one: "go to sleep go to sleep go to"
        if (len(set(key[:size])) > 1
                and all(key[i] == key[i % size] for i in range(len(words)))):
            return " ".join(words[:size]).rstrip(",;:")
    return text
