"""Parakeet speech-to-text (config.STT_ENGINE = "parakeet"): NVIDIA's Parakeet TDT 0.6B v2, English, 8-bit, run on
the CPU by sherpa-onnx (already here for the wake words, kws.py).

On the user's 45 test clips (2026-10-25, .devtools/stt_bench.py) it had the fewest errors of five engines: 2.7%
word error rate against Whisper base.en's 5.4% and small.en's 4.0%, in 0.09 s a clip on the PC's Ryzen 9 7945HX
(Whisper base.en: 0.28 s; small.en on the RTX 3060: 0.10 s at 3.1%). It can't be told names the way Whisper is
(config.STT_PROMPT), and once heard "GLaDOS" as "GWADOS"; the wake word check is loose for that (wakewords.py).

The model files (sherpa-onnx's export, ~660 MB) download once into DESK_ROBOT_CACHE from Hugging Face, pinned to a
commit and checked against a SHA-256 (turn._fetch), the same files as k2-fsa's asr-models release.
"""

from __future__ import annotations

import numpy as np

from . import config
from .turn import _fetch

REVISION = "1ab9323565ddb038682214b292f588070a538ce2"  # csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8
_BASE_URL = f"https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8/resolve/{REVISION}"
FILES = {
    "encoder.int8.onnx": "a32b12d17bbbc309d0686fbbcc2987b5e9b8333a7da83fa6b089f0a2acd651ab",
    "decoder.int8.onnx": "b6bb64963457237b900e496ee9994b59294526439fbcc1fecf705b31a15c6b4e",
    "joiner.int8.onnx": "7946164367946e7f9f29a122407c3252b680dbae9a51343eb2488d057c3c43d2",
    "tokens.txt": "ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d",
}


class ParakeetTranscriber:
    """Same interface as ears.Transcriber: transcribe(16 kHz float32) -> text."""

    def __init__(self) -> None:
        import sherpa_onnx  # slow import, keep it lazy

        p = {name: str(_fetch(f"{_BASE_URL}/{name}", f"parakeet-tdt-0.6b-v2-{name}", sha))
             for name, sha in FILES.items()}
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=p["encoder.int8.onnx"], decoder=p["decoder.int8.onnx"], joiner=p["joiner.int8.onnx"],
            tokens=p["tokens.txt"], num_threads=config.STT_THREADS or 4, model_type="nemo_transducer")

    def transcribe(self, audio: np.ndarray) -> str:
        audio = np.asarray(audio, dtype=np.float32)
        if len(audio) < 1600:  # under 0.1 s: nothing to say
            return ""
        s = self.recognizer.create_stream()
        s.accept_waveform(16000, audio)
        self.recognizer.decode_stream(s)
        return s.result.text.strip()
