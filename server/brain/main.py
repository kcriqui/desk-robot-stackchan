"""desk-robot brain server.

Run with:  python -m brain.main   (from the server/ directory, venv active)

Three jobs:
  1. WebSocket server the robot connects to over WiFi.
  2. An interactive console so you can talk to the brain and puppet the
     robot from your keyboard right now.
  3. Listening: hears "hey Rocky" on the mic, sends what you say to
     the language model, and speaks the reply through the robot (or the Mac).

A reply is a pipeline, not a wait: the model's words stream in, each
finished sentence goes to the voice as soon as it exists, and the voice's
audio streams to the speaker as it's made. If you start talking again
before Rocky has begun speaking, the reply is dropped and he listens to
the rest of what you're saying, then answers once.

Console commands:
  ask <question>   send a question to the brain, print and speak the reply
  say <text>       speak text (robot speaker if connected, else the Mac)
  volume <0-1>     set the robot's speaker volume
  track on|off     face tracking (head follows you)
  listen           toggle listening on the microphone
  mic              3-second level meter to check the microphone
  emo <name>       push a face to the connected robot
  pan <deg>        push a head turn to the connected robot
  tilt <deg>       push a head nod to the connected robot
  status           show whether a robot is connected
  quit
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import secrets
import struct
import sys
import threading
import time

import websockets

from . import alerts, battery, clock, config, faces, logbook, mouth, personality, settings, weather, web
from .body import Body
from .dance import Dancer, length as dance_length
from .memories import Memories
from .timers import Timers, when
from .thinking import Interrupted, RobotBrain
from .ears import Ears, is_command, normalize, strip_leading_word, strip_wake_word
from .eyes import Eyes
from .tracker import Tracker

robot_socket: websockets.ServerConnection | None = None
robot_peer = ""          # the connected robot's address
robot_fw = (0, 0, 0)     # its firmware version, from the hello
face_pack = personality.face_pack()  # the face pack he wears (server/faces/); the console can switch it
robot_heard_at = 0.0     # when it last sent anything (it sends something every few seconds)
_greeting = 0            # connections waiting to say hello
MAX_GREETING = 8         # more than this at once is a flood: refused at once
MIN_TOKEN_LEN = 16       # a shorter ROBOT_TOKEN (or the example's "change-me") is refused
_bad_token = asyncio.Lock()  # wrong tokens wait out their second one at a time: no rapid guessing in parallel
# The wake word the connected robot listens for itself (its hello says),
# when WAKE_ON_ROBOT allows it; None = the brain listens for his name.
robot_wake_word: str | None = None
robot_woke_at = 0.0  # when the robot last heard it; 0 once a transcript settled it
brain: RobotBrain | None = None  # created in main() once the event loop exists
ears: Ears | None = None
# (text, when speech began, when it ended, when the transcript was ready)
heard: asyncio.Queue[tuple[str, float, float, float]] = asyncio.Queue()
speech_started = asyncio.Event()  # the human began a new turn (set by the ears)
awake_until = 0.0  # while time.time() < this, he's awake: no wake word needed
robot_speak_done = asyncio.Event()  # robot finished playing the last reply
speaking_reply = None  # the SpokenReply being thought and spoken (converse), so a hush can stop it
eyes = Eyes()
head_moves: asyncio.Queue[tuple[float | None, float | None, bool]] = asyncio.Queue()
tracker: Tracker | None = None
# One exchange at a time: a conversation turn, or something he says unasked
# (a timer, a warning), which waits for the turn to finish.
turn_lock = asyncio.Lock()
camera_boost = 0  # looks in progress, which want full-rate frames
last_visual_at = 0.0  # when a question last came with a picture, or he last used `look`
timers = Timers()
body: Body | None = None      # small head moves while he talks (body.py)
dancer: Dancer | None = None  # his four head dances (dance.py)
memories = Memories(config.MEMORY_FILE)
said_unasked: list[tuple[float, str]] = []  # (when, what) he said on his own, newest last

FRAME_BYTES = 3200  # 100 ms of 16 kHz s16le per audio frame to the robot

# What the live-view console shows and can change (see console_state / console_command).
current_emotion = "neutral"
speaker_volume = 0.7        # mirrors firmware SPEAKER_VOLUME until changed here
thinking = False            # a reply is being composed (before it's spoken)
TUNABLE = {                 # config knobs the console may change live: (min, max)
    "VAD_THRESHOLD": (0.1, 0.95),
    "TURN_THRESHOLD": (0.1, 0.95),
    "TURN_MAX_SILENCE": (0.5, 6.0),
    "AWAKE_SECONDS": (10.0, 600.0),
}
VOICE_TUNABLE = {           # speaker tuning the console may change live (mouth.Leveler reads these per reply)
    "TTS_LEVEL": (0.08, 0.40),
    "TTS_HIGHPASS_HZ": (0.0, 600.0),
    "TTS_PRESENCE_DB": (0.0, 12.0),
}


async def send_to_robot(payload: dict) -> bool:
    global current_emotion
    if payload.get("type") == "emotion":
        current_emotion = payload.get("name", current_emotion)
        if body is not None:
            body.feel(current_emotion)
    if robot_socket is None:
        print("(no robot connected — command not sent)")
        return False
    await robot_socket.send(json.dumps(payload))
    return True


def pick_mic_source() -> None:
    """auto: the robot's mic while it's connected, the computer's otherwise."""
    if ears is None:
        return
    if config.MIC_SOURCE == "auto":
        source = "robot" if robot_socket is not None else "mac"
    else:
        source = config.MIC_SOURCE
    if source != ears.source:
        ears.set_source(source)
        if source == "mac" and not ears.has_mac_mic:
            print(f"(no computer mic: {ears.mac_error} — nothing is listening until the robot connects)")
        else:
            print(f"listening through the {'robot' if source == 'robot' else 'computer'} mic")


async def handle_robot(websocket: websockets.ServerConnection) -> None:
    global robot_socket, robot_peer, robot_heard_at, _greeting
    peer = websocket.remote_address[0] if websocket.remote_address else "?"
    # Anyone on the WiFi can reach this port, so the first message must be a
    # hello carrying the shared token from server/.env. Anything else is
    # dropped without a reply. (The stricter checks are from the original V2.)
    if _greeting >= MAX_GREETING:  # a flood of connections that never say hello
        await websocket.close(1013)
        return
    _greeting += 1
    why = ""  # why the first message isn't a usable hello (never shows the token)
    try:
        first = await asyncio.wait_for(websocket.recv(), timeout=2)
        if isinstance(first, str) and len(first) > 4096:  # the robot's hello is ~150 bytes
            hello, why = {}, f"first message too big for a hello ({len(first)} bytes)"
        elif isinstance(first, str):
            hello = json.loads(first)
        else:
            hello = {}
            why = f"first message was binary ({len(first)} bytes, type 0x{first[:1].hex() or '--'})"
    except asyncio.TimeoutError:
        hello, why = {}, "no hello within 2 s"
    except json.JSONDecodeError:
        hello, why = {}, "first message wasn't JSON"
    except websockets.ConnectionClosed:
        hello, why = {}, "closed before saying hello"
    finally:
        _greeting -= 1
    if not isinstance(hello, dict):
        hello = {}  # "[1]", "42", "null" are valid JSON but not a hello
        why = why or "first message wasn't a JSON object"
    expected = os.environ.get("ROBOT_TOKEN", "")
    if len(expected) < MIN_TOKEN_LEN or expected == "change-me":  # the example's placeholder: everyone knows it
        print(f"(refusing robot: set ROBOT_TOKEN in server/.env and the firmware's secrets.h to the same random "
              f"string of at least {MIN_TOKEN_LEN} characters, e.g. from "
              f"`python -c \"import secrets; print(secrets.token_hex(16))\"`)")
        await websocket.close(1008)
        return
    offered = str(hello.get("token", "")).encode()  # bytes: compare_digest rejects non-ASCII str
    if hello.get("type") != "hello" or not secrets.compare_digest(offered, expected.encode()):
        if not why:
            why = (f"first message was {str(hello.get('type'))[:20]!r}, not hello" if hello.get("type") != "hello"
                   else "wrong token")
        print(f"(refused a connection from {peer}: {why})")
        async with _bad_token:  # a wrong token costs a second, one at a time
            await asyncio.sleep(1)
        await websocket.close(1008)
        return
    if robot_socket is not None:
        # Our robot back after a reboot (reset, power, an update over WiFi)
        # before its old connection is noticed as dead: it takes over if it
        # comes from the same address, or the old link has gone quiet (a live
        # robot sends something every few seconds). Anything else is a second
        # robot, or someone else with the token, and is refused.
        if peer != robot_peer and time.time() - robot_heard_at < 8:
            print(f"(refused a second robot from {peer}: one is already connected)")
            await websocket.close(1013)
            return
        old, robot_socket = robot_socket, None
        print(f"(robot reconnected from {peer}: dropping the old connection)")
        asyncio.create_task(old.close(1001))
    robot_socket = websocket
    robot_peer, robot_heard_at = peer, time.time()
    global robot_fw
    try:
        robot_fw = tuple(int(x) for x in str(hello.get("fw", "0")).split(".")[:4])  # 0.6.9.1: four parts
    except ValueError:
        robot_fw = (0, 0, 0)
    print(f"robot connected! (fw {hello.get('fw', '?')}, {peer})")
    global robot_wake_word, robot_asleep, powering_off, powered_off, robot_deep_sleep
    powering_off = False  # it powered back on (or never went)
    powered_off = None
    robot_deep_sleep = False
    offered = str(hello.get("wake") or "").strip().lower()
    robot_wake_word = offered if offered and config.WAKE_ON_ROBOT else None
    if robot_wake_word:
        print(f"wake word: the robot listens for \"{robot_wake_word}\" itself; asleep, it sends no audio")
        global awake_until
        if hello.get("asleep") is False and time.time() >= awake_until:
            # Awake when this brain (re)started: carry on as after a reply,
            # rather than shutting his eyes mid-conversation.
            awake_until = time.time() + config.AWAKE_SECONDS
            robot_asleep = False
        elif time.time() >= awake_until:
            robot_asleep = True  # not in a conversation: let the robot do the listening
    pick_mic_source()
    if config.MIC_SOURCE in ("auto", "robot"):
        await send_to_robot({"type": "mic", "on": True})
    await apply_camera()
    if offered:  # older firmware doesn't know the message
        await send_to_robot({"type": "wake_word", "on": bool(robot_wake_word)})
    # Agree on sleep: the robot may have kept its eyes shut (and its camera
    # off) across a brain restart, or come back awake after a reboot.
    await send_to_robot({"type": "asleep", "on": robot_asleep})
    # No idle head glances: they fight deliberate looks. The eyes still move.
    await send_to_robot({"type": "glance", "on": False})
    asyncio.create_task(send_clock())  # the clock screen's time zone and weather (fetched: don't hold up the link)
    await send_face_pack()
    await send_screen()
    await send_to_robot({"type": "volume", "level": speaker_volume})  # the Settings tab's (kept)
    try:
        async for message in websocket:
            if websocket is robot_socket:
                robot_heard_at = time.time()
            if isinstance(message, bytes):
                kind = message[:1]
                if kind == b"\x01" and ears is not None and len(message) % 2 == 1:
                    ears.push_audio(message[1:])  # 1 type byte + whole 16-bit samples
                elif kind == b"\x02":
                    eyes.push_frame(message[1:])
                elif kind == b"\x03" and len(message) > 5:
                    # Head pan/tilt at capture (int16 tenths of a degree), then the JPEG.
                    pan, tilt = struct.unpack_from("<hh", message, 1)
                    eyes.push_frame(message[5:], pose=(pan / 10.0, tilt / 10.0))
                continue
            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "state":
                continue  # heartbeat every 5 s; not worth the console space
            if event.get("type") == "speak_done":
                robot_speak_done.set()
                dry, rssi, nxt = (event.get(k) for k in ("dry_ms", "rssi", "next_ms"))
                if isinstance(dry, int) and dry > 0:  # firmware-stackchan 0.5.2+ says how it went
                    num = lambda v: v if isinstance(v, int) else "?"  # numbers only into the log
                    print(f"  (robot: the voice ran dry for {dry} ms; WiFi {num(rssi)} dBm; next reply "
                          f"buffers {num(nxt)} ms first)")
                continue
            if event.get("type") == "temp":
                try:
                    eyes.temperature = float(event.get("c"))
                except (TypeError, ValueError):
                    pass  # not a number; leave the last reading
                continue
            if event.get("type") == "wake":  # the robot heard its wake word
                await on_robot_wake(str(event.get("word") or "?"))
                continue
            if event.get("type") == "hush":  # a hand over its sensor stopped it talking (firmware 0.6.9.2+)
                on_robot_hush()
                continue
            if event.get("type") == "battery":  # every ~10 s (firmware-stackchan)
                robot_deep_sleep = bool(event.get("deep", False))
                try:
                    eyes.battery = battery.Reading(float(event["v"]), float(event["ma"]), time.time())
                except (KeyError, TypeError, ValueError):
                    pass  # malformed; leave the last reading
                if "lux" in event:  # its light sensor (0.6.7+), for the Settings tab
                    eyes.light = {"lux": event.get("lux"), "prox": event.get("prox"), "dark": event.get("dark")}
                continue
            print(f"robot: {event}")
    except websockets.ConnectionClosed:
        pass
    finally:
        # Only clear the slot if it's still ours: when the robot reboots, the
        # new connection can arrive before the old one is noticed as dead.
        if robot_socket is websocket:
            robot_socket = None
            robot_wake_word = None
            print("robot disconnected")
            pick_mic_source()


async def handle_console_line(line: str) -> bool:
    """Returns False when the server should shut down."""
    line = line.strip()
    if not line:
        return True
    cmd, _, arg = line.partition(" ")
    cmd = cmd.lower()
    arg = arg.strip()

    if cmd == "quit":
        return False
    if cmd == "status":
        print("robot connected" if robot_socket else "no robot connected")
    elif cmd == "emo":
        if arg in config.EMOTIONS:
            await send_to_robot({"type": "emotion", "name": arg})
        else:
            print(f"emotions: {', '.join(config.EMOTIONS)}")
    elif cmd in ("pan", "tilt"):
        try:
            deg = float(arg)
            await send_to_robot({"type": cmd, "deg": deg})
            if tracker is not None:
                tracker.note_pose(**{cmd: deg})
        except ValueError:
            print(f"usage: {cmd} <degrees>")
    elif cmd == "track":
        await set_tracking(arg != "off")
    elif cmd == "ask":
        if not arg:
            print("usage: ask <question>")
            return True
        async with turn_lock:
            await converse(arg)
    elif cmd == "listen":
        if ears is None:
            await start_listening()
        else:
            stop_listening()
    elif cmd == "mic":
        await mic_meter()
    elif cmd == "say":
        if arg:
            async with turn_lock:
                await say(arg)
        else:
            print("usage: say <text>")
    elif cmd == "volume":
        try:
            await set_volume(float(arg))
        except ValueError:
            print("usage: volume <0.0-1.0>")
    elif cmd == "leds":  # leds test | dance on|off | alert timer|battery|hot|hush: the light bars
        if arg == "test":
            await send_to_robot({"type": "leds", "test": True})
        elif arg.startswith("dance"):
            await send_to_robot({"type": "leds", "dance": arg.endswith("on")})
        elif arg.startswith("alert "):
            await send_to_robot({"type": "leds", "alert": arg.split()[1]})
        else:
            print("leds test | dance on|off | alert timer|battery|hot|hush")
    elif cmd == "character":
        if arg:
            try:
                await use_character(arg)
            except ValueError as e:
                print(e)
        print(f"character: {personality.CHARACTER.id} (of {', '.join(c.id for c in characters())}; "
              "CHARACTER in server/.env keeps one)")
    elif cmd == "face":
        global face_pack
        ids = [p["id"] for p in faces.list_packs() if "error" not in p]
        if arg in ids:
            face_pack = arg
            await send_face_pack()
        print(f"face pack: {face_pack} (of {', '.join(ids)}; FACE_PACK in server/.env keeps one)")
    elif cmd == "dance":
        print(f"dancing: {start_dance(arg or None)}" if robot_socket is not None else "no robot connected")
    elif cmd == "gesture":
        if body is None or arg not in config.EMOTIONS:
            print(f"usage: gesture <{'|'.join(config.EMOTIONS)}>")
        else:
            await send_to_robot({"type": "emotion", "name": arg})
            body.test(arg)
    elif cmd == "poweroff":
        if robot_socket is None:
            print("no robot connected")
        else:
            await power_off("power_off_idle")  # the goodbye, then the robot powers off
    elif cmd == "memories":
        facts = memories.facts()
        print("\n".join(f"  #{f.id} {f.text}  ({f.added}" + (f" to {f.until})" if f.until else ")")
                        for f in facts) or "  (remembers nothing yet)")
    elif cmd == "remember":
        print(remember({"fact": arg})[0] if arg else "usage: remember <fact>")
    elif cmd == "forget":
        print(forget({"about": arg})[0] if arg else "usage: forget <words | #number | all>")
    else:
        print("commands: ask <q> | say <text> | volume <0-1> | listen | mic | track on|off | emo <name> | pan <deg> | tilt <deg> | character [id] | face [pack] | dance [name] | leds test | gesture <emotion> | memories | remember <fact> | forget <words|#|all> | poweroff | status | quit")
    return True


# ── Rocky's abilities (called by the brain, from its worker thread) ──────────

async def apply_camera() -> None:
    """Full-rate frames while following a face or looking around, else just
    enough to keep the console page's picture fresh (config.CAMERA_IDLE_FPS).
    Asleep, the robot pauses the stream itself and resumes this on waking."""
    fast = camera_boost > 0 or (tracker is not None and tracker.enabled)
    await send_to_robot({"type": "stream", "on": True,
                         "fps": config.CAMERA_FPS if fast else config.CAMERA_IDLE_FPS})


async def fresh_frame(timeout: float = 2.0) -> bytes | None:
    """Ask for full-rate frames until a new one arrives (or `timeout`)."""
    global camera_boost
    seq = eyes.frame_seq
    camera_boost += 1
    await apply_camera()
    try:
        for _ in range(int(timeout / 0.1)):
            if eyes.frame_seq != seq:
                break
            await asyncio.sleep(0.1)
    finally:
        camera_boost -= 1
        await apply_camera()
    return eyes.latest()


async def look(args: dict) -> tuple[str, bytes | None]:
    """Body language holds still while he looks (and talks about it)."""
    if dancer is not None:
        dancer.stop()
    if body is None:
        return await _look(args)
    async with body.hold():
        return await _look(args)


async def _look(args: dict) -> tuple[str, bytes | None]:
    """Move the head, wait for it to get there, and grab a fresh frame.
    Only the axis that was asked for moves: "left"/"right" pan, "down"/"up"/
    "level" tilt (whichever this body's neck can do), "center" both, "here"
    neither (just a fresh picture, e.g. of something being shown to him)."""
    global last_visual_at
    last_visual_at = time.time()
    if args.get("direction") == "here":
        jpeg = await fresh_frame()
        return (("Head didn't move. Fresh camera image attached." if jpeg
                 else "Head didn't move. No camera image available."), jpeg)
    pan = tracker.pan if tracker else 0.0
    tilt = tracker.tilt if tracker else 0.0
    move_pan = move_tilt = False
    d = args.get("direction")
    try:
        amount = float(args["degrees"]) if args.get("degrees") is not None else None
    except (TypeError, ValueError):
        amount = None
    if d in ("left", "right"):
        deg = amount if amount is not None else 40.0
        pan, move_pan = (-deg if d == "left" else deg), True
    elif d == "down":
        tilt, move_tilt = -(amount if amount is not None else 30.0), True  # a normal glance down; ask for degrees to go further
    elif d == "up":
        tilt, move_tilt = (amount if amount is not None else 30.0), True
    elif d == "level":
        tilt, move_tilt = 0.0, True
    elif d == "center":
        pan, tilt, move_pan, move_tilt = 0.0, config.TILT_REST, True, True
    if not (move_pan or move_tilt):
        return (f"Say where to look: here, left, right, {config.NOD}, level, or center.", None)
    pan = max(-config.TRACK_PAN_LIMIT, min(config.TRACK_PAN_LIMIT, pan))
    tilt = max(config.TRACK_TILT_MIN, min(config.TRACK_TILT_MAX, tilt))

    global camera_boost
    if tracker is not None and tracker.enabled:
        await set_tracking(False, announce=False)  # tracking would drag the head back
    camera_boost += 1  # full rate, so a frame from the new angle comes quickly
    await apply_camera()
    try:
        await set_head_held(d != "center")
        if move_pan:
            await send_to_robot({"type": "pan", "deg": pan})
        if move_tilt:
            await send_to_robot({"type": "tilt", "deg": tilt})
        if tracker is not None:
            tracker.note_pose(pan=pan if move_pan else None, tilt=tilt if move_tilt else None)
        await asyncio.sleep(1.2)  # servo easing + a frame or two from the new angle
        seq = eyes.frame_seq
        for _ in range(10):
            if eyes.frame_seq != seq:
                break
            await asyncio.sleep(0.1)
        jpeg = eyes.latest()
    finally:
        camera_boost -= 1
        await apply_camera()
    pan_word = "left" if pan < -5 else "right" if pan > 5 else "center"
    # Level (0) is one end of the travel: the top for a neck that nods down,
    # the bottom for one that looks up.
    if tilt <= config.TRACK_TILT_MIN + 0.5:
        tilt_word = "level, as low as it goes" if tilt > -5 else "down, as far as it goes"
    elif tilt >= config.TRACK_TILT_MAX - 0.5:
        tilt_word = "level, as high as it goes" if tilt < 5 else "up, as far as it goes"
    else:
        tilt_word = "down" if tilt < -5 else "up" if tilt > 5 else "level"
    where = f"Head is now at pan {pan:.0f} deg ({pan_word}), tilt {tilt:.0f} deg ({tilt_word})."
    return (where + (" Fresh camera image attached." if jpeg else " No camera image available."), jpeg)


head_held = False  # he was told to look somewhere and is holding that pose


async def set_head_held(held: bool) -> None:
    """Remember that a deliberate look is in effect (shown on the console).
    Idle glances are always off, so nothing else needs to move."""
    global head_held
    head_held = held


robot_asleep = False       # the last sleep state sent to the robot
resume_tracking = False    # he was tracking when he fell asleep: pick it up again on waking


async def set_asleep(on: bool) -> None:
    """Shut or open his eyes. Every sleep and wake goes through here, so the
    face can't be left asleep while he talks. Falling asleep pauses face
    tracking (a sleeping robot shouldn't keep turning to follow you), and
    waking resumes it."""
    global robot_asleep, resume_tracking
    robot_asleep = on
    if on:
        if body is not None:
            body.stop()
        if dancer is not None:
            dancer.stop()
    if on and tracker is not None and tracker.enabled:
        await set_tracking(False, announce=False)
        resume_tracking = True
    elif not on and resume_tracking:
        await set_tracking(True, announce=False)
    await send_to_robot({"type": "asleep", "on": on})


def situation() -> str:
    """His current state, told to the model with every question."""
    if tracker is None:
        state = "You have no face tracker running right now."
    elif tracker.enabled:
        state = "Right now you ARE following your human's face with your head (track_face is on)."
    else:
        state = "Right now you are NOT following your human's face (track_face is off)."
    now = time.time()
    state = time.strftime("It is now %A %d %B, %H:%M. ", time.localtime(now)) + state
    pending = timers.pending()
    if pending:
        state += " Timers set: " + "; ".join(
            f"{t.about or 'a timer'} rings {when(t.due, now)}" for t in pending) + "."
    recent = [text for at, text in said_unasked if now - at < 600]
    if recent:
        state += " On your own, a few minutes ago, you said: " + " / ".join(f'"{t}"' for t in recent[-3:])
    b = eyes.battery
    if b is not None and b.low() and now - b.at < 60:
        state += (f" Your battery is LOW (about {b.percent}%, roughly {b.minutes_left()} minutes left): "
                  "mention it and ask to be plugged in.")
    if eyes.temperature is not None and eyes.temperature >= config.TEMP_WARN_C:
        state += f" Your chip is hot ({eyes.temperature:.0f} C)."
    return state


def check_temperature(args: dict) -> tuple[str, bytes | None]:
    c = eyes.temperature
    if c is None:
        return ("No temperature reading yet: your body hasn't reported one.", None)
    verdict = ("too hot" if c >= config.TEMP_WARN_C + 5 else "hot" if c >= config.TEMP_WARN_C
               else "warm but fine" if c >= config.TEMP_WARN_C - 15 else "cool")
    return (f"The chip inside your head is at {c:.1f} C: {verdict} (you warn your human from "
            f"{config.TEMP_WARN_C:.0f} C). It's your own temperature, not the room's; you have no "
            "room thermometer.", None)


def set_timer(args: dict) -> tuple[str, bytes | None]:
    try:
        t = timers.add(float(args.get("minutes", 0)), str(args.get("about") or ""))
    except (TypeError, ValueError) as e:
        return (f"Timer not set: {e}.", None)
    print(f"  (timer: {t.about or 'no label'} rings {when(t.due)})")
    return (f"Timer set: {t.about or 'no label'}, rings {when(t.due)}. You will announce it yourself.", None)


def cancel_timer(args: dict) -> tuple[str, bytes | None]:
    gone = timers.cancel(str(args.get("about") or ""))
    if not gone:
        left = timers.pending()
        return ("No timer matched. " + ("Timers set: " + "; ".join(t.about or "a timer" for t in left)
                                         if left else "No timers are set."), None)
    return ("Cancelled: " + "; ".join(t.about or "a timer" for t in gone) + ".", None)


def _listed(facts) -> str:
    return "; ".join(f"#{f.id} {f.text.rstrip('.')}" for f in facts)


def remember(args: dict) -> tuple[str, bytes | None]:
    days = args.get("days")
    try:
        fact, new = memories.add(str(args.get("fact") or ""), float(days) if days not in (None, "") else None)
    except (TypeError, ValueError) as e:
        return (f"Not remembered: {e}.", None)
    print(f"  (memory {'+' if new else '='} #{fact.id} {fact.text}" + (f", until {fact.until}" if fact.until else "") + ")")
    return ((f"Remembered as #{fact.id}" if new else f"You already remembered that (#{fact.id}); refreshed")
            + (f", until {fact.until}." if fact.until else ", for good."), None)


def forget(args: dict) -> tuple[str, bytes | None]:
    gone = memories.forget(str(args.get("about") or ""))
    if not gone:
        left = memories.facts()
        return ("Nothing matched. " + (f"You remember: {_listed(left)}. Forget by number to be exact."
                                       if left else "Your memory is empty."), None)
    print(f"  (memory - {_listed(gone)})")
    return (f"Forgotten: {_listed(gone)}.", None)


def web_search(args: dict) -> tuple[str, bytes | None]:
    question = str(args.get("question") or "").strip()
    if not question:
        return ("Search for what? Ask one clear question.", None)
    print(f"  (searching the web: {question})")
    try:
        return (searcher.ask(question), None)
    except Exception as e:  # network, rate limit, timeout: say so, don't crash the turn
        print(f"  (web search failed: {e})")
        return ("The search failed just now (no internet or the search service is busy). Say so.", None)


def check_weather(args: dict) -> tuple[str, bytes | None]:
    try:
        return (weather.report(str(args.get("place") or "")), None)
    except (OSError, ValueError, KeyError) as e:  # offline, or an odd reply
        print(f"  (weather failed: {e})")
        return ("The weather service didn't answer just now. Say so; don't guess.", None)


def start_dance(name: str | None = None) -> str | None:
    """One of his four routines, at random (or by name); it starts once his line is said. The routine's name, or
    None with no robot to dance."""
    global awake_until
    if dancer is None or robot_socket is None:
        return None
    picked = dancer.start(name)
    awake_until = max(awake_until, time.time() + dance_length(picked) + config.AWAKE_SECONDS)  # no dozing mid-dance
    return picked


async def dance(args: dict) -> tuple:
    """The `dance` ability. Done: nothing more for the model to say about it."""
    picked = start_dance()
    if picked is None:
        return ("No body connected: can't dance right now.", None)
    return (f"Dancing now ({picked}, about {dance_length(picked):.0f} s). Your line before it was enough: say "
            "nothing more, and don't describe the dance.", None, True)


pending_character: str | None = None  # switch_character: who takes over once the goodbye has been spoken


def switch_character(args: dict) -> tuple:
    """The `switch_character` ability: the handover itself waits for the end of the reply (converse), so the
    goodbye is spoken in the old voice and the new character's first words in theirs."""
    global pending_character
    who = str(args.get("who", "")).strip().lower()
    chars = characters(fresh=True)
    match = next((c for c in chars if who in (c.id, c.name.lower(), personality.name(c).lower())), None)
    if match is None:
        return (f"There's no character {who!r}. There are: " + ", ".join(c.id for c in chars), None)
    if match.id == personality.CHARACTER.id:
        return (f"You already are {match.name}.", None)
    pending_character = match.id
    return (f"{personality.name(match)} takes over as soon as you finish speaking. Your goodbye was enough: "
            "say nothing more.", None, True)


async def hand_over(char_id: str) -> None:
    """Become the character switch_character picked, and let them say hello."""
    try:
        await use_character(char_id)
    except ValueError as e:
        print(f"(couldn't switch: {e})")
        return
    await send_to_robot({"type": "emotion", "name": "happy"})
    print(f"{personality.name()} [happy]: {personality.LINES['wake']}")
    await say(personality.LINES["wake"])


async def _before_dancing() -> None:
    """The dance has the head: he's awake (the console's Dance button can start one while he sleeps), body language
    stops, face tracking goes off, no held pose."""
    if robot_asleep:
        await set_asleep(False)
    if body is not None:
        body.stop()
    if tracker is not None and tracker.enabled:
        await set_tracking(False, announce=False)
    await set_head_held(False)


def check_battery(args: dict) -> tuple[str, bytes | None]:
    if eyes.battery is None:
        return ("No battery reading yet: your body hasn't reported one.", None)
    return (eyes.battery.describe(), None)


async def set_tracking(on: bool, announce: bool = True) -> tuple[str, bytes | None]:
    global resume_tracking
    resume_tracking = False  # an explicit on/off (or the resume itself) settles it
    if tracker is None:
        return ("no tracker running", None)
    tracker.enabled = on
    if on and body is not None:
        body.stop()
    if not on and tracker.tracking:
        tracker.tracking = False
        eyes.tracking_info = {"tracking": False, "pan": tracker.pan, "tilt": tracker.tilt}
    await apply_camera()  # full rate only while following
    if announce:
        print(f"tracking {'on' if on else 'off'}")
    return ("now following the human's face" if on else "stopped following", None)


def _sync(coro_fn):
    """Wrap an async ability so the brain's worker thread can call it."""
    def run(args: dict):
        return asyncio.run_coroutine_threadsafe(coro_fn(args), main_loop).result(timeout=15)
    return run


main_loop: asyncio.AbstractEventLoop | None = None
ABILITIES = {
    "look": _sync(look),
    "track_face": _sync(lambda args: set_tracking(bool(args.get("on", True)))),
    "dance": _sync(dance),
}
ABILITIES.update(check_temperature=check_temperature, set_timer=set_timer, cancel_timer=cancel_timer,
                 remember=remember, forget=forget, check_weather=check_weather, switch_character=switch_character)
searcher = web.WebSearch() if web.available() else None
if searcher is not None:  # Anthropic as the provider (web.py)
    ABILITIES["web_search"] = web_search
if config.BODY == "stackchan":  # the original body has no battery sensor
    ABILITIES["check_battery"] = check_battery


def wants_camera(question: str) -> bool:
    """Does the question sound like it's about what Rocky can see?"""
    q = " " + normalize(question) + " "
    return any(f" {w} " in q or (" " in w and w in q) for w in config.CAMERA_WORDS)


# ── Speaking ─────────────────────────────────────────────────────────────────

class Timeline:
    """Stage times of one reply, measured from the end of the human's speech."""

    ORDER = ("heard", "face", "first sentence", "first audio", "speaking", "done")

    def __init__(self, t0: float) -> None:
        self.t0 = t0
        self.marks: dict[str, float] = {}

    def mark(self, name: str) -> None:
        self.marks.setdefault(name, time.time())

    def report(self) -> None:
        parts = [f"{n} {self.marks[n] - self.t0:.2f}s" for n in self.ORDER if n in self.marks]
        if parts:
            print("  timing (after your last word): " + " · ".join(parts))


class SpokenReply:
    """One reply in flight, as a three-stage pipeline:
    brain thread (model → sentences) → voice thread (Fish → PCM) → the speaker.

    Each stage hands off through a queue, so the first sentence is being
    voiced while the model writes the second, and audio plays while the
    voice is still generating."""

    BREAK = b""  # marks a sentence boundary in the audio queue

    def __init__(self, loop: asyncio.AbstractEventLoop, timeline: Timeline) -> None:
        self.loop = loop
        self.tl = timeline
        self.leveler = mouth.Leveler()  # one per reply: steady loudness across its sentences
        self.sentences: queue.Queue[str | None] = queue.Queue()
        self.audio: queue.Queue[bytes | None] = queue.Queue()
        self.cancel = threading.Event()
        self.spoken: list[str] = []
        self.pcm: list[bytes] = []

    @property
    def text(self) -> str:
        return " ".join(self.spoken)

    def think_and_speak(self, question: str, jpeg: bytes | None, camera_wanted: bool = False) -> None:
        threading.Thread(target=self._think, args=(question, jpeg, camera_wanted), daemon=True).start()
        threading.Thread(target=self._voice, daemon=True).start()

    def speak_fixed(self, text: str) -> None:
        """A canned line: no brain involved."""
        self.spoken.append(text)
        self.sentences.put(text)
        self.sentences.put(None)
        threading.Thread(target=self._voice, daemon=True).start()

    def _on_emotion(self, name: str) -> None:
        # Called from the brain thread as soon as the reply's emotion is known.
        if self.cancel.is_set():
            return
        self.tl.mark("face")
        asyncio.run_coroutine_threadsafe(send_to_robot({"type": "emotion", "name": name}), self.loop)

    def _think(self, question: str, jpeg: bytes | None, camera_wanted: bool) -> None:
        gen = brain.reply(question, jpeg, on_emotion=self._on_emotion, cancelled=self.cancel,
                          camera_wanted=camera_wanted)
        try:
            for sentence in gen:
                if self.cancel.is_set():
                    break
                if not self.spoken:
                    self.tl.mark("first sentence")
                self.spoken.append(sentence)
                print(f"{personality.name()} [{brain.emotion}]: {sentence}")
                self.sentences.put(sentence)
        except Interrupted:
            pass
        except Exception as e:  # never let a brain hiccup kill the server
            print(f"(brain error: {e})")
        finally:
            gen.close()  # drops the question from his memory if we bailed early
            self.sentences.put(None)

    def _voice(self) -> None:
        try:
            while (sentence := self.sentences.get()) is not None:
                if self.cancel.is_set():
                    continue
                for chunk in mouth.stream(sentence, self.leveler):
                    if self.cancel.is_set():
                        break
                    if not self.pcm:
                        self.tl.mark("first audio")
                    self.pcm.append(chunk)
                    self.audio.put(chunk)
                self.audio.put(self.BREAK)
        except Exception as e:
            print(f"(voice error: {e})")
        finally:
            self.audio.put(None)

    async def _next(self) -> bytes | None:
        return await self.loop.run_in_executor(None, self.audio.get)

    async def play(self, interruptible: bool = True) -> bool:
        """Send the audio to the speaker as it arrives. Until the first audio
        is ready the human can cancel the whole reply by talking again;
        returns False in that case. Once Rocky is speaking the mic is muted
        (his voice would trigger it) and he finishes what he's saying."""
        get = asyncio.ensure_future(self._next())
        while not get.done():
            if interruptible and (speech_started.is_set() or (ears is not None and ears.talking)):
                self._abort()
                await get
                return False
            await asyncio.wait({get}, timeout=0.05)
        first = get.result()
        if first is None:
            return True  # nothing to say
        if ears is not None:
            ears.muted.set()
        if body is not None:
            body.speaking(current_emotion)  # the reply's emotion is on his face by now
        try:
            if robot_socket is not None:
                await self._play_robot(first)
            else:
                await self._play_mac(first)
        finally:
            if ears is not None:
                ears.muted.clear()
            if body is not None:
                body.quiet()
        self.tl.mark("done")
        return True

    def stop(self) -> None:
        """Hushed mid-reply: no more words (the robot has dropped what it had)."""
        if not self.cancel.is_set():
            self._abort()

    def _abort(self) -> None:
        self.cancel.set()
        brain.abandon()
        self.sentences.put(None)  # wake the voice thread so it can exit
        self.audio.put(None)

    async def _play_robot(self, first: bytes) -> None:
        """Stream PCM to the robot (docs/protocol.md) and wait until it has played."""
        robot_speak_done.clear()
        # bytes 0 = length unknown: the robot starts after 200 ms of buffer.
        await send_to_robot({"type": "speak_begin", "bytes": 0})
        self.tl.mark("speaking")
        buf = bytearray()
        total = 0
        chunk: bytes | None = first
        while chunk is not None:
            buf += chunk
            while len(buf) >= FRAME_BYTES:
                if robot_socket is None:
                    return
                await robot_socket.send(b"\x01" + bytes(buf[:FRAME_BYTES]))
                del buf[:FRAME_BYTES]
                total += FRAME_BYTES
            chunk = await self._next()
        if buf and robot_socket is not None:
            await robot_socket.send(b"\x01" + bytes(buf))
            total += len(buf)
        await send_to_robot({"type": "speak_end"})
        try:
            await asyncio.wait_for(robot_speak_done.wait(), timeout=total / 32000 + 5)
        except asyncio.TimeoutError:
            print("(robot never said it finished speaking)")

    async def _play_mac(self, first: bytes) -> None:
        """No robot: play each sentence on the Mac as soon as it's complete."""
        self.tl.mark("speaking")
        buf = bytearray()
        chunk: bytes | None = first
        while chunk is not None:
            if chunk == self.BREAK:
                if buf:
                    await self.loop.run_in_executor(None, mouth.play, bytes(buf))
                    buf = bytearray()
            else:
                buf += chunk
            chunk = await self._next()
        if buf:
            await self.loop.run_in_executor(None, mouth.play, bytes(buf))


async def converse(question: str, ended_at: float | None = None, heard_at: float | None = None) -> bool:
    """Ask the brain, show the face, and speak the answer as it forms.
    Returns False if the human started talking again before Rocky spoke
    (the reply was dropped and the question is still open)."""
    global awake_until
    loop = asyncio.get_running_loop()
    tl = Timeline(ended_at or time.time())
    if heard_at is not None:
        tl.marks["heard"] = heard_at
    speech_started.clear()
    await send_to_robot({"type": "emotion", "name": "thinking"})
    global last_visual_at
    # A follow-up to something visual ("how about this one?") gets a picture
    # too, though it has no camera words in it.
    followup = time.time() - last_visual_at < config.CAMERA_FOLLOWUP_SECONDS
    camera_wanted = config.SEND_CAMERA_TO_BRAIN and (wants_camera(question) or followup)
    if camera_wanted:
        last_visual_at = time.time()
    jpeg = eyes.latest() if camera_wanted else None
    if camera_wanted and jpeg is None:
        jpeg = await fresh_frame()  # e.g. just woken: the camera was paused
    if camera_wanted and jpeg is None:
        print("  (camera has no fresh frame — telling him he can't see right now)")
    global speaking_reply
    reply = speaking_reply = SpokenReply(loop, tl)
    reply.think_and_speak(question, jpeg, camera_wanted)
    if body is not None:
        body.ponder()  # a glance up while he thinks
    global thinking
    thinking = True
    global pending_character
    try:
        finished = await reply.play()
    except Exception as e:  # a speaker hiccup shouldn't kill the server
        print(f"(could not speak: {e})")
        finished = True
    finally:
        thinking = False
        speaking_reply = None  # done: a hush now (an announcement) has no reply to stop
    switch_to, pending_character = pending_character, None
    if not finished:
        if body is not None:
            body.quiet()
        print("  (you kept talking — Rocky will hear the rest and answer once)")
        await send_to_robot({"type": "emotion", "name": "neutral"})
        return False
    if reply.text:
        eyes.last_said = reply.text
        if config.DEBUG_TTS_CHECK and reply.pcm:
            loop.run_in_executor(None, check_tts, reply.text, b"".join(reply.pcm))
    awake_until = time.time() + config.AWAKE_SECONDS
    tl.report()
    if switch_to:
        await hand_over(switch_to)
    return True


async def say(text: str) -> bytes:
    """Speak a fixed line: through the robot's speaker when it's connected,
    else the Mac. Ears are muted meanwhile so Rocky doesn't hear himself."""
    reply = SpokenReply(asyncio.get_running_loop(), Timeline(time.time()))
    reply.speak_fixed(text)
    try:
        await reply.play(interruptible=False)
    except Exception as e:
        print(f"(could not speak: {e})")
    return b"".join(reply.pcm)


_tts_checker = None


def check_tts(text: str, pcm: bytes) -> None:
    """Transcribe what the voice model produced and compare it with the text
    it was given. Extra words = the model hallucinated (a laugh, "hello", a
    sound). Runs in a worker thread; prints only when something's off."""
    global _tts_checker
    import re
    from .ears import Transcriber
    if _tts_checker is None:
        _tts_checker = Transcriber("base.en")
    import numpy as np
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    heard_text = _tts_checker.transcribe(audio)
    words = lambda t: set(re.findall(r"[a-z']+", t.lower()))
    said, got = words(mouth.clean_for_tts(text)), words(heard_text)
    extra = got - said
    missing = said - got
    # Transcription is imperfect: only shout when several words are foreign.
    if len(extra) >= 3 or (len(extra) >= 2 and len(extra) >= len(got) / 3):
        print(f"  !! TTS CHECK: audio contains words not in the text: {sorted(extra)}")
        print(f"     text : {text}\n     heard: {heard_text}")
    elif len(missing) >= max(3, len(said) // 2):
        print(f"  !! TTS CHECK: audio is missing much of the text. heard: {heard_text}")


async def set_volume(level: float) -> None:
    global speaker_volume
    speaker_volume = round(max(0.0, min(1.0, level)), 2)
    settings.change("robot", "volume", speaker_volume)  # kept across restarts
    await send_to_robot({"type": "volume", "level": speaker_volume})


# ── The live-view console (http://localhost:8766) ────────────────────────────

BRAIN_STARTED = time.time()  # the live view reloads itself when this changes: a restarted brain may serve a new page
_packs_cache: tuple[float, list[dict]] = (0.0, [])


_chars_cache: tuple[float, list] = (0.0, [])


def characters(fresh: bool = False) -> list:
    """The characters there are (personality.available()), re-read at most every 10 s."""
    global _chars_cache
    if fresh or time.time() - _chars_cache[0] > 10:
        _chars_cache = (time.time(), personality.available())
    return _chars_cache[1]


def face_packs() -> list[dict]:
    """The usable face packs, re-read at most every 10 s (the live view asks five times a second)."""
    global _packs_cache
    if time.time() - _packs_cache[0] > 10:
        _packs_cache = (time.time(), [p for p in faces.list_packs() if "error" not in p])
    return _packs_cache[1]


def console_state() -> dict:
    """Extra fields for /status: everything the page shows beyond the camera."""
    now = time.time()
    return {
        "robot": robot_socket is not None,
        "listening": ears is not None,
        "mic": ears.source if ears is not None else None,
        "level": ears.level if ears is not None else 0.0,
        "speech_prob": ears.speech_prob if ears is not None else 0.0,
        "hearing": ears.hearing if ears is not None else False,
        "speaking": ears.muted.is_set() if ears is not None else False,
        "thinking": thinking,
        "awake": now < awake_until,
        "awake_for": max(0.0, awake_until - now),
        "emotion": current_emotion,
        "voice": {k: getattr(config, k) for k in VOICE_TUNABLE},
        "head_held": head_held,
        "tracking_enabled": tracker.enabled if tracker is not None else False,
        "volume": speaker_volume,
        "tuning": {k: getattr(config, k) for k in TUNABLE},
        "battery_ma": eyes.battery.milliamps if eyes.battery is not None else None,
        "deep_sleep": robot_socket is not None and robot_deep_sleep,
        "brain_started": BRAIN_STARTED,
        "emotions": config.EMOTIONS,
        "name": personality.name(),
        "pronouns": personality.CHARACTER.pronouns,
        "light": eyes.light,
        "character": personality.CHARACTER.id,
        "characters": [{"id": c.id, "name": personality.name(c)} for c in characters()],
        "face_pack": face_pack if config.BODY == "stackchan" else None,
        "face_packs": [p["id"] for p in face_packs()] if config.BODY == "stackchan" else [],
        "wake_word": robot_wake_word,  # the robot's own ("computer"), or None: his name wakes him
        "powered_off": ({"why": powered_off[0], "at": time.strftime("%H:%M", time.localtime(powered_off[1]))}
                        if powered_off is not None and robot_socket is None else None),
        "memories": [{"id": f.id, "text": f.text, "added": f.added, "until": f.until} for f in memories.facts()],
    }


def console_command(action: str, payload: dict) -> dict:
    """A control from the page. Runs on the HTTP thread; hops onto the event
    loop. Raises ValueError for anything the page shouldn't have asked."""
    fut = asyncio.run_coroutine_threadsafe(_console_command(action, payload), main_loop)
    return fut.result(timeout=90 if action in ("character_draft", "character_voice") else 10)


def _number(payload: dict, key: str, lo: float, hi: float) -> float:
    try:
        v = float(payload[key])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"{key} must be a number")
    if not (lo <= v <= hi):
        raise ValueError(f"{key} must be between {lo:g} and {hi:g}")
    return v


async def _console_command(action: str, payload: dict) -> dict:
    global awake_until
    if action == "emotion":
        name = payload.get("name")
        if name not in config.EMOTIONS:
            raise ValueError(f"emotions: {', '.join(config.EMOTIONS)}")
        await send_to_robot({"type": "emotion", "name": name})
    elif action == "head":
        if body is not None:
            body.stop()
        if dancer is not None:
            dancer.stop()
        pan = _number(payload, "pan", -config.TRACK_PAN_LIMIT, config.TRACK_PAN_LIMIT)
        tilt = _number(payload, "tilt", config.TRACK_TILT_MIN, config.TRACK_TILT_MAX)
        if tracker is not None and tracker.enabled:
            await set_tracking(False)
        await set_head_held(True)
        await send_to_robot({"type": "pan", "deg": pan})
        await send_to_robot({"type": "tilt", "deg": tilt})
        if tracker is not None:
            tracker.note_pose(pan=pan, tilt=tilt)
    elif action == "center":
        await set_head_held(False)
        await send_to_robot({"type": "pan", "deg": 0})
        await send_to_robot({"type": "tilt", "deg": config.TILT_REST})
        if tracker is not None:
            tracker.note_pose(pan=0, tilt=config.TILT_REST)
    elif action == "volume":
        await set_volume(_number(payload, "level", 0.0, 1.0))
    elif action == "track":
        await set_tracking(bool(payload.get("on", True)))
    elif action == "sleep":
        if payload.get("on", True):
            awake_until = 0.0
            await send_to_robot({"type": "emotion", "name": "sleepy"})
            await set_asleep(True)
            await set_head_held(False)
        else:
            awake_until = time.time() + config.AWAKE_SECONDS
            await set_asleep(False)
            await send_to_robot({"type": "emotion", "name": "neutral"})
    elif action in ("say", "ask"):
        text = str(payload.get("text", "")).strip()
        if not text or len(text) > 300:
            raise ValueError("text must be 1 to 300 characters")
        print(f"console: {action} {text}")

        async def run() -> None:
            async with turn_lock:  # not over a conversation or an announcement
                await (say(text) if action == "say" else converse(text))
        asyncio.create_task(run())
    elif action == "tune":
        key = payload.get("key")
        if key not in TUNABLE:
            raise ValueError(f"tunable: {', '.join(TUNABLE)}")
        value = _number(payload, "value", *TUNABLE[key])
        settings.change(*_TUNE_SETTING[key], value)
        if ears is not None:
            ears.apply_config()
        print(f"console: {key} = {value:g} (kept in server/data/settings.json)")
    elif action == "voice":
        # Speaker tuning; takes effect on the next reply.
        key = payload.get("key")
        if key not in VOICE_TUNABLE:
            raise ValueError(f"voice settings: {', '.join(VOICE_TUNABLE)}")
        value = _number(payload, "value", *VOICE_TUNABLE[key])
        settings.change("speaker", key, value)
        print(f"console: {key} = {value:g} (kept in server/data/settings.json)")
    elif action == "settings":  # the Settings tab: every setting, its default, and whether it was changed
        return {"settings": settings.current(speaker_volume), "face_packs": [p["id"] for p in face_packs()],
                "light": eyes.light,
                "zone": clock._zone_name(), "robot_fw": ".".join(map(str, robot_fw)) if robot_socket else None}
    elif action == "setting":
        return await change_setting(str(payload.get("section", "")), str(payload.get("key", "")), payload.get("value"))
    elif action == "console":  # the Log tab's command box: a line for the console, as if typed at it
        line = str(payload.get("line", "")).strip()
        if not line or len(line) > 300:
            raise ValueError("a command is 1 to 300 characters")
        if line.split()[0].lower() in ("quit", "exit"):
            raise ValueError("quit stops the brain: restart its container instead")
        print(f"> {line}")
        asyncio.create_task(handle_console_line(line))  # `ask` takes longer than the page waits
    elif action == "character":
        await use_character(str(payload.get("id", "")))  # a ValueError goes back to the page
    elif action == "characters":  # the Personalities card: every character in full
        return {"characters": [{"id": c.id, "builtin": c.id == personality.ROCKY.id,
                                "character": personality.to_dict(c)} for c in characters(fresh=True)],
                "active": personality.CHARACTER.id, "voices": list(config.PIPER_VOICES),
                "speakers": {k: v["speakers"] for k, v in config.PIPER_VOICES.items() if v.get("speakers")},
                "default_voice": config.PIPER_VOICE, "face_packs": [p["id"] for p in face_packs()],
                "default_face": config.FACE_PACK, "lines": personality.DEFAULT_LINES,
                "piper": mouth.engine() == "piper"}
    elif action == "character_save":
        cid = payload.get("id") or None
        cid = personality.save(payload.get("character"), cid)
        characters(fresh=True)
        print(f"console: saved character {cid}")
        if cid == personality.CHARACTER.id:
            await use_character(cid)  # the changes apply now
        else:
            personality.refresh()
        return {"id": cid}
    elif action == "character_delete":
        cid = str(payload.get("id", ""))
        personality.delete(cid)
        characters(fresh=True)
        print(f"console: deleted character {cid}")
        if cid == personality.CHARACTER.id:
            await use_character(personality.ROCKY.id)
        else:
            personality.refresh()
        return {"name": personality.name()}
    elif action == "character_draft":
        loop = asyncio.get_running_loop()
        draft = await loop.run_in_executor(None, personality.draft, brain.client,
                                           payload.get("name"), payload.get("description"))
        return {"draft": draft}
    elif action == "character_voice":
        import base64
        c = payload.get("character") or {}
        c = personality.parse("trial", {"name": "Trial", "identity": "-", "examples": ["[neutral] -"],
                                        "voice": c.get("voice") or {}})
        text = (str(payload.get("text") or "").strip()[:300] or "This is how I sound.").replace("{human}", config.HUMAN_NAME)
        loop = asyncio.get_running_loop()
        wav = await loop.run_in_executor(None, mouth.sample_wav, text, c)
        return {"wav": base64.b64encode(wav).decode()}
    elif action == "face":
        global face_pack
        pid = str(payload.get("id", ""))
        if pid not in [p["id"] for p in face_packs()]:
            raise ValueError("no such face pack")
        face_pack = pid
        await send_face_pack()
        print(f"console: face pack {pid}")
    elif action == "dance":
        picked = start_dance(payload.get("name"))
        if picked is None:
            raise ValueError("no robot connected")
        return {"dance": picked}
    elif action == "power_off":
        if robot_socket is None:
            raise ValueError("no robot connected")
        print("console: power off")
        asyncio.create_task(power_off("power_off_idle"))
    elif action == "remember":
        fact, _ = memories.add(str(payload.get("text", "")))  # a ValueError goes back to the page
        print(f"console: remember #{fact.id} {fact.text}")
    elif action == "forget":
        try:
            fid = int(payload.get("id"))
        except (TypeError, ValueError):
            raise ValueError("id must be a number")
        gone = memories.forget(str(fid))
        if not gone:
            raise ValueError(f"no memory #{fid}")
        print(f"console: forget {_listed(gone)}")
    else:
        raise ValueError(f"no such control: {action}")
    return {}


# ── Listening ────────────────────────────────────────────────────────────────

async def mic_meter() -> None:
    """Three seconds of live mic level and speech probability, so you can see
    whether it hears you."""
    if ears is None:
        print("(not listening — type `listen` first)")
        return
    which = "robot mic" if ears.source == "robot" else f"computer mic \"{ears.device_name}\""
    print(f"{which}: talk for 3 seconds...")
    peak_level = 0.0
    peak_prob = 0.0
    for _ in range(12):
        await asyncio.sleep(0.25)
        level, prob = ears.level, ears.speech_prob
        peak_level = max(peak_level, level)
        peak_prob = max(peak_prob, prob)
        print(f"  level {level:.3f} {'#' * min(40, int(level * 500)):40s} speech {prob:.2f}")
    print(f"peak level {peak_level:.3f}, peak speech probability {peak_prob:.2f} (needs > {config.VAD_THRESHOLD}) ->",
          "speech detected" if peak_prob > config.VAD_THRESHOLD else
          ("NOT DETECTED: raise MIC_GAIN in firmware config.h, or get closer" if ears.source == "robot" else
           "NOT DETECTED: wrong mic, gain down, or no mic permission"))
    if ears.source == "mac":
        import sounddevice as sd
        print("other inputs:", ", ".join(f"[{i}] {d['name']}" for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0))


async def start_listening() -> None:
    global ears
    loop = asyncio.get_running_loop()
    print(await loop.run_in_executor(None, mouth.prepare))
    stt = f"Moonshine {config.MOONSHINE_MODEL}" if config.STT_ENGINE == "moonshine" else config.STT_MODEL
    print(f"loading speech model {stt} and the turn-taking models (first run downloads them)...")
    try:
        ears = await loop.run_in_executor(
            None,
            lambda: Ears(
                on_utterance=lambda text, t0, t1: loop.call_soon_threadsafe(
                    heard.put_nowait, (text, t0, t1, time.time())
                ),
                on_speech_start=lambda: loop.call_soon_threadsafe(speech_started.set),
            ),
        )
        # Asleep or out of awake time, he needs his name to answer. When the
        # robot does the waking, only audio after its wake word arrives, and
        # the first transcript must contain that word (_handle_heard); the
        # ears double-check it with Whisper when Moonshine missed it.
        ears.wake_needed = lambda: bool(robot_woke_at) or (
            robot_wake_word is None and time.time() >= awake_until)
        ears.has_wake_word = lambda text: (
            _robot_wake_word_in(text) is not None if robot_woke_at else strip_wake_word(text)[0])
        mic = await loop.run_in_executor(None, ears.start)
    except Exception as e:
        ears = None
        print(f"(could not start listening: {e})")
        print("  check: mic plugged in? Is your terminal allowed to use the microphone (macOS:")
        print("  System Settings > Privacy & Security > Microphone)? MIC_DEVICE in server/.env?")
        return
    if config.MIC_SOURCE == "robot":
        print(f"listening only through the robot's mic, once it connects (MIC_SOURCE=robot)"
              f" — say \"hey {config.ROBOT_NAME}\"")
    elif ears.mac_error:
        print(f"(computer mic unavailable: {ears.mac_error})")
        print("  the robot's mic still works; for a computer fallback check MIC_DEVICE in server/.env,")
        print("  plug the interface in, or allow the Terminal to use the microphone")
    else:
        print(f"listening on \"{mic}\" — say \"hey {config.ROBOT_NAME}\"")
    pick_mic_source()


def stop_listening() -> None:
    global ears
    if ears is not None:
        ears.stop()
        ears = None
    print("stopped listening")


async def head_loop() -> None:
    """Sends the tracker's head moves to the robot; pauses idle glances while
    a face is being followed and resumes them when it's lost."""
    was_tracking = False
    while True:
        pan, tilt, tracking = await head_moves.get()
        if tracking != was_tracking:
            print("tracking: face found — head follows" if tracking else "tracking: face lost — idle glances resume")
            was_tracking = tracking
        if pan is not None:
            await send_to_robot({"type": "pan", "deg": pan})
        if tilt is not None:
            await send_to_robot({"type": "tilt", "deg": tilt})
        eyes.tracking_info = {"tracking": tracking, "pan": pan, "tilt": tilt}


async def doze_loop() -> None:
    """When the awake clock runs out, he nods off on his own (sleepy face,
    no announcement). Saying "hey Rocky" wakes him again."""
    global awake_until
    was_awake = False
    held_since = 0.0  # when he started waiting for you to finish, past the clock
    while True:
        await asyncio.sleep(1)
        if robot_woke_at and time.time() - robot_woke_at > config.ROBOT_WAKE_GRACE_SECONDS and not (
                ears is not None and ears.hearing):
            # The robot woke but nothing was said: a false wake.
            await back_to_sleep("the robot woke, but nothing followed")
            was_awake = False
            continue
        now = time.time()
        if awake_until - now > 5:
            held_since = 0.0  # a transcript restarted the clock: no hold in progress
        if was_awake and now >= awake_until and (turn_lock.locked() or (
                ears is not None and ears.hearing and now - (held_since or now) < config.DOZE_HOLD_SECONDS)):
            # Not in the middle of a reply, or of you talking. The clock only
            # restarts when a transcript arrives, which can come back empty or
            # a moment after you stop, so give it a few seconds at a time.
            # Music can sound like talking, hence the limit.
            held_since = held_since or now
            awake_until = now + 3.0
            continue
        awake = now < awake_until
        if was_awake and not awake:
            print(f"({personality.name()} dozed off — say \"hey {config.ROBOT_NAME}\" to wake {personality.pronoun(1)})")
            await set_asleep(True)
            await set_head_held(False)  # idle life resumes; the head may wander again
        was_awake = awake


async def _next_heard() -> tuple[str, float, float, float] | None:
    """Wait for the rest of an interrupted question. Keeps waiting while the
    ears still hear speech; gives up after a few quiet seconds."""
    waited = 0.0
    while True:
        try:
            return await asyncio.wait_for(heard.get(), timeout=0.5)
        except asyncio.TimeoutError:
            waited += 0.5
            hearing = ears is not None and ears.hearing
            if (not hearing and waited >= 4.0) or waited >= 30.0:
                return None


async def on_robot_wake(word: str) -> None:
    """The robot heard its wake word, opened its eyes and is streaming the
    audio from just before it. Wake up properly; the next transcript must
    contain the wake word, or it was a false wake (see _handle_heard)."""
    global awake_until, robot_woke_at
    print(f"(the robot heard \"{word}\": awake, listening)")
    robot_woke_at = time.time()
    awake_until = time.time() + config.AWAKE_SECONDS
    await set_asleep(False)
    await send_to_robot({"type": "emotion", "name": "surprised"})
    if tracker is None or not tracker.tracking:
        await send_to_robot({"type": "tilt", "deg": config.TILT_WAKE})
        if tracker is not None:
            tracker.note_pose(tilt=config.TILT_WAKE)


def on_robot_hush() -> None:
    """A hand over the robot's sensor stopped it mid-reply (it has already stopped its speaker): drop the rest of
    the reply, as when you talk over it, and stay awake to listen."""
    global awake_until
    print("(a hand stopped the robot talking)")
    awake_until = time.time() + config.AWAKE_SECONDS
    if speaking_reply is not None:
        speaking_reply.stop()


async def back_to_sleep(why: str) -> None:
    """A false wake: eyes shut again, no announcement."""
    global awake_until, robot_woke_at
    print(f"({why}: back to sleep)")
    robot_woke_at = 0.0
    awake_until = 0.0
    await send_to_robot({"type": "emotion", "name": "neutral"})
    await set_asleep(True)


async def announce(key: str, emotion: str, **values) -> None:
    """Speak up unasked: a timer ringing, a low battery, a hot chip. Waits for
    any conversation turn to finish, opens his eyes to say it, and leaves him
    awake so you can answer without the wake word."""
    global awake_until
    text = personality.LINES[key].format(**values)
    if robot_socket is None:
        print(f"(robot not connected; {personality.name()} would have said: {text})")
        return
    async with turn_lock:
        print(f"{personality.name()} [{emotion}] (unasked): {text}")
        if robot_asleep:
            await set_asleep(False)
        await send_to_robot({"type": "emotion", "name": emotion})
        flash = _ALERT_LEDS.get(key)
        if flash and robot_fw >= (0, 6, 9, 5):
            await send_to_robot({"type": "leds", "alert": flash})
        await say(text)
        eyes.last_said = text
        said_unasked.append((time.time(), text))
        del said_unasked[:-5]
        awake_until = time.time() + config.AWAKE_SECONDS


# What he says unasked, and with which face: personality.LINES has the words.
_ALERT_EMOTIONS = {
    "battery_low": "thinking", "battery_very_low": "sad", "battery_critical": "surprised",
    "battery_saved": "happy", "hot": "thinking", "very_hot": "sad",
}
# ...and how the light bars flash for it (leds.h).
_ALERT_LEDS = {"timer": "timer", "timer_plain": "timer", "battery_low": "battery", "battery_very_low": "battery",
               "battery_critical": "battery", "hot": "hot", "very_hot": "hot"}


async def send_clock() -> None:
    """The clock screen's time zone, then the weather (StackChan firmware 0.6.0+)."""
    if robot_fw < (0, 6, 0):
        return
    await send_to_robot({"type": "clock", "tz": clock.posix_tz(), "h24": config.CLOCK_24H})
    await send_weather()


async def use_character(char_id: str) -> str:
    """Become another character (personality.py): their prompt, lines, voice and face from the next word on. The
    conversation starts fresh; long-term memory stays. ValueError if the character doesn't load."""
    global face_pack
    c = personality.use(char_id)
    if brain is not None:
        brain.history.clear()
    face_pack = personality.face_pack()
    await send_face_pack()
    print(f"character: {c.name} (voice {personality.voice()[0]}, face {face_pack})")
    settings.change("robot", "character", c.id)  # a restart keeps them
    if mouth.engine() == "piper":
        # Load (the first time, download) the new voice now, not on the first reply.
        asyncio.get_running_loop().run_in_executor(None, _warm_voice)
    return c.name


def _warm_voice() -> None:
    try:
        mouth._synthesize_piper("Ready.")
    except Exception as e:  # it will say so again when it speaks
        print(f"(voice didn't load: {e})")


_TUNE_SETTING = {"VAD_THRESHOLD": ("listening", "VAD_THRESHOLD"), "TURN_THRESHOLD": ("listening", "TURN_THRESHOLD"),
                 "TURN_MAX_SILENCE": ("listening", "TURN_MAX_SILENCE"), "AWAKE_SECONDS": ("robot", "awake_seconds")}


async def change_setting(section: str, key: str, value) -> dict:
    """The Settings tab changed one (value None = back to the default): check it, keep it, and apply it now."""
    global face_pack
    if (section, key) == ("robot", "character"):
        await use_character(str(value or "rocky"))  # it keeps the setting itself
        return {"value": personality.CHARACTER.id}
    if (section, key) == ("robot", "volume"):
        await set_volume(float(settings.DEFAULTS["robot"]["volume"] if value is None else value))
        return {"value": speaker_volume}
    if (section, key) == ("robot", "face_pack") and value is not None and value not in [p["id"] for p in face_packs()]:
        raise ValueError("no such face pack")
    place = None
    if (section, key) == ("weather", "location") and value:
        place = await asyncio.get_running_loop().run_in_executor(None, weather.find_place, str(value))
        if place is None:
            raise ValueError(f"no place called {value!r} found")
    if (section, key) == ("clock", "timezone") and value and clock.is_zone_name(str(value).strip()):
        import zoneinfo
        try:
            zoneinfo.ZoneInfo(str(value).strip())
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"no time zone called {value!r} (like America/Los_Angeles)") from None
    value = settings.change(section, key, value)
    print(f"settings: {section}.{key} = {value!r}")
    if section == "robot" and key == "human_name":
        personality.refresh()
    elif section == "robot" and key == "face_pack" and not personality.CHARACTER.face:
        face_pack = personality.face_pack()
        await send_face_pack()
    elif section == "clock" or section == "weather":
        asyncio.create_task(send_clock())  # the time zone and style, then the weather (fetched)
    elif section in ("screen", "leds") or key in ("near_stop", "near_level", "power_bank"):
        await send_screen()
    elif section == "listening" and ears is not None:
        ears.apply_config()
    return {"value": value, **({"place": str(place)} if place else {})}


async def send_screen() -> None:
    """Screen brightness and the clock at night (StackChan firmware 0.6.3+; it keeps them in flash)."""
    if robot_fw < (0, 6, 3):
        return
    await send_to_robot({"type": "screen", "face": config.SCREEN_BRIGHTNESS, "clock": config.CLOCK_BRIGHTNESS,
                         "night": ("off", "dim", "dark").index(config.NIGHT_MODE),
                         "dark_lux": config.DARK_LUX, "near": config.NEAR_STOP, "near_level": config.NEAR_LEVEL,
                         "night_pct": config.NIGHT_BRIGHTNESS,
                         "idle": ("clock", "pong", "tetris", "frogger", "roam", "life").index(config.IDLE_SCREEN),
                         "led": config.LED_BRIGHTNESS, "led_sleep": ("off", "glow").index(config.LED_ASLEEP),
                         "bank": config.POWER_BANK})


async def send_face_pack() -> None:
    """His face pack, which the robot keeps in flash (StackChan firmware 0.6.1+)."""
    global face_pack
    if robot_socket is None or robot_fw < (0, 6, 1):
        return
    face_pack, pack = faces.load_or_default(face_pack)
    await send_to_robot(faces.robot_message(pack))


async def send_weather() -> None:
    """The weather at home for the clock screen. Nothing without HOME_LOCATION or when the service is down."""
    if robot_socket is None or robot_fw < (0, 6, 0):
        return
    if not config.CLOCK_WEATHER:
        if robot_fw >= (0, 6, 3):
            await send_to_robot({"type": "weather", "none": True})  # just the time
        return
    try:
        msg = await asyncio.get_running_loop().run_in_executor(None, clock.weather_message)
    except (OSError, ValueError, KeyError) as e:  # offline, or an odd reply
        print(f"(weather for the clock screen: {e})")
        return
    if msg is not None:
        await send_to_robot(msg)


powering_off = False  # the robot was told to power off; cleared when it says hello again
robot_deep_sleep = False  # the robot's own deep sleep (on battery), from its battery reports
powered_off: tuple[str, float] | None = None  # (why, when) we powered it off; None once it's back


async def power_off(key: str) -> None:
    """Say goodbye, then have the robot power itself off (on battery, long
    asleep or nearly flat; alerts.PowerOffWatch). Only its power button
    turns it back on."""
    global powering_off, powered_off, awake_until
    powering_off = True
    why = {"power_off_battery": "battery", "power_off_bank": "power bank"}.get(key, "idle")
    await announce(key, "sleepy")  # waits for any conversation, and for the words to finish playing
    print(f"(powering the robot off: {why})")
    await send_to_robot({"type": "power_off", "why": why})
    powered_off = (why, time.time())
    awake_until = 0.0  # the goodbye kept him "awake"; he's off


async def watch_loop() -> None:
    """Every second: ring due timers, warn about the battery and the chip's
    temperature, and power off on battery when it's time (alerts.py decides
    when)."""
    battery_watch, temp_watch = alerts.BatteryWatch(), alerts.TempWatch()
    power_watch = alerts.PowerOffWatch()
    next_weather = time.time() + config.CLOCK_WEATHER_MINUTES * 60  # (sent on connect too)
    while True:
        await asyncio.sleep(1)
        try:
            for t in timers.due():
                await announce("timer" if t.about else "timer_plain", "surprised", about=t.about)
            now = time.time()
            key = battery_watch.check(eyes.battery, now)
            if key:
                b = eyes.battery
                await announce(key, _ALERT_EMOTIONS[key], percent=b.percent, minutes=b.minutes_left() or 0)
            key = temp_watch.check(eyes.temperature, now)
            if key:
                await announce(key, _ALERT_EMOTIONS[key], temp=round(eyes.temperature))
            if now >= next_weather:
                next_weather = now + config.CLOCK_WEATHER_MINUTES * 60
                asyncio.create_task(send_weather())
            key = power_watch.check(eyes.battery, robot_asleep, bool(timers.pending()), now)
            if key and not powering_off and robot_socket is not None:
                await power_off(key)
        except Exception as e:  # a bad reading or a speaker hiccup mustn't stop the watch
            print(f"(watch loop: {e})")


async def voice_loop() -> None:
    """Turns what the ears hear into conversations. A bug in one turn must
    not kill the loop (then nothing you say would get through), so each turn
    is guarded."""
    while True:
        item = await heard.get()
        try:
            async with turn_lock:
                await _handle_heard(item)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"(voice loop error, ignoring that turn: {e})")


def _robot_wake_word_in(text: str) -> re.Match | None:
    """The robot's wake word in a transcript, with the punctuation after it."""
    return re.search(rf"\b{re.escape(config.ROBOT_WAKE_WORD)}\b[\s,.!?:;-]*", text, re.IGNORECASE)


async def _handle_heard(item: tuple[str, float, float, float]) -> None:
    global awake_until
    global robot_woke_at
    text, started_at, ended_at, heard_at = item
    woke, question = strip_wake_word(text)
    if robot_woke_at:
        # The first thing heard after the robot's wake word must contain it
        # (the robot sends the audio from just before it). The question is
        # whatever follows the word.
        robot_woke_at = 0.0
        m = _robot_wake_word_in(text)
        if m is None:
            await back_to_sleep(f"the robot woke, but \"{text}\" has no \"{config.ROBOT_WAKE_WORD}\" in it")
            return
        # The question is what follows, without the word again ("Computer computer, ...").
        woke, question = True, strip_leading_word(text[m.end():], config.ROBOT_WAKE_WORD, loose=True)
    elif robot_wake_word:
        # Already awake, "Computer, what's ...": the model needn't hear the word, and "Computer" alone is a bare
        # wake ("Question?"), not a question about computers.
        rest = strip_leading_word(text, config.ROBOT_WAKE_WORD)
        if rest != text.strip():
            woke, question = True, rest
    # Judge the follow-up window by when you STARTED talking, not by when
    # the transcript arrived — long sentences shouldn't time out.
    if not woke and started_at >= awake_until:
        print(f"(heard, ignoring: {text})")
        return
    print(f"{config.HUMAN_NAME}: {text}")
    eyes.last_heard = text
    awake_until = time.time() + config.AWAKE_SECONDS  # anything you say keeps him up
    if robot_asleep and not woke:
        # You started talking just before he dozed off, so he's answering:
        # open his eyes. (A wake word opens them below, with the wake pose.)
        await set_asleep(False)
    norm = normalize(question if woke else text)  # "Computer, go to sleep" is the sleep command
    lines = personality.LINES
    # Shortcuts, answered without asking the model, only when the phrase is
    # (nearly) all that was said. Anything else goes to the model as usual.
    if is_command(norm, config.TRACK_ON_PHRASES) and not is_command(norm, config.TRACK_OFF_PHRASES):
        await set_tracking(True)
        print(f"{personality.name()} [happy]: {lines['track_on']}")
        await send_to_robot({"type": "emotion", "name": "happy"})
        await say(lines["track_on"])
        return
    if is_command(norm, config.TRACK_OFF_PHRASES):
        await set_tracking(False)
        print(f"{personality.name()} [neutral]: {lines['track_off']}")
        await send_to_robot({"type": "emotion", "name": "neutral"})
        await say(lines["track_off"])
        return
    if is_command(norm, config.SLEEP_PHRASES):
        # "Rocky, sleep": goodnight line, sleepy face, and only "hey Rocky"
        # wakes him. No brain call.
        awake_until = 0.0
        print(f"{personality.name()} [sleepy]: {lines['sleep']}")
        await send_to_robot({"type": "emotion", "name": "sleepy"})
        await say(lines["sleep"])
        await set_asleep(True)
        awake_until = 0.0  # say() doesn't touch it, but be explicit
        return
    if woke:
        # Heard his name: eyes open, perk up to TILT_WAKE (eye level on the
        # original build, where tilt can't go above 0 — the platform would
        # hit the pan servo; a little up on a StackChan).
        await set_asleep(False)
        await send_to_robot({"type": "emotion", "name": "surprised"})
        if tracker is None or not tracker.tracking:
            await send_to_robot({"type": "tilt", "deg": config.TILT_WAKE})
            if tracker is not None:
                tracker.note_pose(tilt=config.TILT_WAKE)
    if woke and not question:
        # Just "hey Rocky" — wait for the actual question.
        await say(lines["wake"])
        awake_until = time.time() + config.AWAKE_SECONDS
        return
    finished = await converse(question, ended_at, heard_at)
    while not finished:
        # He was cut off while thinking: wait for the rest of the sentence
        # and answer the whole thing once.
        item = await _next_heard()
        if item is None:
            print("  (didn't catch the rest — answering what I heard)")
            finished = await converse(question)
            continue
        text, started_at, ended_at, heard_at = item
        _, more = strip_wake_word(text)
        print(f"{config.HUMAN_NAME}: {text}")
        question = f"{question} {more}".strip()
        eyes.last_heard = question
        awake_until = time.time() + config.AWAKE_SECONDS
        finished = await converse(question, ended_at, heard_at)


async def console_loop() -> None:
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:  # EOF
            break
        if not await handle_console_line(line):
            break


async def main() -> None:
    global speaker_volume, face_pack
    logbook.install()  # the live view's Log tab
    saved = settings.apply_saved()  # the Settings tab's, over config.py and server/.env
    speaker_volume = saved.get("robot", {}).get("volume", speaker_volume)
    if config.CHARACTER != personality.CHARACTER.id:
        try:
            personality.use(config.CHARACTER)  # who it was when the brain stopped
        except ValueError as e:
            print(f"(character {config.CHARACTER!r}: {e}; staying {personality.CHARACTER.name})")
    else:
        personality.refresh()  # your name may be a setting
    face_pack = personality.face_pack()
    print(f"{config.ROBOT_NAME} brain server — model {config.MODEL}")
    print(f"listening for the robot on ws://0.0.0.0:{config.PORT}")
    eyes.state_provider = console_state
    eyes.command_handler = console_command
    eyes.serve(config.LIVE_VIEW_PORT, config.LIVE_VIEW_BIND)
    print(f"live view + controls: http://localhost:{config.LIVE_VIEW_PORT}/  (this computer only)")
    if not os.environ.get("ROBOT_TOKEN"):
        print("WARNING: ROBOT_TOKEN is not set in server/.env — the robot will be refused")
    global tracker, brain, main_loop
    loop = asyncio.get_running_loop()
    main_loop = loop
    brain = RobotBrain(ABILITIES)
    brain.situation = situation
    global body, dancer
    brain.memory = memories.prompt
    print(f"long-term memory: {len(memories.facts())} facts in {config.MEMORY_FILE}")
    print("web search:", "on (Anthropic, ~1 cent a question)" if searcher is not None else
          "off (needs Anthropic as LLM_BASE_URL and the anthropic package)")
    home = weather.home()
    print("home for the weather:", home or (f"{config.HOME_LOCATION!r} not found" if config.HOME_LOCATION
                                           else "not set (HOME_LOCATION in server/.env)"))
    tracker = Tracker(eyes, lambda p, t, on: loop.call_soon_threadsafe(head_moves.put_nowait, (p, t, on)))
    eyes.has_annotator = True
    tracker.start()
    body = Body(send_to_robot, tracker)
    body.allowed = lambda: (robot_socket is not None and not robot_asleep and time.time() < awake_until
                            and not tracker.enabled and not (dancer is not None and dancer.dancing))
    body.hush = lambda: ears is not None and (ears.hearing or ears.muted.is_set())  # you, or he, are talking
    dancer = Dancer(send_to_robot, tracker)
    dancer.ready = lambda: not (ears is not None and ears.muted.is_set())  # his line has been said
    dancer.before = _before_dancing
    print("face tracking:", "on" if config.TRACKING else f"off — say \"{config.ROBOT_NAME}, track me\" or type `track on`")
    print("type `ask <question>` to talk to the brain right now\n")
    async with websockets.serve(handle_robot, "0.0.0.0", config.PORT, max_size=256 * 1024):
        voice_task = asyncio.create_task(voice_loop())
        doze_task = asyncio.create_task(doze_loop())
        head_task = asyncio.create_task(head_loop())
        watch_task = asyncio.create_task(watch_loop())
        idle_task = asyncio.create_task(body.idle_loop())  # small head moves while he waits for you
        if config.LISTEN_ON_START:
            await start_listening()
        try:
            await console_loop()
        finally:
            voice_task.cancel()
            doze_task.cancel()
            head_task.cancel()
            watch_task.cancel()
            idle_task.cancel()
            if ears is not None:
                stop_listening()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
