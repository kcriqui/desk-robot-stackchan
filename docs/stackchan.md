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
| Neck | SG90 PWM servos, nods **down** from level | SCS0009 serial-bus servos, looks **up** from level (0 to 60°), turns ±90° |
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

## Tuning

- **Face tracking overshoots or lags.** `TRACK_HFOV` and `TRACK_VFOV` in
  `server/brain/config.py` (the `BODY == "stackchan"` block) are estimates
  for the GC0308 lens. Lower them if the head overshoots your face. If it
  turns away from you, flip `TRACK_PAN_SIGN`.
- **Camera picture upside down or mirrored.** `CAMERA_VFLIP` and
  `CAMERA_HMIRROR` in the firmware config.
- **Camera stream slow.** Compressing JPEGs in software is the costliest job
  on the robot. `snap` prints how long one frame takes to encode.
  `CAMERA_JPEG_QUALITY` trades size and time against detail.
- **He doesn't hear you, or clips.** `MIC_GAIN` in the firmware config, and
  `miclevel` in the serial console to watch the level.
- **Voice too quiet or harsh.** `volume` in either console. The speaker is
  small, so the tips in the main README's "Change the voice" section apply
  here too.

## Not yet confirmed on hardware

This port was written against M5Stack's StackChan BSP and M5Unified
sources, and builds cleanly. These points still need a real robot to
settle. If you're first, say how it went.

- Which way the yaw and pitch servos turn (`PAN_INVERT`, `TILT_INVERT`), and
  whether pitch 0 is level on every unit.
- That the head-touch sensor and LEDs keep working after the camera takes
  over the internal I2C bus. M5Stack's own camera code does the same thing.
- The GC0308's field of view, and how many fps software JPEG really reaches
  while WiFi and audio are busy.

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
