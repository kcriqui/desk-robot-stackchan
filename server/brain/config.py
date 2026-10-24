"""Central knobs for the desk-robot brain."""

import os
import re
from pathlib import Path

# ── Secrets ──────────────────────────────────────────────────────────────────
# API keys live in server/.env (git-ignored; see .env.example), one KEY=VALUE
# per line. Anything already exported in the shell wins over the file.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
if _ENV_FILE.is_file():
    for _line in _ENV_FILE.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _v = _v.strip().strip("'\"")
        if _v:  # a blank line in .env means "not set", not "set to nothing"
            os.environ.setdefault(_k.strip(), _v)

# The robot's name — the wake word is "hey <name>". ROBOT_NAME in server/.env
# renames him (e.g. RockyChan), and ROBOT_ALIASES (comma-separated, e.g.
# Rocky) are other names he answers to. The character is personality.py.
ROBOT_NAME = os.environ.get("ROBOT_NAME", "Rocky")
ROBOT_ALIASES = [a.strip() for a in os.environ.get("ROBOT_ALIASES", "").split(",") if a.strip()]


def _spoken_forms(name: str) -> list[str]:
    """How speech-to-text may write a name: "RockyChan" -> "rockychan" and
    "rocky chan" (the wake-word match also accepts "Rocky-chan")."""
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name) or [name]
    return list(dict.fromkeys([name.lower(), " ".join(p.lower() for p in parts)]))


# Every form of every name he answers to, longest first so "rocky chan" wins
# over "rocky" when both match.
NAMES = sorted({f for n in [ROBOT_NAME, *ROBOT_ALIASES] for f in _spoken_forms(n)}, key=len, reverse=True)

# Your name — Rocky calls you this. Set HUMAN_NAME in server/.env so it
# stays out of the repo; "friend" until you do.
HUMAN_NAME = os.environ.get("HUMAN_NAME", "friend")

# Which body the brain is driving. Set BODY in server/.env:
#   desk-robot  the original build in firmware/ (XIAO ESP32S3 on a pan-tilt
#               kit), whose neck nods DOWN from eye level
#   stackchan   an M5Stack StackChan running firmware-stackchan/, whose neck
#               looks UP from eye level
# The head limits under "Face tracking" follow it.
BODY = os.environ.get("BODY", "desk-robot").strip().lower()
if BODY not in ("desk-robot", "stackchan"):
    raise SystemExit(f"BODY={BODY!r} in server/.env: use desk-robot or stackchan")

# Language model. The brain speaks the OpenAI-style chat API, which
# OpenRouter, Anthropic and OpenAI all serve, so pick a provider here and
# put its key in server/.env as LLM_API_KEY. OpenRouter is the default
# because one key reaches every model, and the model is just a string:
#
#   provider    LLM_BASE_URL                       MODEL (examples)
#   OpenRouter  https://openrouter.ai/api/v1       anthropic/claude-haiku-4.5   (best at staying in character, ~$1-4/month)
#                                                  google/gemini-2.5-flash-lite (cheapest that still sounds like Rocky)
#                                                  openai/gpt-4.1-mini          (middle ground)
#   Anthropic   https://api.anthropic.com/v1/      claude-haiku-4-5
#   OpenAI      https://api.openai.com/v1          gpt-4.1-mini
#
# The model must accept images (Rocky sends camera frames) and tool calls
# (he moves his head with them). LLM_BASE_URL and MODEL in server/.env
# override these, so switching provider needs no edit here.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
MODEL = os.environ.get("MODEL", "anthropic/claude-haiku-4.5")
# Token ceiling per model call. Only a backstop: REPLY_MAX_SENTENCES ends the
# reply first and the stream is closed there. Models that think before
# answering (Claude Haiku 5.5) count the thinking against it too, and a
# ceiling of 200 cut their replies and tool calls short.
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "1024"))

# WebSocket port the robot connects to.
PORT = 8765

# Rocky's voice and how much he says. The character itself (system prompt,
# canned lines) is in personality.py.
#   TTS_VOICE_ID  Fish Audio (fish.audio) voice: open the voice's page on the
#                 site and copy the 32-char ID from the URL. Needs
#                 FISH_AUDIO_API_KEY in server/.env.
#   TTS_TEMPERATURE / TTS_TOP_P
#                 Fish generation settings. Lower = steadier, fewer
#                 hallucinated sounds/laughs/extra words (a known quirk of
#                 generated speech); higher = more expressive. Fish's
#                 defaults are 0.7 / 0.7.
#   REPLY_MAX_SENTENCES
#                 the prompt asks for at most this many, and the server cuts
#                 anything past it before it's spoken (keeps voice cost down).
#   TTS_LEVEL     loudness the voice is held to (RMS, 0..1; see TTS_MAX_GAIN).
#   TTS_HIGHPASS_HZ
#                 bass cut before the speaker, 0 = off. The small speaker
#                 can't reproduce bass; it just rattles the shell. A deep
#                 voice wants 250-350.
#   TTS_PRESENCE_DB
#                 boost above ~1.5 kHz, where speech clarity lives, 0 = off.
#                 Lifts a muffled voice; too much sounds thin and hissy.
# The console page has sliders for level / bass cut / presence that apply
# live; set the winners here to keep them.
TTS_VOICE_ID = "6dd07916890445e59c5f019ad0fc7879"
TTS_TEMPERATURE = 0.4
TTS_TOP_P = 0.6
REPLY_MAX_SENTENCES = 3
TTS_LEVEL = 0.12
TTS_HIGHPASS_HZ = 0.0
TTS_PRESENCE_DB = 0.0

# Text-to-speech. The voice comes from Fish Audio (TTS_VOICE_ID above).
TTS_FALLBACK_VOICE = "Fred"  # built-in voice used until Fish Audio is set up: a macOS `say`
                             # voice name (`say -v ?` lists them); Windows and Linux use their default

# Which voice speaks. TTS_ENGINE in server/.env:
#   auto     Fish Audio if FISH_AUDIO_API_KEY is set, else Piper if it's
#            installed, else the computer's own voice (the default)
#   fish / piper / builtin   that one, falling back to the computer's own
#            voice if it can't speak
TTS_ENGINE = os.environ.get("TTS_ENGINE", "auto").strip().lower()
if TTS_ENGINE not in ("auto", "fish", "piper", "builtin"):
    raise SystemExit(f"TTS_ENGINE={TTS_ENGINE!r} in server/.env: use auto, fish, piper or builtin")

# Piper (github.com/OHF-Voice/piper1-gpl): a free voice that runs on this
# computer, well under 0.1 s a sentence on a desktop CPU. Optional, and
# GPL-3.0 licensed, so it's not in requirements.txt:
#   pip install -r requirements-piper.txt
# PIPER_VOICE in server/.env picks one of PIPER_VOICES. Each is tuned here:
#   pitch   semitones up (+) or down (-); the voice keeps its size (Praat)
#   speed   1 = the voice's own pace
#   noise / noise_w   Piper's variation in sound / in timing; None = the
#           voice's own default
# Voice files download once into DESK_ROBOT_CACHE (~60 MB each), pinned to a
# revision of rhasspy/piper-voices and checked against a SHA-256 first. To
# add a voice, try it in a Piper sandbox, then add it here with its hashes.
PIPER_REVISION = "5041a7a49ae5b3a0f30e6837769eb03fdbb231bd"
PIPER_VOICES = {
    "en_US-ryan-medium": {  # chosen 2026-10-21
        "pitch": -1.25, "speed": 1.05, "noise": None, "noise_w": None,
        "sha256": {
            ".onnx": "abf4c274862564ed647ba0d2c47f8ee7c9b717d27bdad9219100eb310db4047a",
            ".onnx.json": "44034c056cb15681b2ad494307c7f3f2e4499d1253c700c711fa0a4607ffe78d",
        },
    },
    "en_US-hfc_male-medium": {  # plan B: its own defaults (it's set 25% faster than most)
        "pitch": 0.0, "speed": 1.0, "noise": None, "noise_w": None,
        "sha256": {
            ".onnx": "d11e403a02bdf5a670c877b3dc56e0e1c8cece6fb30289586314dffdc0a78cb0",
            ".onnx.json": "f66847424aed0bf99ecbb5d7cfde47c0a906f426a0daf7c46f305e7d21afd886",
        },
    },
}
PIPER_VOICE = os.environ.get("PIPER_VOICE", "en_US-ryan-medium").strip()
if PIPER_VOICE not in PIPER_VOICES:
    raise SystemExit(f"PIPER_VOICE={PIPER_VOICE!r} in server/.env: use one of {', '.join(PIPER_VOICES)}")
# Silence after each sentence. Piper adds none of its own between sentences.
PIPER_SENTENCE_PAUSE = 0.2
# Loudness. Fish's level wanders from line to line, so the audio goes through
# an automatic gain control (mouth.Leveler) that holds it near TTS_LEVEL
# (RMS, 0..1) using at most TTS_MAX_GAIN of boost, with a soft limiter on
# the peaks. 0.12 / 6.0 are the values of the original build (2026-09-06),
# which sounded right on the robot's speaker; 0.26 drove every peak into
# the limiter and the amp near clipping (crackly, muffled). Raise the
# robot's `volume` for everyday loudness, not this.
TTS_MAX_GAIN = 6.0
# Diagnostics, both off by default so nothing is written to disk. DEBUG_SAVE_TTS
# saves each spoken clip and its text to server/debug/tts/ (git-ignored, keeps
# the last 30); DEBUG_TTS_CHECK also transcribes each clip afterwards to flag
# audio that doesn't match the text, i.e. the voice model made something up.
# The check runs a second speech model right after every reply, which slows
# the next transcription if you answer quickly.
DEBUG_SAVE_TTS = False
DEBUG_TTS_CHECK = False

# Listening. The robot's mic when it is connected, the computer's otherwise.
LISTEN_ON_START = True
WAKE_PHRASES = [  # "hey"/"hi" + each name, then what speech-to-text tends to hear for "hey Rocky"
    *(f"{greeting} {n}" for n in NAMES for greeting in ("hey", "hi")),
    "hey rocky",
    "hey rocket",
    "hey rocking",
    "hi rocky",
    "a rocky",
    "hey ricky",
]
# The robot can listen for a wake word itself (firmware-stackchan with an
# esp-sr model; it says which in its hello). Then, asleep, it sends no audio
# until it hears it, and the brain only checks that the transcript that
# follows contains it (a false wake goes straight back to sleep).
# WAKE_ON_ROBOT=0 in server/.env makes the brain listen for his name in the
# audio instead, as without the feature. ROBOT_WAKE_WORD is what the
# robot's model listens for, as speech-to-text writes it.
WAKE_ON_ROBOT = os.environ.get("WAKE_ON_ROBOT", "1") not in ("", "0")
ROBOT_WAKE_WORD = os.environ.get("ROBOT_WAKE_WORD", "computer").strip().lower()
# After the robot wakes, how long to wait for something to be said before
# deciding it was a false wake and going back to sleep.
ROBOT_WAKE_GRACE_SECONDS = 8.0
WAKE_PHRASES = list(dict.fromkeys(WAKE_PHRASES))
# Also wake on his name alone at the very start of a sentence ("Rocky, ...",
# "Rocky."). A name further in ("I told Rocky...") still doesn't wake him.
# On for the StackChan: its mic is quieter, and the soft "hey" often doesn't
# make it into the transcript.
WAKE_ON_NAME = BODY == "stackchan"
# Speech-to-text engine. STT_ENGINE in server/.env:
#   whisper    faster-whisper (STT_MODEL below), the default. It's told his
#              name (STT_PROMPT), which helps it hear "Rocky".
#   moonshine  Moonshine (brain/moonshine.py, MOONSHINE_MODEL tiny or base):
#              7-10x faster on short questions, since Whisper always works
#              through a 30 s window. Worth it on a slow CPU. It can't be
#              told his name.
# DEBUG_STT_COMPARE=1 in server/.env also runs the other engine on each
# utterance (after the reply has started) and prints both transcripts, to
# judge Moonshine on your own voice before switching. It costs CPU time.
STT_ENGINE = os.environ.get("STT_ENGINE", "whisper").strip().lower()
if STT_ENGINE not in ("whisper", "moonshine"):
    raise SystemExit(f"STT_ENGINE={STT_ENGINE!r} in server/.env: use whisper or moonshine")
MOONSHINE_MODEL = os.environ.get("MOONSHINE_MODEL", "base").strip().lower()
DEBUG_STT_COMPARE = os.environ.get("DEBUG_STT_COMPARE", "") not in ("", "0")
# Threads for the end-of-turn model (Smart Turn). 2 is plenty on a fast
# CPU; on the NAS's 4 cores, 4 took it from ~160 to ~110 ms.
TURN_THREADS = int(os.environ.get("TURN_THREADS", "2"))
STT_MODEL = "base.en"   # faster-whisper model: base.en ~0.3 s per utterance on an Apple
                         # Silicon Mac, small.en hears a little better but takes ~1 s
# The Hugging Face commit of that model to download (Systran/faster-whisper-<STT_MODEL>).
# Pinned so a changed upload can't be loaded unnoticed; set to None to take the latest.
STT_REVISION = "3d3d5dee26484f91867d81cb899cfcf72b96be6c"
STT_THREADS = int(os.environ.get("STT_THREADS", "8"))  # CPU threads for transcription
                         # (0 = library default of 4); STT_THREADS in server/.env or the
                         # environment overrides it, e.g. 4 on a 4-core server
STT_PROMPT = f"Hey {ROBOT_NAME}. {ROBOT_NAME} is a robot."  # name hint for the model
MIC_SOURCE = os.environ.get("MIC_SOURCE", "auto")
                         # "robot" = only the robot's mic (nothing listens until it connects),
                         # "mac" = this computer's mic (MIC_DEVICE below),
                         # "auto" = robot when it's connected, else this computer.
                         # Set MIC_SOURCE in server/.env to change it.
MIC_DEVICE = os.environ.get("MIC_DEVICE") or None  # local input by name (set MIC_DEVICE in
                         # server/.env); None = system default. List devices: python -m sounddevice
# Speech detection (server/brain/turn.py). A Silero VAD model decides whether
# each 32 ms chunk is speech (VAD_THRESHOLD, 0..1: lower = more sensitive).
# Once speech starts, a cutoff 0.15 lower keeps softer syllables from being
# mistaken for a pause. Microphone level and background noise still matter.
# When you pause for TURN_PAUSE_SECONDS the Smart Turn model listens to the
# whole sentence and
# decides whether you sound finished (probability above TURN_THRESHOLD ->
# Rocky answers now). If it thinks you're mid-thought it keeps listening,
# re-checking every TURN_RECHECK_SECONDS, and gives up waiting after
# TURN_MAX_SILENCE seconds of quiet. If Rocky keeps cutting you off, raise
# TURN_THRESHOLD; if he waits too long after you finish, lower it.
VAD_THRESHOLD = 0.5      # 0.4 let more background sound (TV, radio) through on the StackChan
# Audio kept from before speech is detected, so a soft start isn't cut off.
# 0.3 lost the first words on the StackChan's quiet mic ("Give me a list..."
# came out "A list...").
PRE_ROLL_SECONDS = 0.8
TURN_PAUSE_SECONDS = 0.2
TURN_RECHECK_SECONDS = 0.6
TURN_MAX_SILENCE = 2.5
TURN_THRESHOLD = 0.5
# If you start talking again while Rocky is still thinking (before he speaks),
# speech that lasts this long cancels his reply and he listens to the rest.
CANCEL_MIN_SPEECH = 0.3
DEBUG_SAVE_UTTERANCE = ""  # set to "debug/last_utterance.wav" to keep the last thing heard, for mic tuning
AWAKE_SECONDS = 60.0     # after "hey Rocky" he stays awake; each thing you say
                         # resets this clock, and when it runs out he dozes off
DOZE_HOLD_SECONDS = 20.0  # ...unless he's hearing speech: then he waits up to this
                          # much longer for it to end (not forever: music sounds like speech)
# Speaking up on his own: timers he was asked to set, and warnings about his
# battery and temperature. He waits for any conversation to finish, opens his
# eyes to say it, and stays awake to hear your answer. Lines: personality.py.
# Battery (StackChan only): (below this %, repeat every N minutes or None for
# once, which line). Each lower level warns again, and more often.
BATTERY_WARNINGS = [
    (20, None, "battery_low"),
    (10, 10, "battery_very_low"),
    (5, 2, "battery_critical"),
]
# Powering off on battery (StackChan; alerts.PowerOffWatch). Asleep this long
# on battery, he says goodbye and powers off; only his power button (or USB,
# maybe) turns him back on. A timer that's set keeps him on, unless the
# battery is below POWER_OFF_TIMER_MIN_PERCENT. At POWER_OFF_CRITICAL_PERCENT
# he shuts down whatever is going on. The robot sleeps deeper after 5 minutes
# asleep on battery by itself (firmware-stackchan/include/config.h).
IDLE_POWER_OFF_MINUTES = 30
POWER_OFF_TIMER_MIN_PERCENT = 20
POWER_OFF_CRITICAL_PERCENT = 5
# Chip temperature: he warns once above TEMP_WARN_C, and again (every 10 min)
# from 5 degrees hotter. It resets once he's cooled 5 degrees below it.
TEMP_WARN_C = 70.0
SLEEP_PHRASES = [        # any of these puts him to sleep until the next "hey <name>"
    *(f"{n} {s}" for n in NAMES for s in ("sleep", "go to sleep")),
    "go to sleep",
    "goodnight",
    "good night",
    "stop listening",
]
# What he says as he goes to sleep: personality.py, LINES.

# Emotions the firmware knows how to display: firmware/src/face.cpp's seven on
# the original body; a StackChan (firmware-stackchan/src/face.cpp, from the
# original project's V2) adds five.
EMOTIONS = [
    "neutral",
    "happy",
    "sad",
    "angry",
    "surprised",
    "sleepy",
    "thinking",
]
if BODY == "stackchan":
    EMOTIONS += ["curious", "skeptical", "excited", "love", "confused"]
# Which face pack a StackChan draws (server/faces/<id>.json, or custom/<id>):
# rocky, bean, blocky or visor. The console can switch until the next restart.
FACE_PACK = os.environ.get("FACE_PACK", "rocky").strip() or "rocky"

# Camera. The robot streams small JPEGs while connected; the live view
# is at http://localhost:<LIVE_VIEW_PORT>/ on this computer.
CAMERA_FPS = 10
# Otherwise the camera only keeps the console page's picture fresh: full
# rate is for following a face (and for a moment during a `look`). The
# camera's frames are compressed in software, which heats the chip, so this
# saves heat and battery. Asleep, the robot stops it altogether.
CAMERA_IDLE_FPS = 1
LIVE_VIEW_PORT = 8766
LIVE_VIEW_BIND = "127.0.0.1"  # this computer only. "0.0.0.0" would show the camera to the whole LAN.
SEND_CAMERA_TO_BRAIN = True  # let Rocky see the camera when a question is about seeing
# A frame is attached only when the question is about seeing (any of these
# words or phrases). Everyday words like "this", "that", "here", "there" and
# "right" are deliberately NOT in the list: they made him describe the room
# in answers that had nothing to do with it. He can always use his `look`
# ability to get a fresh picture on his own.
CAMERA_WORDS = [
    "see", "seeing", "look", "looking", "watch", "watching", "camera", "picture",
    "photo", "image", "view", "describe", "notice", "recognize", "recognise",
    "holding", "wearing", "what color", "what colour", "what am i", "who is",
    "who's", "how many", "in front of you", "behind you", "on my desk", "on the desk",
    "in the room", "whiteboard", "on the screen", "on my screen", "read this", "read that",
    "read the", "read what", "show", "showing", "this one", "that one", "which one",
]
# For this long after a question that used the camera (a picture attached,
# or a `look`), the next question gets a picture too: "how about this one?"
CAMERA_FOLLOWUP_SECONDS = 60.0

# Face tracking: the head follows the biggest face in the picture.
# Off until you ask ("Rocky, track me"); phrases below switch it without a
# brain call, and Rocky can also switch it himself when asked in other words.
TRACKING = False
TRACK_ON_PHRASES = ["track me", "follow me", "watch me", "keep your eyes on me", "look at me"]
TRACK_OFF_PHRASES = ["stop tracking", "stop following", "stop watching", "stop looking at me"]
# What he says when tracking starts/stops: personality.py, LINES.
TRACK_HFOV = 62.0          # camera field of view, degrees (OV2640 stock lens)
TRACK_VFOV = 48.0
TRACK_GAIN = 0.5           # fraction of the error corrected per frame (lower = calmer)
TRACK_DEADBAND = 0.10      # ignore errors smaller than this fraction of half-frame
TRACK_POSE_GAIN = 0.8      # fraction of the error to aim for when frames carry the head's
                           # angle (firmware-stackchan): aimed from where the head really
                           # was, so it can be high; under 1 allows for a rough TRACK_HFOV
TRACK_PAN_SIGN = 1         # flip to -1 if the head turns AWAY from you
TRACK_TILT_SIGN = 1        # flip to -1 if it nods the wrong way
TRACK_PAN_LIMIT = 60.0     # must match PAN_MIN/MAX_DEG in firmware config.h
TRACK_TILT_MIN = -60.0     # must match TILT_MIN/MAX_DEG in firmware config.h
TRACK_TILT_MAX = 0.0
TILT_REST = 0.0           # "center": where the head sits when nothing else is going on
TILT_WAKE = 0.0            # where his head comes up to when woken
NOD = "down"               # which way the neck tilts from level (0): "down" or "up"
if BODY == "stackchan":
    # Must match firmware-stackchan/include/config.h.
    TRACK_HFOV = 60.0      # CoreS3 GC0308; approximate, lower it if tracking overshoots
    TRACK_VFOV = 45.0
    # Its frames carry the head's angle (0x03), so tracking aims from that
    # (TRACK_POSE_GAIN) rather than nudging by TRACK_GAIN.
    # Its little servos stop up to ~4 deg short of a target, so a tighter
    # "centred" zone than that leaves the tracker waiting on a move that
    # never comes. 0.15 of the half-frame is about 4.5 deg.
    TRACK_DEADBAND = 0.15
    TRACK_PAN_LIMIT = 120.0
    TRACK_TILT_MIN = 0.0   # level is as low as it goes
    TRACK_TILT_MAX = 90.0
    TILT_REST = 25.0       # it sits on the desk below your eyes: rest looking a little up
    TILT_WAKE = 25.0
    NOD = "up"
TRACK_LOST_SECONDS = 4.0   # no face this long → idle glances resume

# Body language (body.py): small head moves with his emotion while he talks, and a
# glance up while he thinks. Off = he keeps his head still while talking.
BODY_LANGUAGE = True

# How many conversation turns to remember before forgetting the oldest.
MAX_HISTORY_TURNS = 20
# Looking things up. web_search (web.py) needs Anthropic as the provider and
# the anthropic package; WEB_SEARCH=0 in server/.env turns it off. Each search
# costs ~1 cent. check_weather (weather.py) is free.
WEB_SEARCH = os.environ.get("WEB_SEARCH", "1") != "0"
SEARCH_MAX_USES = 3        # searches per question, at most
# Home, for the weather and local searches: "Portland, OR", "Paris, France".
# Set it in server/.env; with none, he asks where.
HOME_LOCATION = os.environ.get("HOME_LOCATION", "").strip()
TEMP_UNIT = "celsius" if os.environ.get("TEMP_UNIT", "").lower().startswith("c") else "fahrenheit"
# The robot's clock screen while he's asleep (StackChan firmware 0.6.0+; clock.py):
# it's told the time zone and, every CLOCK_WEATHER_MINUTES, the weather at
# HOME_LOCATION. CLOCK_TZ (a POSIX rule like PST8PDT,M3.2.0,M11.1.0) overrides the
# zone worked out from this computer's.
CLOCK_TZ = os.environ.get("CLOCK_TZ", "").strip()
CLOCK_24H = os.environ.get("CLOCK_24H", "") == "1"
CLOCK_WEATHER_MINUTES = 30

# Facts he keeps for good (the remember / forget tools; memories.py). The
# server/data folder is git-ignored.
MEMORY_FILE = Path(os.environ.get("MEMORY_FILE") or Path(__file__).resolve().parent.parent / "data" / "memories.json")
