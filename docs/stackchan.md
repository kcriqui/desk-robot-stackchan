# Running Rocky on an M5Stack StackChan

The [M5Stack StackChan](https://docs.m5stack.com/en/StackChan) already has
everything Rocky's body needs: a CoreS3 (ESP32-S3, 8 MB PSRAM), a 2" colour
screen, a camera, microphones, a speaker, and a two-servo neck. So there is
nothing to build or wire. Flash `firmware-stackchan/` onto it, tell the
brain which body it is driving, and Rocky moves in.

The brain (`server/`) is the same one that drives the original robot. Only
the firmware is different, and it speaks the same
[protocol](protocol.md).

## What changes from the original build

| | Original (`firmware/`) | StackChan (`firmware-stackchan/`) |
| --- | --- | --- |
| Face | 128x64 OLED | The same face, scaled 2.5x onto the 320x240 LCD |
| Neck | SG90 PWM servos, nods **down** from level | SCS0009 serial-bus servos, looks **up** from level (0 to 90°), rests 20° up, turns ±120° |
| Camera | OV2640, JPEG in hardware | GC0308, JPEG in software, capped at 8 fps |
| Mic / speaker | Separate I2S buses | One shared bus: the mic pauses while Rocky speaks (the brain mutes it then anyway) |
| Extras | | Pat his head and he blinks. `battery` reports the pack. |

Face tracking, the `look` ability, and Rocky's own idea of where his neck
can go all follow the body. Set `BODY=stackchan` and they flip from "down"
to "up".

## 1. Flash the firmware and test over USB

Flashing replaces the StackChan's factory firmware (the XiaoZhi assistant).
You can put that back any time with M5Burner.

1. Install PlatformIO (`pip install platformio`, or the VS Code
   extension). The first build downloads the ESP32 toolchain. That takes a
   few minutes, and only happens once. On Windows, run that first build
   from PowerShell or cmd, not Git Bash: the toolchain installer refuses to
   run under MSYS ("MSys/Mingw is not supported").
2. Plug the StackChan in over USB-C, then:

   ```bash
   cd firmware-stackchan
   pio run -t upload
   pio device monitor        # serial console at 115200
   ```

   If the upload can't find the board, hold the reset button until the
   green light flashes. That puts it in download mode.

3. The eyes should open, blink, and cycle through expressions on their own.
   The serial console has the same commands as the original (`help` lists
   them), plus three for this body:

   ```
   torque off           let the head go limp so you can move it by hand
   zero                 make the current pose "straight ahead and level" (saved)
   battery              battery voltage and current
   ```

4. Check the head's manners:
   - `pan -30` should turn the head to **its own** left, your right as you
     face it. If it goes the other way, flip `PAN_INVERT` in
     `firmware-stackchan/include/config.h`.
   - `tilt 30` should look up. If it looks down, flip `TILT_INVERT`.
   - If the head isn't straight and level at `center`:
     1. Type `torque off`.
     2. Hold the head straight and level.
     3. Type `zero`.

     The servo's factory calibration lives on the robot, and this updates it.
   - If the head sags when looking up, set `SERVO_AUTO_RELAX` to `false`
     so the servos keep holding.

   Re-flash after each config change.

Without a `secrets.h` the firmware stays in this USB-only mode.

## 2. Point the brain at it

Set the brain up as in the main [README](../README.md#set-up-the-brain-on-your-computer).
Then add one line to `server/.env`:

```
BODY=stackchan
```

Copy `firmware-stackchan/include/secrets.h.example` to `secrets.h` in the
same folder. Fill in your WiFi, the computer's address, and the same
`ROBOT_TOKEN`. Then flash again. The serial console shows
`wifi: connected` and then `brain: connected`.

Optional, in `server/.env`:

- `MIC_SOURCE=robot` listens only through the robot. The computer's mic is
  never opened, and nothing listens until the robot connects.
- `ROBOT_NAME=RockyChan` and `ROBOT_ALIASES=Rocky` give him a new wake word
  ("hey RockyChan", also heard as "Rocky Chan") and keep the old one. He
  stays Rocky in character, and knows what you call him.

## 3. Updating over WiFi

Once the robot runs firmware with an OTA password, later updates don't need
the USB cable.

1. In `firmware-stackchan/include/secrets.h`, set `OTA_PASSWORD` to a long
   random string, and optionally `OTA_HOSTNAME` (default `stackchan`).
   Flash over USB once. The console prints `ota: ready as <name>.local`.
2. Copy `firmware-stackchan/ota.ini.example` to `ota.ini` (git-ignored). Put
   the same password in `--auth=`, and `<name>.local` (or the robot's IP
   address) in `upload_port`.
3. From then on:

   ```bash
   pio run -e stackchan-ota -t upload
   ```

The robot restarts into the new firmware, and the brain takes it straight
back. If an update is interrupted, the robot keeps running the old firmware
(the 16 MB partition table has two app slots). Without `OTA_PASSWORD` the
firmware never listens for updates.

## Tuning

- **How face tracking works here.** Each camera frame carries the head's
  actual angle, read from the servos when the picture was taken. The
  tracker aims from that angle (`TRACK_POSE_GAIN`), so it doesn't have to
  wait for the head to settle between moves. On one robot it re-centres a
  face 20° off in about 1.2–1.4 s, where the original robot's method took
  2.6 s.
- **Face tracking overshoots or lags.** `TRACK_HFOV` and `TRACK_VFOV` in
  `server/brain/config.py` (the `BODY == "stackchan"` block) are estimates
  for the GC0308 lens. Lower them if the head overshoots your face, or lower
  `TRACK_POSE_GAIN`. If it turns away from you, flip `TRACK_PAN_SIGN`.
- **Tracking stops just short of your face.** The little servos stop up to
  about 4° short of a target. `TRACK_DEADBAND` (0.15 here, about ±4.5°) is
  how close counts as centred; much tighter than the servos can reach, and
  the head waits on a move that never comes.
- **Camera picture upside down or mirrored.** `CAMERA_VFLIP` and
  `CAMERA_HMIRROR` in the firmware config.
- **Camera stream slow.** Compressing JPEGs in software is the costliest job
  on the robot, and it caps the stream at about 8 fps at 320×240.
  `CAMERA_JPEG_QUALITY` trades size and time against detail.
- **Diagnostics.** Every 5 s the serial console prints a `diag:` line: free
  memory, the longest pause in each part of the main loop, camera grab +
  encode time, frames captured and sent, and WiFi signal. It's the first
  place to look if the link to the brain stalls. Don't use `snap` while the
  brain is connected: it pauses the robot for up to 2 s.
- **He doesn't hear you, or clips.** `MIC_GAIN` in the firmware config, and
  `miclevel` in the serial console to watch the level.
- **Voice too quiet or harsh.** `volume` in either console. The speaker is
  small, so the tips in the main README's "Change the voice" section apply
  here too.

## Checked on hardware

On one StackChan, over USB (2026-10-17), these all work as expected:

- the face, including every expression
- `pan`, `tilt` and `center` with the default direction flags
- `beep`, `miclevel` and `snap`
- `battery`
- the head-touch blink, which shows the internal I2C bus still works after
  the camera takes it over

## Not yet confirmed

These points still need trying. If you're first, say how it went.

- Whether pitch 0 is level on every unit, not just one.
- The GC0308's field of view.

## Known issues

- **Needing reset after an upload: very likely fixed.** Early firmware often
  seemed to stay dark after `pio run -t upload` until reset was pressed. The
  culprit was most likely the USB-serial stall (now fixed): with USB plugged
  in and no monitor open, every console print blocked for up to 100 ms, so
  boot dragged and the brain refused the late hello. Since the fix, 9 of 9
  uploads (4 through PlatformIO, 5 through esptool directly) came back by
  themselves, checked over WiFi without touching the serial port. esptool's
  `--after watchdog-reset` is no better here: it reboots fine (4/5) but
  reports every upload as failed, because the port vanishes mid-reset.
  If a board still stays dark, press reset once.

## Keeping up with the original

The StackChan port lives in its own folder, and the server changes are
small and only switch on with `BODY`. Pulling in improvements from
[cgro00/desk-robot](https://github.com/cgro00/desk-robot) is a normal merge:

```bash
git fetch upstream
git merge upstream/main
```

- **Face changes:** `firmware-stackchan/src/face.cpp` is the upstream file
  unchanged, and `link.*` too. If upstream changes them, copy them across.
  `face.h` differs only by the display type.
- **Protocol changes:** a new message in `docs/protocol.md` needs the same
  handling added in `firmware-stackchan/src/main.cpp`.
