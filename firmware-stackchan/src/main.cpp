// desk-robot firmware for the M5Stack StackChan: the same face, voice, ears,
// eyes and brain link as firmware/, on a CoreS3 with serial-bus servos.
// Driven over USB serial (115200 baud, type `help`) and, when
// include/secrets.h exists, over WiFi by the brain (server/, BODY=stackchan).

#include <Arduino.h>
#include <M5StackChan.h>
#include <M5Unified.h>
#include <WiFi.h>

#include "camera.h"
#include "config.h"
#include "face.h"
#include "face_gfx.h"
#include "link.h"
#include "mic.h"
#include "servo_neck.h"
#include "speaker.h"

#if __has_include("secrets.h")
#include "secrets.h"
#define HAVE_BRAIN 1
#else
#define HAVE_BRAIN 0
#endif

FaceGfx gfx;
Face face(gfx);
ServoNeck panNeck;
ServoNeck tiltNeck;
Link brainLink;
Speaker speaker;
Mic mic;
Camera camera;
uint32_t nextTempMs = 0;
volatile bool speakDonePending = false;  // set by the speaker task, sent from loop()

uint32_t lastFrameMs = 0;
uint32_t nextStateMs = 0;
bool demoMode = true;  // cycles emotions on its own; off while the brain is connected
bool brainConnected = false;
bool glanceWanted = true;  // the brain's `glance on|off` (off while it holds a pose or tracks a face)

// Idle glances only while Rocky is awake and his brain is connected. Asleep,
// or with no server running, the head stays still.
void applyGlances() {
  bool on = brainConnected && !face.asleep() && glanceWanted;
  panNeck.setIdleGlances(on);
  tiltNeck.setIdleGlances(on);
}
uint32_t nextDemoEmotionMs = 0;
uint8_t demoEmotionIdx = 0;

void printHelp() {
  Serial.println(F("desk-robot (StackChan) commands:"));
  Serial.println(F("  emo <name>   neutral|happy|sad|angry|surprised|sleepy|thinking"));
  Serial.printf("  pan <deg>    turn head, %.0f..%.0f (0 = center, - = its left)\n", PAN_MIN_DEG, PAN_MAX_DEG);
  Serial.printf("  tilt <deg>   tilt head, %.0f (level)..%.0f (up)\n", TILT_MIN_DEG, TILT_MAX_DEG);
  Serial.printf("  center       head straight ahead, tilted %.0f up (rest pose)\n", TILT_REST_DEG);
  Serial.println(F("  raw pan|tilt <deg>  calibration move that ignores the limits (watch it!)"));
  Serial.println(F("  torque on|off  hold the head, or let it go limp to position by hand"));
  Serial.println(F("  zero         make the current head pose the new center (saved)"));
  Serial.println(F("  blink        blink now"));
  Serial.println(F("  sleep on|off eyes shut, breathing, Z's"));
  Serial.println(F("  demo on|off  idle life: auto blinks and emotion changes"));
  Serial.println(F("  glance on|off  allow idle head glances (only happen while the brain is connected and he is awake)"));
  Serial.println(F("  volume <0-1> speaker volume"));
  Serial.println(F("  beep         play a test tone through the speaker"));
  Serial.println(F("  mic on|off   stream the microphone to the brain"));
  Serial.println(F("  miclevel     print mic level for 3 s (talk to it)"));
  Serial.println(F("  stream on|off [fps]  stream camera JPEGs to the brain"));
  Serial.println(F("  snap         grab one frame and report its size"));
  Serial.println(F("  temp         chip temperature"));
  Serial.println(F("  battery      battery voltage and current"));
  Serial.println(F("  help         this text"));
}

void handleCommand(String line) {
  line.trim();
  if (line.isEmpty()) return;

  int space = line.indexOf(' ');
  String cmd = (space < 0) ? line : line.substring(0, space);
  String arg = (space < 0) ? String() : line.substring(space + 1);
  cmd.toLowerCase();
  arg.trim();

  if (cmd == "help") {
    printHelp();
  } else if (cmd == "emo") {
    Emotion e;
    if (emotionFromName(arg.c_str(), e)) {
      face.setEmotion(e);
      Serial.printf("emotion: %s\n", emotionName(e));
    } else {
      Serial.println(F("unknown emotion — try: neutral happy sad angry surprised sleepy thinking"));
    }
  } else if (cmd == "pan") {
    panNeck.setTarget(arg.toFloat());
    Serial.printf("pan -> %.0f deg\n", panNeck.target());
  } else if (cmd == "tilt") {
    tiltNeck.setTarget(arg.toFloat());
    Serial.printf("tilt -> %.0f deg\n", tiltNeck.target());
  } else if (cmd == "raw") {
    // raw pan|tilt <deg>: calibration, ignores the limits. Watch the mechanism!
    int sp = arg.indexOf(' ');
    String axis = sp < 0 ? arg : arg.substring(0, sp);
    float deg = sp < 0 ? 0 : arg.substring(sp + 1).toFloat();
    if (axis == "tilt") tiltNeck.setRaw(deg);
    else if (axis == "pan") panNeck.setRaw(deg);
    else { Serial.println(F("usage: raw pan|tilt <deg>")); return; }
    Serial.printf("raw %s -> %.0f deg (limits ignored)\n", axis.c_str(), deg);
  } else if (cmd == "center") {
    panNeck.setTarget(0);
    tiltNeck.setTarget(TILT_REST_DEG);
    Serial.printf("head -> center (tilt %.0f)\n", TILT_REST_DEG);
  } else if (cmd == "torque") {
    M5StackChan.Motion.setTorqueEnabled(arg != "off");
    Serial.printf("torque %s\n", arg == "off" ? "off — move the head by hand" : "on");
  } else if (cmd == "zero") {
    // Calibrate: `torque off`, hold the head straight and level, then `zero`.
    M5StackChan.Motion.setCurrentPostionAsHome();
    panNeck.setRaw(0);
    tiltNeck.setRaw(0);
    Serial.println(F("zero: this pose is now center/level (saved)"));
  } else if (cmd == "blink") {
    face.blink();
  } else if (cmd == "sleep") {
    face.setAsleep(arg == "on");
    applyGlances();
    Serial.printf("sleep %s\n", face.asleep() ? "on" : "off");
  } else if (cmd == "speak_begin") {
    speaker.beginSpeech(static_cast<size_t>(arg.toInt()));
  } else if (cmd == "speak_end") {
    speaker.endSpeech();
  } else if (cmd == "volume") {
    speaker.setVolume(arg.toFloat());
    Serial.printf("volume -> %.2f\n", arg.toFloat());
  } else if (cmd == "beep") {
    // 0.4 s of 440 Hz so the amp can be checked without the brain.
    static int16_t tone[16000 * 4 / 10];
    for (size_t i = 0; i < sizeof(tone) / sizeof(tone[0]); ++i) {
      tone[i] = static_cast<int16_t>(8000 * sinf(2 * PI * 440 * i / 16000.0f));
    }
    speaker.beginSpeech();
    speaker.feed(reinterpret_cast<uint8_t*>(tone), sizeof(tone));
    speaker.endSpeech();
    Serial.println(F("beep"));
  } else if (cmd == "mic") {
    mic.setStreaming(arg == "on");
    Serial.printf("mic %s\n", mic.streaming() ? "on" : "off");
  } else if (cmd == "miclevel") {
    for (int i = 0; i < 12; ++i) {
      delay(250);
      int bars = static_cast<int>(mic.level() * 200);
      Serial.printf("  level %.3f %.*s\n", mic.level(), min(bars, 40), "########################################");
    }
  } else if (cmd == "stream") {
    int sp = arg.indexOf(' ');
    String onoff = sp < 0 ? arg : arg.substring(0, sp);
    float fps = sp < 0 ? 10.0f : arg.substring(sp + 1).toFloat();
    camera.setStreaming(onoff == "on", fps);
    Serial.printf("stream %s @ %.1f fps (max %.0f)\n", camera.streaming() ? "on" : "off",
                  min(fps, CAMERA_MAX_FPS), CAMERA_MAX_FPS);
  } else if (cmd == "snap") {
    if (!camera.ok()) {
      Serial.println(F("camera not available"));
    } else {
      bool was = camera.streaming();
      camera.setStreaming(true, CAMERA_MAX_FPS);
      static uint8_t* jpg = static_cast<uint8_t*>(ps_malloc(64 * 1024));
      size_t n = 0;
      for (int i = 0; i < 80 && n == 0 && jpg; ++i) { delay(25); n = camera.takeFrame(jpg, 64 * 1024); }
      camera.setStreaming(was, CAMERA_MAX_FPS);
      Serial.printf("snap: %u bytes (%s), encode %u ms\n", n, n ? "ok" : "no frame",
                    camera.lastEncodeMs());
    }
  } else if (cmd == "temp") {
    Serial.printf("chip %.1f C\n", temperatureRead());
  } else if (cmd == "battery") {
    Serial.printf("battery %.2f V, %.0f mA\n", M5StackChan.getBatteryVoltage(),
                  M5StackChan.getBatteryCurrent() * 1000.0f);
  } else if (cmd == "glance") {
    glanceWanted = (arg == "on");
    applyGlances();
    Serial.printf("glance %s\n", glanceWanted ? "on" : "off");
  } else if (cmd == "demo") {
    demoMode = (arg == "on");
    face.setIdle(demoMode);
    Serial.printf("demo %s\n", demoMode ? "on" : "off");
  } else {
    Serial.println(F("unknown command — type `help`"));
  }
}

void setup() {
  Serial.begin(115200);
  // Drop console output rather than wait when USB is plugged into a computer
  // that isn't reading the port. Each print would otherwise block for up to
  // 100 ms; with the brain's head commands echoed several times a second,
  // that froze loop() for over a second and the brain dropped the link.
  Serial.setTxTimeoutMs(0);
  randomSeed(esp_random());

  // M5Unified, the IO expander (servo power, LEDs), head touch, servos.
  M5StackChan.begin();
  M5StackChan.Motion.setAutoTorqueReleaseEnabled(SERVO_AUTO_RELAX);
  // Tracking streams small moves; re-reading the servo before each one stutters.
  M5StackChan.Motion.setAutoAngleSyncEnabled(false);

  gfx.configure(FACE_COLOR, BACKGROUND_COLOR);
  face.begin();
  panNeck.begin(ServoNeck::Axis::Pan, PAN_MIN_DEG, PAN_MAX_DEG, PAN_SPEED,
                PAN_GLANCE_DEG, PAN_INVERT);
  tiltNeck.begin(ServoNeck::Axis::Tilt, TILT_MIN_DEG, TILT_MAX_DEG, TILT_SPEED,
                 TILT_GLANCE_DEG, TILT_INVERT, TILT_REST_DEG);

  mic.begin(MIC_GAIN);
  speaker.begin(&mic, SPEAKER_VOLUME, []() { speakDonePending = true; });
  // Last: it borrows the internal I2C bus the others set up on.
  if (camera.begin()) Serial.println(F("camera: ready"));

  demoMode = true;
  face.setIdle(true);
  applyGlances();  // off: no brain yet
  nextDemoEmotionMs = millis() + 8000;

  Serial.println(F("\ndesk-robot (StackChan) v0.3.0 — hello!"));
  printHelp();

#if HAVE_BRAIN
  brainLink.onAudio([](const uint8_t* pcm, size_t len) { speaker.feed(pcm, len); });
#ifndef ROBOT_TOKEN
#define ROBOT_TOKEN ""
#endif
  brainLink.begin(WIFI_SSID, WIFI_PASS, BRAIN_HOST, BRAIN_PORT, ROBOT_TOKEN,
             [](const String& cmd) { handleCommand(cmd); },
             [](bool connected) {
               // The brain picks emotions while it's connected; the demo
               // cycle takes over again if it goes away. Blinks stay on
               // either way; idle glances only while connected and awake.
               brainConnected = connected;
               demoMode = !connected;
               if (connected) {
                 face.setEmotion(Emotion::Happy);
               }
               applyGlances();
             });
#else
  Serial.println(F("no include/secrets.h — USB-only mode (see secrets.h.example)"));
#endif
}

void loop() {
  M5StackChan.update();

  // Serial console.
  static String lineBuf;
  while (Serial.available()) {
    char c = static_cast<char>(Serial.read());
    if (c == '\n' || c == '\r') {
      if (!lineBuf.isEmpty()) handleCommand(lineBuf);
      lineBuf = "";
    } else if (lineBuf.length() < 80) {
      lineBuf += c;
    }
  }

  // A pat on the head gets a blink.
  if (M5StackChan.TouchSensor.wasPressed()) face.blink();

  uint32_t now = millis();

#if HAVE_BRAIN
  brainLink.update(now);
  if (speakDonePending) {
    speakDonePending = false;
    brainLink.sendJson("{\"type\":\"speak_done\"}");
    Serial.printf("speech: %u underruns, prebuffered %u bytes in %u ms, wifi %d dBm\n",
                  speaker.lastUnderruns(), speaker.lastPrebuffered(),
                  speaker.lastPrebufferMs(), WiFi.RSSI());
  }
  {
    static uint8_t frame[Mic::FRAME_BYTES];
    while (brainLink.connected() && mic.nextFrame(frame)) {
      brainLink.sendBinary(0x01, frame, sizeof(frame));
    }
  }
  if (brainLink.connected() && camera.streaming()) {
    static uint8_t* jpg = static_cast<uint8_t*>(ps_malloc(64 * 1024));
    size_t n = jpg ? camera.takeFrame(jpg, 64 * 1024) : 0;
    if (n > 0) brainLink.sendBinary(0x02, jpg, n);
  }
  if (brainLink.connected() && now >= nextTempMs) {
    nextTempMs = now + 10000;
    brainLink.sendJson(String("{\"type\":\"temp\",\"c\":") + String(temperatureRead(), 1) + "}");
  }
  if (brainLink.connected() && now >= nextStateMs) {
    nextStateMs = now + 5000;
    brainLink.sendState(panNeck.current(), emotionName(face.emotion()));
  }
#endif

  // Demo mode: wander through emotions so a fresh flash shows everything.
  if (demoMode && now >= nextDemoEmotionMs) {
    static const Emotion cycle[] = {
        Emotion::Neutral, Emotion::Happy,     Emotion::Thinking,
        Emotion::Neutral, Emotion::Surprised, Emotion::Sleepy,
    };
    demoEmotionIdx = (demoEmotionIdx + 1) % (sizeof(cycle) / sizeof(cycle[0]));
    face.setEmotion(cycle[demoEmotionIdx]);
    nextDemoEmotionMs = now + random(6000, 12000);
  }

  if (now - lastFrameMs >= FRAME_INTERVAL_MS) {
    lastFrameMs = now;
    face.setTalking(speaker.speaking(), speaker.level());
    face.update(now);
    panNeck.update(now);
    tiltNeck.update(now);
  }
}
