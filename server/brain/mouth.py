"""Mouth: turn the robot's words into sound.

Three voices (config.TTS_ENGINE picks; "auto" takes the first available):
  * Fish Audio (Rocky's real voice, config.TTS_VOICE_ID) when
    FISH_AUDIO_API_KEY is set. Plain HTTPS POST asking for raw 16 kHz PCM. The response body
    streams, so the first audio arrives ~0.3 s in, long before the sentence
    is finished, and is passed straight on to the speaker.
  * Piper (config.PIPER_VOICE, or the speaking character's), a free voice that runs on this computer, when
    it's installed (requirements-piper.txt; optional because it's GPL-3.0).
    A sentence takes well under 0.1 s, then Praat (parselmouth) shifts its
    pitch and resamples it to 16 kHz.
  * the computer's built-in voice as the fallback so the loop is audible
    before that's set up: `say` on macOS, Windows' speech engine through
    PowerShell, espeak-ng on Linux.

`stream(text)` yields 16 kHz mono s16le PCM chunks — the exact format the
robot's speaker expects (docs/protocol.md) — as they're produced.
`synthesize()` is the same joined into one buffer; `play()` plays a buffer
on this computer.
"""

from __future__ import annotations

import io
import json
import os
import re
import ssl
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import importlib.util
import urllib.request
import wave
from collections.abc import Iterator

import certifi

import numpy as np

from . import config

SAMPLE_RATE = 16_000
FISH_TTS_URL = "https://api.fish.audio/v1/tts"


def fish_available() -> bool:
    return bool(config.TTS_VOICE_ID and os.environ.get("FISH_AUDIO_API_KEY"))


def piper_available() -> bool:
    """Piper and parselmouth are installed (both optional, GPL-3.0)."""
    return all(importlib.util.find_spec(m) is not None for m in ("piper", "parselmouth"))


def engine() -> str:
    """The voice to try first: fish, piper or builtin (config.TTS_ENGINE)."""
    if config.TTS_ENGINE != "auto":
        return config.TTS_ENGINE
    if fish_available():
        return "fish"
    return "piper" if piper_available() else "builtin"


def prepare() -> str:
    """Load the voice ahead of the first reply (Piper takes a second or two,
    plus a one-time download) and say which one will speak."""
    e = engine()
    if e == "fish":
        return "voice: Fish Audio" if fish_available() else "voice: the computer's own (TTS_ENGINE=fish, but no FISH_AUDIO_API_KEY)"
    if e == "piper":
        if not piper_available():
            return "voice: the computer's own (TTS_ENGINE=piper, but Piper isn't installed: pip install -r requirements-piper.txt)"
        try:
            _synthesize_piper("Hello.")  # loads the voice and warms it up (the first sentence is ~0.5 s slower)
        except Exception as ex:
            return f"voice: the computer's own (Piper failed to load: {ex})"
        from . import personality
        return f"voice: Piper {personality.voice()[0]} ({personality.CHARACTER.name})"
    return "voice: the computer's own"


_MARKUP = re.compile(r"[*_`#~<>\[\]{}|\\]")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")


def clean_for_tts(text: str) -> str:
    """What the voice model gets: plain spoken words. Markdown marks, emoji
    and runs of punctuation tend to make generated speech do odd things."""
    text = _EMOJI.sub("", _MARKUP.sub("", text))
    for word, said in config.SAY_AS.items():         # names the voice would misread
        text = re.sub(rf"\b{re.escape(word)}\b", said, text, flags=re.IGNORECASE)
    text = re.sub(r"([!?.,])\1+", r"\1", text)      # "!!!" -> "!"
    text = re.sub(r"\.{2,}", ".", text)              # "..." -> "."
    text = re.sub(r"\s+", " ", text).strip()
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _fish_stream(text: str) -> Iterator[bytes]:
    """Raw 16 kHz PCM from Fish Audio, yielded as the server produces it."""
    body = json.dumps(
        {
            "text": text,
            "reference_id": config.TTS_VOICE_ID,
            "format": "pcm",          # headerless s16le at sample_rate: nothing to parse
            "sample_rate": SAMPLE_RATE,
            "latency": "balanced",    # a little faster to the first byte than "normal"
            "temperature": config.TTS_TEMPERATURE,
            "top_p": config.TTS_TOP_P,
        }
    ).encode()
    req = urllib.request.Request(
        FISH_TTS_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {os.environ['FISH_AUDIO_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    # python.org builds of Python on macOS don't see the system root certs;
    # certifi's bundle (already installed with the openai package) does.
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        carry = b""  # a chunk boundary can split a 16-bit sample in half
        while chunk := resp.read(4096):
            chunk = carry + chunk
            if len(chunk) % 2:
                chunk, carry = chunk[:-1], chunk[-1:]
            else:
                carry = b""
            if chunk:
                yield chunk


class Biquad:
    """One second-order IIR filter section with streaming state, so a chunk
    picks up exactly where the last one left off (no clicks at chunk edges)."""

    def __init__(self, b: np.ndarray, a: np.ndarray) -> None:
        self.b = b / a[0]
        self.a = a / a[0]
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0

    @classmethod
    def highpass(cls, fc: float, sr: int = SAMPLE_RATE, q: float = 0.7071) -> "Biquad":
        w0 = 2 * np.pi * fc / sr
        al = np.sin(w0) / (2 * q)
        c = np.cos(w0)
        return cls(np.array([(1 + c) / 2, -(1 + c), (1 + c) / 2]), np.array([1 + al, -2 * c, 1 - al]))

    @classmethod
    def high_shelf(cls, fc: float, gain_db: float, sr: int = SAMPLE_RATE, q: float = 0.7071) -> "Biquad":
        A = 10 ** (gain_db / 40)
        w0 = 2 * np.pi * fc / sr
        al = np.sin(w0) / (2 * q)
        c = np.cos(w0)
        s = 2 * np.sqrt(A) * al
        b = np.array([A * ((A + 1) + (A - 1) * c + s), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - s)])
        a = np.array([(A + 1) - (A - 1) * c + s, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - s])
        return cls(b, a)

    def process(self, x: np.ndarray) -> np.ndarray:
        b0, b1, b2 = self.b
        _, a1, a2 = self.a
        x1, x2, y1, y2 = self.x1, self.x2, self.y1, self.y2
        y = np.empty_like(x)
        for i, v in enumerate(x.tolist()):
            o = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, v, y1, o
            y[i] = o
        self.x1, self.x2, self.y1, self.y2 = x1, x2, y1, y2
        return y


PRESENCE_HZ = 1500.0  # the presence shelf starts here; speech clarity is 1-4 kHz


class Equalizer:
    """Speaker tuning: a bass cut (the small speaker can't
    reproduce bass, it just rattles the shell) and a presence lift for a
    voice that comes out muffled. Both off = pass-through."""

    def __init__(self, highpass_hz: float = 0.0, presence_db: float = 0.0) -> None:
        self.stages: list[Biquad] = []
        if highpass_hz > 0:
            # Two stages (4th order, 24 dB/octave): a single one is still
            # letting half the energy through an octave below the cutoff.
            self.stages.append(Biquad.highpass(highpass_hz))
            self.stages.append(Biquad.highpass(highpass_hz))
        if presence_db != 0:
            self.stages.append(Biquad.high_shelf(PRESENCE_HZ, presence_db))

    def process(self, x: np.ndarray) -> np.ndarray:
        for stage in self.stages:
            x = stage.process(x)
        return x


class Leveler:
    """Streaming automatic gain: holds the voice near a steady loudness.

    Fish's level wanders from sentence to sentence and within one, and audio
    streams to the speaker as it's made, so it can't be normalized after the
    fact. Each chunk (~130 ms) gets a loudness reading; a smoothed estimate
    follows it upward over ~0.3 s and downward over ~1.5 s, ignoring
    near-silence so gaps don't pump up the noise. The gain steers toward
    TTS_LEVEL / estimate, capped at TTS_MAX_GAIN, and is ramped
    across each chunk so nothing clicks. The shape inside a chunk (syllables)
    is kept. The equalizer runs first, so the level is judged on
    what the speaker will actually get. Use one Leveler per reply so its
    sentences match each other."""

    GATE = 0.004        # RMS below this is a gap, not a quiet word
    ATTACK = 0.5        # per chunk: louder than expected → follow fast
    RELEASE = 0.1       # per chunk: quieter than expected → follow slowly
    SQUEEZE = 0.35      # how far each chunk is pulled toward the running level
                        # (0 = only sentence-scale leveling, 1 = flatten every chunk)
    CEILING = 0.95      # never let a chunk's peak exceed this: the chunk is turned down, not bent

    def __init__(self) -> None:
        # Read once per reply, so the console's sliders apply from the next one.
        self.level = config.TTS_LEVEL
        self.eq = Equalizer(config.TTS_HIGHPASS_HZ, config.TTS_PRESENCE_DB)
        self.env = 0.0      # loudness estimate; 0 = nothing heard yet
        self.gain = 1.0

    def process(self, pcm: bytes) -> bytes:
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if not len(x):
            return pcm
        x = self.eq.process(x).astype(np.float32)
        rms = float(np.sqrt(np.mean(x * x)))
        if rms > self.GATE:
            if self.env == 0.0:
                self.env = rms                                  # first sound: trust it
            elif rms > self.env:
                self.env += self.ATTACK * (rms - self.env)
            else:
                self.env += self.RELEASE * (rms - self.env)
        if self.env and rms > self.GATE:
            # Judge this chunk by a blend of the running level and its own
            # level: loud bits come down a little, quiet words come up a little.
            judged = self.env * (rms / self.env) ** self.SQUEEZE
            target = min(config.TTS_MAX_GAIN, max(0.5, self.level / judged))
        else:
            target = self.gain  # a gap: hold the gain where it is
        # Peak limiting the way the original build did it (2026-09-06): cap the
        # chunk's gain so its peak stays under CEILING. The waveform is never
        # bent, so loud syllables get quieter rather than harsher.
        peak = float(np.abs(x).max())
        if peak > 0:
            target = min(target, self.CEILING / peak)
        y = x * np.linspace(self.gain, target, len(x), dtype=np.float32)
        self.gain = target
        top = float(np.abs(y).max())
        if top > self.CEILING:  # the ramp started above the cap: trim the whole chunk
            y *= self.CEILING / top
        return (y * 32767).astype(np.int16).tobytes()


def _wav_to_pcm16k(wav_bytes: bytes) -> bytes:
    """Any WAV → 16 kHz mono s16le, whatever rate/channels/width it came in."""
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width == 2:
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768
    elif width == 1:
        samples = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128) / 128
    elif width == 4:
        samples = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2**31
    else:
        raise ValueError(f"unsupported WAV sample width {width}")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        n_out = int(len(samples) * SAMPLE_RATE / rate)
        x_old = np.linspace(0, 1, len(samples), endpoint=False)
        x_new = np.linspace(0, 1, n_out, endpoint=False)
        samples = np.interp(x_new, x_old, samples).astype(np.float32)
    return (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()


_pipers: dict = {}
_piper_lock = threading.Lock()  # one synthesis at a time; also guards loading


def _piper_voice(key: str):
    """A Piper voice from config.PIPER_VOICES, downloaded once (pinned and
    hash-checked like the turn-taking models) and loaded once."""
    with _piper_lock:
        if key not in _pipers:
            from piper import PiperVoice  # optional, GPL-3.0; slow import, keep it lazy

            from .turn import _fetch

            lang, name, quality = key.split("-")
            base = config.PIPER_VOICES[key].get("url") or (
                f"https://huggingface.co/rhasspy/piper-voices/resolve/{config.PIPER_REVISION}/"
                f"{lang[:2]}/{lang}/{name}/{quality}/{key}")
            files = {ext: _fetch(base + ext, key + ext, sha) for ext, sha in config.PIPER_VOICES[key]["sha256"].items()}
            _pipers[key] = PiperVoice.load(str(files[".onnx"]), config_path=str(files[".onnx.json"]))
        return _pipers[key]


def _synthesize_piper(text: str, voice_of=None) -> bytes:
    """The words from Piper as 16 kHz s16le PCM, in the speaking character's
    voice and tuning (personality.voice(); config.PIPER_VOICES explains the
    settings), with a pause after the sentence. The same steps as Piper Lab
    (.devtools/piper_lab), so a voice tuned there sounds the same here.
    voice_of: another character's voice instead (the live view's Test voice)."""
    import parselmouth  # optional, GPL-3.0 (Praat)
    from parselmouth.praat import call
    from piper import SynthesisConfig

    from . import personality

    key, tune = personality.voice(voice_of)
    voice = _piper_voice(key)
    shift = 2 ** (float(tune.get("pitch") or 0) / 12)
    # Pitch by resampling: synthesize slower by `shift`, then play it at the
    # original rate times `shift` (a smaller voice). keep_size: Praat moves the
    # pitch below and leaves the size alone.
    f = shift if tune.get("pitch_mode") == "resample" else 1.0
    speaker = tune.get("speaker") or None
    ids = voice.config.speaker_id_map or {}
    if speaker is not None and speaker not in ids:
        raise ValueError(f"{key} has no speaker {speaker!r} (it has {', '.join(ids) or 'just one'})")
    syn = SynthesisConfig(
        speaker_id=ids.get(speaker) if speaker else None,
        length_scale=voice.config.length_scale * f / float(tune.get("speed") or 1.0),  # speed 1 = its own pace
        noise_scale=voice.config.noise_scale if tune.get("noise") is None else float(tune["noise"]),
        noise_w_scale=voice.config.noise_w_scale if tune.get("noise_w") is None else float(tune["noise_w"]),
    )
    with _piper_lock:
        chunks = list(voice.synthesize(text, syn_config=syn))
    if not chunks:
        return b""
    rate = chunks[0].sample_rate
    pause = float(tune.get("pause", config.PIPER_SENTENCE_PAUSE))
    gap = np.zeros(int(pause * rate * f), dtype=np.float32)  # scaled by f: the resample brings it back
    parts: list[np.ndarray] = []
    for c in chunks:  # Piper may split the text further; same pause between its pieces
        parts += [c.audio_float_array, gap]
    snd = parselmouth.Sound(np.concatenate(parts).astype(np.float64), rate)
    intonation = float(tune.get("intonation", 1.0))
    praat_shift = 1.0 if f != 1.0 else shift
    if abs(intonation - 1) >= 1e-3 or abs(praat_shift - 1) >= 1e-3:
        # Praat pitch manipulation, resynthesized by overlap-add (timing and
        # size kept): the rise and fall scaled around the median pitch by
        # `intonation` (0 = flat), and every point moved by `praat_shift`.
        f0 = snd.to_pitch(pitch_floor=60, pitch_ceiling=500).selected_array["frequency"]
        f0 = f0[f0 > 0]
        if len(f0):
            med = float(np.median(f0))
            manip = call(snd, "To Manipulation", 0.01, 60, 500)
            tier = call(manip, "Extract pitch tier")
            call(tier, "Formula", f"{med * praat_shift} * (self / {med}) ^ {intonation}")
            call([tier, manip], "Replace pitch tier")
            snd = call(manip, "Get resynthesis (overlap-add)")
    # Band-limited, unlike _wav_to_pcm16k's. Read as recorded at rate * f, so a
    # resample pitch shift plays it faster.
    samples = parselmouth.Sound(snd.values[0], snd.sampling_frequency * f).resample(SAMPLE_RATE, 50).values[0]
    return (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()


def _pieces(pcm: bytes, size: int = 4096) -> Iterator[bytes]:
    """A finished buffer in Fish-sized chunks (~130 ms), so the Leveler
    treats it the way it treats streamed audio."""
    for i in range(0, len(pcm), size):
        yield pcm[i:i + size]


class NoBuiltinVoice(RuntimeError):
    """This computer has no speech engine we know how to drive."""


# PowerShell script for Windows' built-in speech engine: UTF-8 text on stdin,
# 16 kHz mono WAV out to $env:DESK_ROBOT_TTS_OUT. (Not $args: arguments after
# -Command are appended to the script text, not passed in.)
_WINDOWS_TTS = """
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SetOutputToWaveFile($env:DESK_ROBOT_TTS_OUT, $fmt)
$s.Speak([Console]::In.ReadToEnd())
$s.Dispose()
"""


def _synthesize_builtin(text: str) -> bytes:
    """The computer's own voice as 16 kHz s16le PCM. Text always goes in on
    stdin, never as an argument, so a reply starting with "-" can't turn
    into options ("-f /etc/hosts" would make `say` read a file)."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    try:
        env = None
        if sys.platform == "darwin":
            cmd = ["say", "-v", config.TTS_FALLBACK_VOICE, "-o", path, "--data-format=LEI16@16000"]
        elif sys.platform == "win32":
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_TTS]
            env = {**os.environ, "DESK_ROBOT_TTS_OUT": path}
        elif shutil.which("espeak-ng") or shutil.which("espeak"):
            cmd = [shutil.which("espeak-ng") or "espeak", "--stdin", "-w", path]
        else:
            raise NoBuiltinVoice("no built-in voice on this system (install espeak-ng, or set FISH_AUDIO_API_KEY)")
        subprocess.run(cmd, input=text.encode(), check=True, timeout=60, env=env)
        with open(path, "rb") as f:
            wav = f.read()
    finally:
        os.unlink(path)
    return _wav_to_pcm16k(wav)


def stream(text: str, leveler: Leveler | None = None) -> Iterator[bytes]:
    """Rocky's words as 16 kHz mono s16le PCM chunks, yielded as they're made.
    The voice engine() picks (Fish streams; Piper arrives a sentence at a
    time), else the computer's own voice (all at once). Loudness is steadied
    by `leveler` (a fresh one if none is given). A saved copy goes to debug/tts/ when DEBUG_SAVE_TTS is on."""
    text = clean_for_tts(text)
    if not text:
        return
    leveler = leveler or Leveler()
    parts: list[bytes] = []
    complete = False
    try:
        chunks: Iterator[bytes] | None = None
        first = b""
        e = engine()
        if e == "fish" and fish_available():
            try:
                chunks = _fish_stream(text)
                first = next(chunks, b"")
            except Exception as ex:  # network, auth, bad voice id... still speak
                print(f"(fish audio failed, using the built-in voice: {ex})")
                chunks = None
        elif e == "piper" and piper_available():
            try:
                chunks = _pieces(_synthesize_piper(text))
                first = next(chunks, b"")
            except Exception as ex:
                print(f"(piper failed, using the built-in voice: {ex})")
                chunks = None
        if chunks is None:
            try:
                first = _synthesize_builtin(text)
            except NoBuiltinVoice as e:
                print(f"(cannot speak: {e})")
                return
        if first:
            first = leveler.process(first)
            parts.append(first)
            yield first
        if chunks is not None:
            try:
                for chunk in chunks:
                    chunk = leveler.process(chunk)
                    parts.append(chunk)
                    yield chunk
            except Exception as ex:  # dropped mid-stream: say what we have
                print(f"(voice stream cut short: {ex})")
        complete = True
    finally:
        if complete and parts and config.DEBUG_SAVE_TTS:
            _save_clip(b"".join(parts), text)


def sample_wav(text: str, character) -> bytes:
    """How a character (personality.Character) sounds saying `text`, as a WAV file for the browser."""
    if engine() != "piper":
        raise ValueError("the brain speaks with Fish Audio (TTS_ENGINE), so a character's Piper voice isn't used")
    pcm = normalize(_synthesize_piper(text, character), 0.9)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE); w.writeframes(pcm)
    return buf.getvalue()


def synthesize(text: str) -> bytes:
    """Rocky's reply as one 16 kHz mono signed 16-bit little-endian PCM buffer."""
    return b"".join(stream(text))


def _save_clip(pcm: bytes, text: str) -> None:
    """Keep recent clips on disk so a weird one can be inspected."""
    try:
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug", "tts")
        os.makedirs(d, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")  # date too, or a morning clip sorts "older" than last night's
        path = os.path.join(d, f"{stamp}.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE); w.writeframes(pcm)
        with open(path[:-4] + ".txt", "w") as f:
            f.write(text + "\n")
        wavs = [f for f in os.listdir(d) if f.endswith(".wav")]
        old = sorted(wavs, key=lambda f: os.path.getmtime(os.path.join(d, f)))[:-30]
        for f in old:
            for ext in (".wav", ".txt"):
                try: os.unlink(os.path.join(d, f[:-4] + ext))
                except OSError: pass
    except OSError:
        pass


def normalize(pcm: bytes, peak: float = 0.95) -> bytes:
    """Scale so the loudest sample hits `peak` of full scale. Used for the
    built-in fallback voice, which arrives all at once."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    top = float(np.abs(samples).max()) if len(samples) else 0.0
    if top < 1:
        return pcm
    return np.clip(samples * (peak * 32767 / top), -32768, 32767).astype(np.int16).tobytes()


def play(pcm: bytes) -> None:
    """Play PCM on this computer's default output. Blocks until done.

    On macOS this uses the system player rather than sounddevice: opening an
    output stream while the mic stream is running trips CoreAudio ("cannot
    do in current context") and can hang, and afplay is a separate process
    so it can't. Elsewhere sounddevice plays it directly (untested here)."""
    if not pcm:
        return
    if sys.platform != "darwin":
        import sounddevice as sd
        sd.play(np.frombuffer(pcm, dtype=np.int16), SAMPLE_RATE, blocking=True)
        return
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    try:
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm)
        subprocess.run(["afplay", path], check=False, timeout=60)
    finally:
        os.unlink(path)


def speak(text: str) -> bytes:
    """Synthesize and play on this computer; returns the PCM for the robot too."""
    pcm = synthesize(text)
    play(pcm)
    return pcm
