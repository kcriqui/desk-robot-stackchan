# Robot ↔ brain WebSocket protocol (v0)

The robot connects to `ws://<mac-ip>:8765`. The first message must be a
`hello` carrying the shared `ROBOT_TOKEN` (server/.env, firmware secrets.h);
the server drops anything else without replying, and allows one robot at a
time. A wrong token costs a second; a token shorter than 16 characters (or
the example's `change-me`) is refused outright. If the robot connects again
while its old connection is still open (a reboot or a replug), the new one
replaces it when it comes from the same address or the old one has sent
nothing for 8 s; otherwise it is refused (1013). Plain `ws://` on the home LAN — no TLS in v1. Text frames are JSON. Binary
frames start with one type byte: `0x01` = mic audio, `0x02` = camera JPEG.
The rest of the frame is the payload.

## Robot → server

```json
{"type": "hello", "who": "desk-robot", "fw": "0.4.1", "token": "...", "wake": "computer"}  // must be the first message; token = ROBOT_TOKEN.
                                  // "wake": the wake word the robot can listen for itself (firmware-stackchan with a model)
                                  // "asleep": whether its eyes are shut, so a restarted brain keeps the state
{"type": "state", "pan": 12.5, "emotion": "neutral"}
{"type": "temp", "c": 52.0}       // chip temperature, sent every ~10 s
{"type": "battery", "v": 3.92, "ma": 410, "deep": false}  // firmware-stackchan: battery volts and mA (+ discharging,
                                  // - charging), every ~10 s; "deep" (0.5.1+): in deep sleep, also sent at once on a change;
                                  // 0.6.7+ also "lux", "prox" (0-2047) and "dark" from its light sensor
{"type": "speak_done", "dry_ms": 0, "wait_ms": 210, "next_ms": 200, "rssi": -58}
                                  // finished playing the last reply: how long the audio ran dry mid-reply,
                                  // how long it buffered before playing, the next reply's head start (ms),
                                  // and the WiFi signal (dBm). firmware-stackchan before 0.5.2 sends only the type.
{"type": "wake", "word": "computer"}  // heard its wake word while asleep: eyes open, and mic audio
                                  // follows, starting ~2 s before the word
{"type": "hush"}                  // 0.6.9.2+: a hand over its proximity sensor stopped it mid-reply (it has
                                  // stopped its speaker; the brain drops the rest). 0.6.7-0.6.9 sent "near"
                                  // instead, waking it
```

Binary `0x01` frames: microphone audio, 16 kHz mono signed 16-bit PCM,
little-endian, 30 ms (480 samples) per frame.

Binary `0x02` frames: one camera JPEG per frame, QVGA (320×240) by
default, sent continuously at `fps` while streaming is on. The server keeps
the latest frame for the live-view page, the face tracker, and the language model.

Binary `0x03` frames (firmware-stackchan): the same JPEG, tagged with where
the head pointed when it was taken. Payload: pan, then tilt, each a
little-endian int16 in tenths of a degree (head degrees, as in `pan`/`tilt`
below), then the JPEG. With the angle, the face tracker aims from where the
head really was instead of guessing, so it needn't wait for the head to
settle. The server takes either type.

## Server → robot

```json
{"type": "emotion", "name": "happy"}   // neutral happy sad angry surprised sleepy thinking; firmware-stackchan
                                  // 0.6.1+ also curious skeptical excited love confused
{"type": "pan", "deg": -20}
{"type": "tilt", "deg": 15}
{"type": "speak_begin", "bytes": 0}      // binary TTS audio frames follow; bytes = total if known, 0 = streaming (robot starts after 200 ms of audio)
{"type": "speak_end"}             // no more audio; robot replies speak_done when played out
{"type": "volume", "level": 0.4}  // speaker volume 0.0-1.0
{"type": "asleep", "on": true}    // eyes shut + Z's; false = wake up. firmware-stackchan also pauses the camera stream and dims the screen
                                  // (sent on connect too, so both sides agree)
{"type": "wake_word", "on": true} // robot-side waking: asleep, the mic sends nothing and the
                                  // robot listens for its wake word itself (sends "wake")
{"type": "mic", "on": true}       // stream the microphone to the server
{"type": "stream", "on": true, "fps": 10}   // start/stop the camera stream, set rate
{"type": "face", "save": true, "pack": {...}}  // firmware-stackchan 0.6.1+: a face pack (server/faces/*.json,
                                  // minus its description); save = keep it in flash. Sent on connect.
                                  // 0.6.2+ also draw its plate, socket, lens and pupil colour
{"type": "clock", "tz": "PST8PDT,M3.2.0,M11.1.0", "h24": false}  // firmware-stackchan 0.6.0+: the clock screen's
                                  // time zone (POSIX rule) and style; kept in flash. The robot gets the time
                                  // itself (SNTP) and keeps it in its clock chip. Sent on connect.
{"type": "weather", "temp": 65, "hi": 67, "lo": 54, "code": 2, "day": true, "unit": "F", "sky": "partly cloudy"}
                                  // 0.6.0+: the weather at home for the clock screen (WMO code), on connect
                                  // and every 30 min; the screen drops it after 3 h without an update.
{"type": "weather", "none": true}  // 0.6.3+: no weather on the clock screen (switched off in Settings)
{"type": "screen", "face": 0, "clock": 0, "night": 1, "dark_lux": 3, "night_pct": 10, "idle": 0,
 "near": true, "near_level": 60}
                                  // firmware-stackchan 0.6.7+: brightness in percent, awake (0 = the
                                  // display's default) and on the clock (0 = as awake); in a dark room
                                  // (light sensor under dark_lux) the clock dims to night_pct (night 1) or
                                  // goes dark (2); 0 = off. near: a hand at its sensor (proximity
                                  // near_level or more, 0-2047) stops it talking. Kept in flash. Sent on connect.
                                  // (0.6.3-0.6.6 took n0 / n1, a night from / until in minutes.)
                                  // 0.6.9.5+ also "led" (the light bars' brightness, percent, 0 = off) and
                                  // "led_sleep" (asleep: 0 off, 1 a scanner); 0.6.9.18+ "bank" (true: on a USB
                                  // power bank, so it sleeps deeper on USB too)
{"type": "leds", "alert": "timer"}  // firmware-stackchan 0.6.9.5+: the light bars: "alert" timer | battery |
                                  // hot | hush (a flash or pulses), or "dance": true | false (a rainbow that
                                  // jumps on each head move until false, or 60 s), or "test": true (one LED
                                  // at a time, 0 to 11). Otherwise they follow its face and voice by themselves.
                                  // 0.6.4+ also "idle": what it shows while it sleeps on USB: 0 the
                                  // clock, 1 Pong, 2 Tetris, 3 Frogger, 4 roam (the sleeping face, the head looking around),
                                  // 5 Conway's Game of Life (0.6.5+). In the dark a game dims with night mode
                                  // (0.6.9.3+; before, the clock took over); roam gives way to the clock.
                                  // Asleep for 5 s (not in deep sleep), the robot shows the clock screen.
{"type": "power_off", "why": "idle"}  // firmware-stackchan 0.5.0+: power off ("idle" or "battery"); the
                                  // server says goodbye first. Only the power button turns it back on.
                                  // On battery the robot also sleeps deeper by itself after 5 min asleep
                                  // (screen off, WiFi power saving, servo power off), and powers off
                                  // on its own with no server for 30 min or a nearly flat battery.
```

The server sends `stream on` at `CAMERA_FPS` when the robot connects. The
live view is served by `server/brain/eyes.py` at http://localhost:8766/.

Binary frames: type byte `0x01` then TTS audio for the speaker, same
PCM format as above, ~100 ms per frame. The server streams each reply as
the voice generates it (`bytes: 0`); the robot starts playing once it has a
head start buffered (firmware-stackchan 0.5.2+: 400 ms after power-on, up to
600 ms after replies that ran dry, settling to 200 ms after clean ones;
before that, always 200 ms) and keeps a 32 s ring buffer in PSRAM. The server
waits for `speak_done` before it un-mutes the microphone.

Firmware side: `firmware/src/link.cpp` maps each server message onto the
same text commands the USB console uses (`emo`, `pan`, `tilt`), so both
paths behave identically. The robot sends `state` every 5 s.

Keep this file in sync with `firmware/src/link.cpp` and `server/brain/main.py`
whenever a message type is added.
