// desk-robot firmware for the M5Stack StackChan: the same face, voice, ears,
// eyes and brain link as firmware/, on a CoreS3 with serial-bus servos.
// Driven over USB serial (115200 baud, type `help`) and, when
// include/secrets.h exists, over WiFi by the brain (server/, BODY=stackchan).

#include <Arduino.h>
#include <M5StackChan.h>
#include <Preferences.h>
#include <M5Unified.h>
#include <WiFi.h>

#include "camera.h"
#include "clock_screen.h"
#include "config.h"
#include "face.h"
#include "face_gfx.h"
#include "idle_games.h"
#include "leds.h"
#include "light.h"
#include "link.h"
#include "mic.h"
#include "wake.h"
#include "servo_neck.h"
#include "speaker.h"
#include "touch_gestures.h"

#if __has_include("secrets.h")
#include "secrets.h"
#define HAVE_BRAIN 1
#else
#define HAVE_BRAIN 0
#endif

// Updates over WiFi only when secrets.h sets a password.
#if HAVE_BRAIN && defined(OTA_PASSWORD)
#include <ArduinoOTA.h>
#define HAVE_OTA 1
#ifndef OTA_HOSTNAME
#define OTA_HOSTNAME "stackchan"
#endif
#else
#define HAVE_OTA 0
#endif

FaceGfx gfx;
Face face(gfx);
ServoNeck panNeck;
ServoNeck tiltNeck;
Link brainLink;
TouchGestures touch;  // the head's touch strip, as gestures for the brain
Speaker speaker;
Mic mic;
Wake wake;
Camera camera;
ClockScreen clockScreen;
// What the screen shows while it sleeps (on USB: on battery deep sleep turns it off): after a few seconds of the
// sleeping face, the clock, an idle game, or Roam (still the sleeping face, the head looking round the room). In a
// dark room the clock and the games dim (or go dark) with night mode, and Roam gives way to the clock so the head
// doesn't whir about in the dark. The brain picks one (Settings, the `screen` message).
enum class Shown : uint8_t { Face, Clock, Game, Roam };
Shown shown = Shown::Face;
IdleMode idleMode = IdleMode::Clock;
uint32_t nextGameMs = 0;
constexpr uint32_t GAME_FRAME_MS = 66;  // ~15 frames a second, as on V2
uint32_t nextStatusMs = 0;
uint32_t nextTempMs = 0;
volatile bool speakDonePending = false;  // set by the speaker task, sent from loop()

uint32_t lastFrameMs = 0;
uint32_t nextStateMs = 0;
bool demoMode = true;  // cycles emotions on its own; off while the brain is connected
bool brainConnected = false;

// Every 5 s on serial: memory, the longest pause in each part of loop(), and
// frames captured vs sent. For finding what stalls the link to the brain.
struct Diag {
  uint32_t loop = 0, link = 0, send = 0, face = 0, neck = 0;  // worst, microseconds
  uint32_t framesSent = 0;
} diag;
uint32_t nextDiagMs = 5000;
uint32_t lastCaptured = 0;

void printDiag(uint32_t nowMs) {
  if (nowMs < nextDiagMs) return;
  nextDiagMs = nowMs + 5000;
  uint32_t captured = camera.framesCaptured();
  float encAvg = 0;
  uint32_t encMax = 0;
  camera.takeEncodeStats(encAvg, encMax);
  Serial.printf("diag: heap %u KB (min %u, block %u) psram %u KB | worst ms: loop %u link %u send %u face %u neck %u"
                " | cam %.1f fps (grab+encode avg %.0f ms, max %u) sent %.1f fps | wifi %d dBm %s\n",
                heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024,
                heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL) / 1024,
                heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL) / 1024,
                heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024,
                diag.loop / 1000, diag.link / 1000, diag.send / 1000, diag.face / 1000, diag.neck / 1000,
                (captured - lastCaptured) / 5.0f, encAvg, encMax, diag.framesSent / 5.0f, WiFi.RSSI(),
                brainLink.connected() ? "brain up" : "brain DOWN");
  lastCaptured = captured;
  diag = Diag{};
}

// Asleep, he saves power and heat: the camera stops streaming (its frames are
// compressed in software, the biggest load) and the screen dims. What the
// brain asks of the camera meanwhile is remembered for when he wakes. The
// servos already let go at rest (SERVO_AUTO_RELAX).
bool streamWanted = false;
float streamFps = 10.0f;

// The mic, and the wake word on the robot. The brain turns the mic on, and
// turns on robot-side waking ("wake on") when it can use it. Then, asleep,
// the mic sends nothing: the wake engine listens instead, and on the wake
// word the robot opens his eyes and streams again, starting with the 2 s
// before it so the brain hears the whole request.
bool micWanted = false;
bool hearing = false;    // the brain hears speech (its `hearing` message); dropped after HEARING_TIMEOUT_MS
uint32_t hearingAt = 0;
bool wakeOnRobot = false;

void applyMic() {
  bool sleeping = wakeOnRobot && face.asleep();
  wake.setListening(sleeping && brainConnected);
  mic.setStreaming(micWanted && !sleeping);
}
uint8_t awakeBrightness = 0;

// Screen brightness, the clock screen at night, and a hand to stop it talking: the brain's `screen` message (its Settings
// tab), kept in flash so they hold with the brain away. Percents; face 0 = the display's own default, clock 0 = as
// bright as awake. "Night" is the room going dark (the light sensor), not a time of day.
struct ScreenSettings {
  uint8_t face = 0, clock = 0;
  uint8_t night = 0;          // 0 off, 1 dim, 2 dark (the screen off)
  uint8_t nightPct = 10;
  uint16_t darkLux = 3;       // the room is dark below this (a lit room reads ~15 through the glass)
  uint8_t idle = 0;           // IdleMode: 0 clock, 1 pong, 2 tetris, 3 frogger, 4 roam, 5 life
  uint8_t near = 1;           // a hand at the sensor stops it mid-reply
  uint16_t nearLevel = 60;    // proximity reading (0-2047) that counts as near (nothing near: 0-5)
  uint8_t led = 30;           // the light bars' brightness, percent; 0 = off
  uint8_t ledSleep = 0;       // asleep: 0 off, 1 a scanner
  uint8_t powerBank = 0;      // on a USB power bank: deep sleep on USB too (the brain's robot.power_bank)
} screenCfg;
LightSensor light;
Leds leds;
bool roomDark = false;
uint32_t darkSinceMs = 0, lightSinceMs = 0;

// Dark once the light has stayed under darkLux for 5 s; light again once it's over twice that for 2 s. The gap
// keeps the clock's own glow from flicking it back and forth.
void updateDark(uint32_t now) {
  if (!light.ok()) {
    roomDark = false;
    return;
  }
  float lx = light.lux();
  if (lx < screenCfg.darkLux) {
    lightSinceMs = 0;
    if (!darkSinceMs) darkSinceMs = now | 1;
    if (!roomDark && now - darkSinceMs >= 5000) roomDark = true;
  } else if (lx > screenCfg.darkLux * 2.0f + 2) {
    darkSinceMs = 0;
    if (!lightSinceMs) lightSinceMs = now | 1;
    if (roomDark && now - lightSinceMs >= 2000) roomDark = false;
  } else {
    darkSinceMs = lightSinceMs = 0;  // in between: stay as it is
  }
}
uint8_t defaultBrightness = 0;   // the display's own, at boot
uint8_t shownClockBrightness = 255;  // what the clock screen was last given
uint32_t nextClockBrightMs = 0;

uint8_t fromPct(uint8_t p) { return static_cast<uint8_t>(lroundf(constrain(p, 0, 100) * 2.55f)); }

// The clock screen's brightness now: night's if the room is dark, else the clock's own, else as bright as awake
// (but readable: at least 96).
bool isNight() { return screenCfg.night && roomDark; }

uint8_t clockBrightness() {
  if (isNight()) return screenCfg.night == 2 ? 0 : max(fromPct(screenCfg.nightPct), (uint8_t)1);
  return screenCfg.clock ? fromPct(screenCfg.clock) : max(awakeBrightness, (uint8_t)96);
}

void loadScreen() {
  Preferences p;
  if (!p.begin("screen", true)) return;
  screenCfg.face = p.getUChar("face", screenCfg.face);
  screenCfg.clock = p.getUChar("clock", screenCfg.clock);
  screenCfg.night = p.getUChar("night", screenCfg.night);
  screenCfg.nightPct = p.getUChar("nightPct", screenCfg.nightPct);
  screenCfg.darkLux = p.getUShort("darkLux", screenCfg.darkLux);
  screenCfg.idle = p.getUChar("idle", screenCfg.idle);
  screenCfg.near = p.getUChar("near", screenCfg.near);
  screenCfg.nearLevel = p.getUShort("nearLvl", screenCfg.nearLevel);
  screenCfg.led = p.getUChar("led", screenCfg.led);
  screenCfg.ledSleep = p.getUChar("ledSleep", screenCfg.ledSleep);
  screenCfg.powerBank = p.getUChar("bank", screenCfg.powerBank);
  p.end();
}

void saveScreen() {
  Preferences p;
  if (!p.begin("screen", false)) return;
  p.putUChar("face", screenCfg.face);
  p.putUChar("clock", screenCfg.clock);
  p.putUChar("night", screenCfg.night);
  p.putUChar("nightPct", screenCfg.nightPct);
  p.putUShort("darkLux", screenCfg.darkLux);
  p.putUChar("idle", screenCfg.idle);
  p.putUChar("near", screenCfg.near);
  p.putUShort("nearLvl", screenCfg.nearLevel);
  p.putUChar("led", screenCfg.led);
  p.putUChar("ledSleep", screenCfg.ledSleep);
  p.putUChar("bank", screenCfg.powerBank);
  p.end();
}

IdleGames games(gfx);
extern float batteryVolts, batteryMa;  // (below) the body's INA226

// The charge from the resting voltage (the brain's battery.py curve, for one Li-ion cell).
int batteryPercent(float v) {
  static const float V[] = {3.30f, 3.50f, 3.61f, 3.69f, 3.74f, 3.77f, 3.80f, 3.84f, 3.90f, 3.96f, 4.05f, 4.15f};
  static const int P[] = {0, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100};
  if (v <= V[0]) return 0;
  for (int i = 1; i < 12; ++i)
    if (v <= V[i]) return lroundf(P[i - 1] + (P[i] - P[i - 1]) * (v - V[i - 1]) / (V[i] - V[i - 1]));
  return 100;
}

// The WiFi and battery for the clock screen's corners.
void updateStatus() {
  int8_t bars = -1;
  if (WiFi.status() == WL_CONNECTED) {
    int r = WiFi.RSSI();
    bars = r > -55 ? 4 : r > -65 ? 3 : r > -75 ? 2 : r > -85 ? 1 : 0;
  }
  uint8_t power = batteryMa > ON_BATTERY_MA ? 0 : batteryMa < -ON_BATTERY_MA ? 1 : 2;
  clockScreen.setStatus(bars, batteryVolts > 0 ? batteryPercent(batteryVolts) : -1, batteryVolts > 0 ? power : 255);
}

// Roam: while it sleeps by day, the head looks round the room every few seconds, slowly, with the sleeping face
// (dimmed, Z's) still up (from V2's roamUpdate, whose face woke up for it). A glance from where it is, back toward
// the middle, or anywhere across the room.
struct {
  uint32_t at = 0;
  bool active = false;
} roam;
constexpr int ROAM_PAN_SPEED = 260, ROAM_TILT_SPEED = 220;

void roamStart(uint32_t now) {
  roam.active = true;
  roam.at = now + 1500;
  panNeck.setSpeed(ROAM_PAN_SPEED);
  tiltNeck.setSpeed(ROAM_TILT_SPEED);
}

void roamStop() {
  if (!roam.active) return;
  roam.active = false;
  panNeck.setSpeed(PAN_SPEED);
  tiltNeck.setSpeed(TILT_SPEED);
  panNeck.setTarget(0);
  tiltNeck.setTarget(TILT_REST_DEG);
}

void roamUpdate(uint32_t now) {
  if (!roam.active || static_cast<int32_t>(now - roam.at) < 0) return;
  float pan0 = panNeck.target();
  int kind = random(100);
  float pan = kind < 25 ? pan0 + random(15, 36) * (random(2) ? 1 : -1) : kind < 40 ? random(-10, 11)
                                                                       : random(-60, 61);
  pan = constrain(pan, -60.0f, 60.0f);  // round the desk, not behind itself
  float tilt = constrain(TILT_REST_DEG + random(-15, 16), TILT_MIN_DEG, TILT_REST_DEG + 25.0f);
  panNeck.setTarget(pan);
  tiltNeck.setTarget(tilt);
  roam.at = now + random(4000, 9001);
}

void printScreen() {
  static const char* kNight[] = {"off", "dim", "dark"};
  static const char* kIdle[] = {"clock", "pong", "tetris", "frogger", "roam", "life"};
  Serial.printf("screen: face %u%% (0 = default), clock %u%% (0 = as awake), night %s below %u lux at %u%% "
                "(room %s), idle %s, a hand stops it talking %s (at %u), power bank %s\n", screenCfg.face, screenCfg.clock,
                kNight[min(screenCfg.night, (uint8_t)2)], screenCfg.darkLux, screenCfg.nightPct,
                roomDark ? "dark" : "light", kIdle[min(screenCfg.idle, (uint8_t)5)], screenCfg.near ? "on" : "off",
                screenCfg.nearLevel, screenCfg.powerBank ? "yes" : "no");
  light.printStatus();
}

// Power on battery (config.h). Asleep on battery for DEEP_SLEEP_AFTER_MS, he
// sleeps deeper: screen off, WiFi power saving, servo power off. The wake
// word keeps listening. Powering off is the brain's call (it knows about
// reminders, and says goodbye); the robot only does it itself when the brain
// is gone or the battery is all but flat. Only the power button (and maybe
// plugging in USB) turns him back on: the CoreS3's clock can't. On a USB power bank (a setting) it also
// sleeps deeper on USB, to make the pack last.
float batteryVolts = 0, batteryMa = 0;  // the body's INA226, read every 10 s
uint32_t nextBatteryMs = 0;
uint32_t asleepSinceMs = 0;   // when his eyes shut; 0 while awake
uint32_t orphanSinceMs = 0;   // when the brain link went away; 0 while connected
uint8_t lowVoltReadings = 0;  // in a row, at or below POWER_OFF_VOLTS
bool deepSleep = false;

bool onBattery() { return batteryMa > ON_BATTERY_MA; }

uint32_t servoOffAtMs = 0;  // deep sleep: when to cut the servos' power (the head is going down first); 0 = not pending
static const uint32_t SERVO_OFF_DELAY_MS = 1500;
static const uint32_t SERVO_WAKE_MS = 600;  // power back on: the servos answer on the bus after this

void checkServoOff(uint32_t now) {
  if (servoOffAtMs && deepSleep && static_cast<int32_t>(now - servoOffAtMs) >= 0) {
    servoOffAtMs = 0;
    M5StackChan.setServoPowerEnabled(false);
  }
}

void setDeepSleep(bool on) {
  if (on == deepSleep) return;
  deepSleep = on;
  if (on) {
    M5.Display.setBrightness(0);
    M5.Display.sleep();
    WiFi.setSleep(true);  // modem sleep: asleep, he sends nothing but a reading every 10 s
    // Head to the middle and down as far as it goes before the servos lose power (it would droop anyway);
    // checkServoOff() cuts it once the move is done.
    panNeck.setRaw(0);
    tiltNeck.setRaw(TILT_MIN_DEG);
    panNeck.update(millis());
    tiltNeck.update(millis());
    servoOffAtMs = (millis() + SERVO_OFF_DELAY_MS) | 1;
  } else {
    servoOffAtMs = 0;
    M5StackChan.setServoPowerEnabled(true);
    panNeck.resync(millis(), SERVO_WAKE_MS);  // the head may have drooped while they were off
    tiltNeck.resync(millis(), SERVO_WAKE_MS);
    WiFi.setSleep(false);
    M5.Display.wakeup();
    M5.Display.setBrightness(face.asleep() ? max(awakeBrightness / 4, 8) : awakeBrightness);
  }
  Serial.printf("power: deep sleep %s (%.2f V, %.0f mA)\n", on ? "on" : "off", batteryVolts, batteryMa);
  nextTempMs = 0;  // tell the brain now (the battery report carries "deep")
}

// Shows why, waits for any speech to finish, and cuts the power.
static const uint32_t SEND_SETTLE_MS = 50;  // past ServoNeck's minimum gap between moves

void powerOff(const String& why) {
  Serial.printf("power: off (%s)\n", why.c_str());
  leds.setBrightness(0);  // dark before the power goes (they'd keep their last colour)
  M5StackChan.showRgbColor(0, 0, 0);
  setDeepSleep(false);
  // Head to the middle and down as far as it goes, as when it falls asleep: with the power gone it would droop.
  // The loop (which sends moves) is stopped from here on, so send them now; the BSP's own task carries them out
  // during the goodbye and the screen below.
  panNeck.setTarget(0);
  tiltNeck.setTarget(TILT_MIN_DEG);
  for (uint32_t waited = 0; waited <= SERVO_WAKE_MS + SEND_SETTLE_MS; waited += SEND_SETTLE_MS) {
    delay(SEND_SETTLE_MS);  // out of deep sleep, the servos need a moment before they take a move
    panNeck.update(millis());
    tiltNeck.update(millis());
  }
  for (int i = 0; i < 100 && speaker.speaking(); ++i) delay(100);  // the goodbye, if still playing
  M5.Display.setBrightness(max(awakeBrightness, (uint8_t)64));
  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.setTextDatum(middle_center);
  M5.Display.setFont(&fonts::FreeSans12pt7b);
  M5.Display.drawString("Powering off", M5.Display.width() / 2, M5.Display.height() / 2 - 30);
  M5.Display.setFont(&fonts::FreeSans9pt7b);
  M5.Display.drawString(why == "battery" ? "Battery almost empty" : why == "asked" ? "Goodnight"
                                                                    : "to save the battery",
                        M5.Display.width() / 2, M5.Display.height() / 2 + 5);
  M5.Display.drawString("Press the power button to wake me", M5.Display.width() / 2,
                        M5.Display.height() / 2 + 35);
  delay(4000);
  M5.Power.powerOff();
}

void applyStream() {
  camera.setStreaming(streamWanted && !face.asleep(), streamFps);
}

void setAsleep(bool on) {
  if (!on) setDeepSleep(false);
  if (!on) roamStop();
  if (on != face.asleep()) {
    asleepSinceMs = on ? (millis() | 1) : 0;  // never 0 while asleep
    if (on) {
      M5.Display.setBrightness(max(awakeBrightness / 4, 8));
    } else {
      M5.Display.setBrightness(awakeBrightness);
    }
  }
  face.setAsleep(on);
  applyStream();
  applyMic();
}
uint32_t nextDemoEmotionMs = 0;
uint8_t demoEmotionIdx = 0;

void printHelp() {
  Serial.println(F("desk-robot (StackChan) commands:"));
  Serial.println(F("  emo <name>   neutral happy sad angry surprised sleepy thinking"));
  Serial.println(F("               curious skeptical excited love confused"));
  Serial.println(F("  still <name>|off  hold one emotion still, to look at it (off: back to life)"));
  Serial.printf("  pan <deg>    turn head, %.0f..%.0f (0 = center, - = its left)\n", PAN_MIN_DEG, PAN_MAX_DEG);
  Serial.printf("  tilt <deg>   tilt head, %.0f..%.0f (0 = level, 90 = straight up)\n", TILT_MIN_DEG, TILT_MAX_DEG);
  Serial.printf("  center       head straight ahead, tilted %.0f up (rest pose)\n", TILT_REST_DEG);
  Serial.println(F("  raw pan|tilt <deg>  calibration move that ignores the limits (watch it!)"));
  Serial.println(F("  speed <pan> [tilt]  neck speed 0-1000 until reboot (defaults in config.h)"));
  Serial.println(F("  torque on|off  hold the head, or let it go limp to position by hand"));
  Serial.println(F("  zero         make the current head pose the new center (saved)"));
  Serial.println(F("  blink        blink now"));
  Serial.println(F("  sleep on|off eyes shut, breathing, Z's"));
  Serial.println(F("  demo on|off  idle life: auto blinks and emotion changes"));
  Serial.println(F("  volume <0-1> speaker volume"));
  Serial.println(F("  beep         play a test tone through the speaker"));
  Serial.println(F("  mic on|off   stream the microphone to the brain"));
  Serial.println(F("  miclevel     print mic level for 3 s (talk to it)"));
  Serial.println(F("  stream on|off [fps]  stream camera JPEGs to the brain"));
  Serial.println(F("  snap         grab one frame and report its size"));
  Serial.println(F("  temp         chip temperature"));
  Serial.println(F("  battery      battery voltage and current"));
  Serial.println(F("  power        battery, deep sleep and power-off state"));
  Serial.println(F("  deep now     asleep on battery: sleep deeper now, not after 5 min"));
  Serial.println(F("  poweroff     power off now (the power button turns him back on)"));
  Serial.println(F("  clock [tz [12|24]]  the clock screen's time zone (POSIX rule), or its status"));
  Serial.println(F("  weather <temp> <hi> <lo> <code> <day 0|1> <F|C> <words>  the clock screen's weather"));
  Serial.println(F("  light        the light sensor: lux and proximity"));
  Serial.println(F("  leds test    the light bars one LED at a time, 0 to 11 (also: dance on|off, alert ...)"));
  Serial.println(F("  screen [...] brightness, night, idle screen and hand-to-stop settings (from the brain)"));
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
      Serial.println(F("unknown emotion — try: neutral happy sad angry surprised sleepy thinking curious "
                       "skeptical excited love confused"));
    }
  } else if (cmd == "pan") {
    if (leds.dancing()) leds.beat();
    panNeck.setTarget(arg.toFloat());
    Serial.printf("pan -> %.0f deg\n", panNeck.target());
  } else if (cmd == "tilt") {
    if (leds.dancing()) leds.beat();
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
  } else if (cmd == "speed") {
    // speed <pan> [tilt]: neck speed, 0-1000, until the next reboot. Find a
    // value you like, then set PAN_SPEED / TILT_SPEED in config.h.
    int sp = arg.indexOf(' ');
    if (!arg.isEmpty()) {
      panNeck.setSpeed(arg.toInt());
      tiltNeck.setSpeed(sp < 0 ? arg.toInt() : arg.substring(sp + 1).toInt());
    }
    Serial.printf("speed: pan %d, tilt %d\n", panNeck.speed(), tiltNeck.speed());
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
    setAsleep(arg == "on");
    Serial.printf("sleep %s (camera %s)\n", face.asleep() ? "on" : "off", camera.streaming() ? "on" : "off");
  } else if (cmd == "speak_begin") {
    setDeepSleep(false);
    speaker.beginSpeech(static_cast<size_t>(arg.toInt()));
  } else if (cmd == "speak_end") {
    speaker.endSpeech();
  } else if (cmd == "volume") {
    speaker.setVolume(arg.toFloat());
    Serial.printf("volume -> %.2f\n", arg.toFloat());
  } else if (cmd == "beep") {
    // 0.4 s of 440 Hz so the amp can be checked without the brain (feed() copies it).
    const size_t n = 16000 * 4 / 10;
    int16_t* tone = static_cast<int16_t*>(heap_caps_malloc(n * sizeof(int16_t), MALLOC_CAP_SPIRAM));
    if (tone == nullptr) return;
    for (size_t i = 0; i < n; ++i) tone[i] = static_cast<int16_t>(8000 * sinf(2 * PI * 440 * i / 16000.0f));
    speaker.beginSpeech();
    speaker.feed(reinterpret_cast<uint8_t*>(tone), n * sizeof(int16_t));
    speaker.endSpeech();
    heap_caps_free(tone);
    Serial.println(F("beep"));
  } else if (cmd == "mic") {
    micWanted = (arg == "on");
    applyMic();
    Serial.printf("mic %s%s\n", micWanted ? "on" : "off",
                  micWanted && !mic.streaming() ? " (asleep: the wake word engine listens)" : "");
  } else if (cmd == "wake") {
    // wake on|off: robot-side waking (the brain sends this); wake <0-1>: threshold
    if (arg == "on" || arg == "off") {
      wakeOnRobot = (arg == "on") && wake.start();
      applyMic();
    } else if (!arg.isEmpty() && wake.ready()) {
      wake.setThreshold(arg.toFloat());
    }
    Serial.printf("wake: %s, \"%s\" %s, %lu detections (engine: %s)\n", wakeOnRobot ? "on" : "off",
                  wake.word(), wake.listening() ? "listening" : "idle", (unsigned long)wake.count(),
                  wake.status());
  } else if (cmd == "miclevel") {
    for (int i = 0; i < 12; ++i) {
      delay(250);
      int bars = static_cast<int>(mic.level() * 200);
      Serial.printf("  level %.3f %.*s\n", mic.level(), min(bars, 40), "########################################");
    }
  } else if (cmd == "stream") {
    int sp = arg.indexOf(' ');
    String onoff = sp < 0 ? arg : arg.substring(0, sp);
    streamWanted = onoff == "on";
    streamFps = sp < 0 ? 10.0f : arg.substring(sp + 1).toFloat();
    applyStream();  // not while asleep; it starts on waking
    Serial.printf("stream %s @ %.1f fps (max %.0f)%s\n", camera.streaming() ? "on" : "off",
                  min(streamFps, CAMERA_MAX_FPS), CAMERA_MAX_FPS,
                  streamWanted && face.asleep() ? ", once he wakes" : "");
  } else if (cmd == "snap") {
    if (!camera.ok()) {
      Serial.println(F("camera not available"));
    } else {
      camera.setStreaming(true, CAMERA_MAX_FPS);
      static uint8_t* jpg = static_cast<uint8_t*>(ps_malloc(64 * 1024));
      size_t n = 0;
      for (int i = 0; i < 80 && n == 0 && jpg; ++i) { delay(25); n = camera.takeFrame(jpg, 64 * 1024); }
      applyStream();  // back to what the brain wants (and off while asleep)
      Serial.printf("snap: %u bytes (%s); encode time is on the diag line\n", n, n ? "ok" : "no frame");
    }
  } else if (cmd == "temp") {
    Serial.printf("chip %.1f C\n", temperatureRead());
  } else if (cmd == "battery") {
    Serial.printf("battery %.2f V, %.0f mA\n", M5StackChan.getBatteryVoltage(),
                  M5StackChan.getBatteryCurrent() * 1000.0f);
  } else if (cmd == "power") {
    uint32_t now = millis();
    Serial.printf("power: %.2f V, %.0f mA (%s), %s, deep sleep %s, brain %s\n", batteryVolts, batteryMa,
                  onBattery() ? "on battery" : "USB", face.asleep() ? "asleep" : "awake",
                  deepSleep ? "on" : "off", brainLink.connected() ? "connected" : "gone");
    if (asleepSinceMs) Serial.printf("  asleep %lu s\n", (unsigned long)((now - asleepSinceMs) / 1000));
    if (orphanSinceMs) Serial.printf("  no brain for %lu s\n", (unsigned long)((now - orphanSinceMs) / 1000));
  } else if (cmd == "deep") {
    if (arg == "now" && face.asleep() && asleepSinceMs) asleepSinceMs = (millis() - DEEP_SLEEP_AFTER_MS) | 1;
    else if (arg == "off") setDeepSleep(false);
    Serial.printf("deep sleep: %s (asleep on battery only)\n", deepSleep || arg == "now" ? "on" : "off");
  } else if (cmd == "facepack" || cmd == "facepreview") {
    // From the brain's `face` message: a face pack, kept in flash unless it's a preview.
    if (face.applyPackJson(arg.c_str(), cmd == "facepack")) {
      Serial.printf("face pack: %s%s\n", face.packName(), cmd == "facepack" ? "" : " (preview)");
    } else {
      Serial.println(F("face pack: not valid JSON, kept the old one"));
    }
  } else if (cmd == "still") {
    Emotion e;
    if (arg == "off") {
      face.setStill(false);
    } else if (emotionFromName(arg.c_str(), e)) {
      face.setStill(true, e);
    }
    Serial.printf("still: %s\n", arg.c_str());
  } else if (cmd == "clock") {
    if (!arg.isEmpty()) {
      int sp = arg.indexOf(' ');
      clockScreen.setClock(sp < 0 ? arg : arg.substring(0, sp), sp >= 0 && arg.substring(sp + 1) == "24");
    }
    clockScreen.printStatus();
  } else if (cmd == "screen") {
    // "screen <face %> <clock %> <night 0|1|2> <dark below lux> <night %> <idle 0-5> <near 0|1> <near level>"
    // ... [<light bars %> <light bars asleep 0|1>] (0.6.9.5+) [<on a power bank 0|1>] (0.6.9.18+)
    int f, c, n, lx, np, idle, nr, nl, led = screenCfg.led, ledSleep = screenCfg.ledSleep,
        bank = screenCfg.powerBank;
    if (sscanf(arg.c_str(), "%d %d %d %d %d %d %d %d %d %d %d", &f, &c, &n, &lx, &np, &idle, &nr, &nl, &led,
               &ledSleep, &bank) >= 8) {
      screenCfg.powerBank = bank ? 1 : 0;
      screenCfg.led = constrain(led, 0, 100);
      screenCfg.ledSleep = ledSleep ? 1 : 0;
      leds.setBrightness(screenCfg.led);
      leds.setAsleepGlow(screenCfg.ledSleep);
      screenCfg.face = constrain(f, 0, 100);
      screenCfg.clock = constrain(c, 0, 100);
      screenCfg.night = constrain(n, 0, 2);
      screenCfg.darkLux = constrain(lx, 1, 1000);
      screenCfg.nightPct = constrain(np, 1, 100);
      screenCfg.idle = constrain(idle, 0, 5);
      screenCfg.near = nr ? 1 : 0;
      screenCfg.nearLevel = constrain(nl, 1, 2047);
      saveScreen();
      awakeBrightness = screenCfg.face ? fromPct(screenCfg.face) : defaultBrightness;
      if (!deepSleep && !face.asleep()) M5.Display.setBrightness(awakeBrightness);
      shownClockBrightness = 255;  // the clock screen picks up the change on its next check
      nextClockBrightMs = 0;
    }
    printScreen();
  } else if (cmd == "hearing") {  // the brain hears speech: the face and light bars show it
    hearing = arg == "on";
    hearingAt = millis();
    face.setListening(hearing);
    leds.setHearing(hearing);
  } else if (cmd == "leds") {
    // leds test | dance on|off | alert timer|battery|hot|hush
    uint32_t now = millis();
    if (arg == "test") leds.test(now);
    else if (arg == "dance on") leds.setDance(true, now);
    else if (arg == "dance off") leds.setDance(false, now);
    else if (arg == "alert timer") leds.alert(Leds::Alert::Timer, now);
    else if (arg == "alert battery") leds.alert(Leds::Alert::Battery, now);
    else if (arg == "alert hot") leds.alert(Leds::Alert::Hot, now);
    else if (arg == "alert hush") leds.alert(Leds::Alert::Hush, now);
    leds.printStatus();
  } else if (cmd == "light") {
    light.printStatus();
  } else if (cmd == "weather") {
    if (!clockScreen.setWeather(arg)) Serial.println(F("usage: weather <temp> <hi> <lo> <code> <day 0|1> <F|C> <words>"));
  } else if (cmd == "poweroff") {
    powerOff(arg.isEmpty() ? String("console") : arg);
  } else if (cmd == "demo") {
    demoMode = (arg == "on");
    face.setIdle(demoMode);
    Serial.printf("demo %s\n", demoMode ? "on" : "off");
  } else {
    Serial.println(F("unknown command — type `help`"));
  }
}

#if HAVE_OTA
// Listen for firmware updates over WiFi (`pio run -e stackchan-ota -t upload`)
// once WiFi is up. An update blocks loop() until it finishes, then the robot
// restarts into the new firmware; a failed or interrupted one keeps the old.
void updateOta() {
  static bool started = false;
  if (!started) {
    if (WiFi.status() != WL_CONNECTED) return;
    ArduinoOTA.setHostname(OTA_HOSTNAME);
    ArduinoOTA.setPassword(OTA_PASSWORD);
    ArduinoOTA.onStart([]() {
      // Give the update the WiFi and the CPU: stop streaming camera and mic.
      camera.setStreaming(false, CAMERA_MAX_FPS);
      mic.setStreaming(false);
      face.setEmotion(Emotion::Thinking);
      face.update(millis());
      Serial.println(F("ota: receiving update..."));
    });
    ArduinoOTA.onEnd([]() { Serial.println(F("ota: done, restarting")); });
    ArduinoOTA.onError([](ota_error_t e) { Serial.printf("ota: failed (error %u)\n", e); });
    ArduinoOTA.begin();
    started = true;
    Serial.printf("ota: ready as %s.local (%s)\n", OTA_HOSTNAME, WiFi.localIP().toString().c_str());
  }
  ArduinoOTA.handle();
}
#endif

// Milliseconds from `then` to `now`, 0 if `then` is later: a command handled
// during this loop (falling asleep) can stamp a time after the loop's `now`,
// and the plain subtraction then wraps to ~49 days (he slept deep at once).
uint32_t msSince(uint32_t now, uint32_t then) {
  return static_cast<int32_t>(now - then) > 0 ? now - then : 0;
}

// Every loop: read the battery every 10 s, sleep deeper when it's time, and
// the robot's own power-off fallbacks (config.h).
void updatePower(uint32_t now) {
  if (now >= nextBatteryMs) {
    nextBatteryMs = now + 10000;
    batteryVolts = M5StackChan.getBatteryVoltage();
    batteryMa = M5StackChan.getBatteryCurrent() * 1000.0f;
    lowVoltReadings = (onBattery() && batteryVolts > 1.0f && batteryVolts <= POWER_OFF_VOLTS)
                          ? lowVoltReadings + 1 : 0;
    if (lowVoltReadings >= (brainLink.connected() ? 12 : 3)) {
      powerOff("battery");  // the brain says goodbye first if it can; this is the backstop
    }
  }
  checkServoOff(now);
  if (!onBattery() && !screenCfg.powerBank) {
    setDeepSleep(false);  // plugged in: back to normal
  } else if (face.asleep() && asleepSinceMs && msSince(now, asleepSinceMs) >= DEEP_SLEEP_AFTER_MS) {
    setDeepSleep(true);
  }
  if (brainLink.connected()) {
    orphanSinceMs = 0;
  } else {
    if (!orphanSinceMs) orphanSinceMs = now | 1;
    if (!face.asleep() && msSince(now, orphanSinceMs) >= ORPHAN_SLEEP_MS) {
      Serial.println(F("no brain for 3 min: asleep"));
      setAsleep(true);
    }
    if (onBattery() && msSince(now, orphanSinceMs) >= ORPHAN_POWER_OFF_MS) powerOff("no brain");
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
  // After a full power-off the light bars come up showing whatever is in the LED
  // driver's memory: dark until leds.update() draws them.
  M5StackChan.showRgbColor(0, 0, 0);
  M5StackChan.Motion.setAutoTorqueReleaseEnabled(SERVO_AUTO_RELAX);
  // Tracking streams small moves; re-reading the servo before each one stutters.
  M5StackChan.Motion.setAutoAngleSyncEnabled(false);

  gfx.configure(BACKGROUND_COLOR);
  face.begin();
  defaultBrightness = M5.Display.getBrightness();
  loadScreen();
  leds.setBrightness(screenCfg.led);
  leds.setAsleepGlow(screenCfg.ledSleep);
  light.begin();  // before the camera borrows the bus
  awakeBrightness = screenCfg.face ? fromPct(screenCfg.face) : defaultBrightness;
  M5.Display.setBrightness(awakeBrightness);
  panNeck.begin(ServoNeck::Axis::Pan, PAN_MIN_DEG, PAN_MAX_DEG, PAN_SPEED, PAN_INVERT);
  tiltNeck.begin(ServoNeck::Axis::Tilt, TILT_MIN_DEG, TILT_MAX_DEG, TILT_SPEED, TILT_INVERT, TILT_REST_DEG);

  mic.begin(MIC_GAIN);
  if (wake.begin("spiffs", WAKE_MODEL)) {
    mic.setTap([](const int16_t* s, size_t n) { wake.push(s, n); });
#if HAVE_BRAIN
    brainLink.setWakeWord(wake.word());  // tells the brain in the hello
#endif
  }
#if HAVE_BRAIN
  brainLink.setAsleepSource([]() { return face.asleep(); });
#endif
  speaker.begin(&mic, SPEAKER_VOLUME, []() { speakDonePending = true; });
  // Last: it borrows the internal I2C bus the others set up on.
  if (camera.begin()) Serial.println(F("camera: ready"));
  // Each frame is tagged with the head's actual angle (read from the servos),
  // in the same head degrees the brain uses.
  camera.setPoseSource([](float& pan, float& tilt) {
    auto a = M5StackChan.Motion.getCurrentAngles();  // tenths of a degree
    pan = (PAN_INVERT ? -a.x : a.x) / 10.0f;
    tilt = (TILT_INVERT ? -a.y : a.y) / 10.0f;
  });

  demoMode = true;
  face.setIdle(true);
  nextDemoEmotionMs = millis() + 8000;

  Serial.println(F("\ndesk-robot (StackChan) v0.6.9.34 — hello!"));
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
               // either way.
               brainConnected = connected;
               demoMode = !connected;
               if (connected) {
                 face.setEmotion(Emotion::Happy);
               } else {
                 wakeOnRobot = false;  // a brain that doesn't ask gets the old behaviour
               }
               applyMic();
             });
#else
  Serial.println(F("no include/secrets.h — USB-only mode (see secrets.h.example)"));
#endif
  clockScreen.begin();  // the time from the clock chip now, internet time once WiFi is up
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

  // A pat on the head gets a blink at once; the brain hears the gesture and answers it.
  if (M5StackChan.TouchSensor.wasPressed()) face.blink();
  {
    auto& ts = M5StackChan.TouchSensor;
    int swiped = ts.wasSwipedForward() ? 1 : ts.wasSwipedBackward() ? -1 : 0;
    touch.update(millis(), ts.getIntensities(), swiped, [](const char* json) {
      Serial.printf("touch: %s\n", json);
      if (brainLink.connected()) brainLink.sendJson(json);
    });
  }

  uint32_t now = millis();
  uint32_t t0 = micros();

#if HAVE_BRAIN
  brainLink.update(now);
  diag.link = max(diag.link, micros() - t0);
#if HAVE_OTA
  updateOta();
#endif
  if (speakDonePending) {
    speakDonePending = false;
    // With how it went, so the brain's log shows a voice that ran dry and the
    // WiFi signal at the time.
    char done[112];
    snprintf(done, sizeof done,
             "{\"type\":\"speak_done\",\"dry_ms\":%u,\"wait_ms\":%u,\"next_ms\":%u,\"rssi\":%d}",
             static_cast<unsigned>(speaker.lastUnderruns() * 5), static_cast<unsigned>(speaker.lastPrebufferMs()),
             static_cast<unsigned>(speaker.headStartMs()), static_cast<int>(WiFi.RSSI()));
    brainLink.sendJson(done);
    Serial.printf("speech: %u underruns, prebuffered %u bytes in %u ms, wifi %d dBm\n",
                  speaker.lastUnderruns(), speaker.lastPrebuffered(),
                  speaker.lastPrebufferMs(), WiFi.RSSI());
  }
  light.update(now);
  if (hearing && (msSince(now, hearingAt) > HEARING_TIMEOUT_MS || !brainConnected)) {
    hearing = false;  // the off went missing (the brain repeats an on every few seconds)
    face.setListening(false);
    leds.setHearing(false);
  }
  leds.update(now, face.eyeColor(), face.asleep(), deepSleep, face.emotion() == Emotion::Thinking,
              speaker.speaking(), speaker.level(), mic.level());
  updateDark(now);
  static uint32_t proxSeen = 0;
  static uint8_t nearReads = 0;
  if (light.readings() != proxSeen) {
    proxSeen = light.readings();
    nearReads = light.proximity() >= screenCfg.nearLevel ? nearReads + 1 : 0;
    // Two near readings in a row (0.2 s) while it's talking: a hand over the sensor stops it, now. The brain
    // drops the rest of the reply.
    if (nearReads >= 2 && screenCfg.near && speaker.speaking()) {
      Serial.printf("hush: a hand (proximity %u): stopped talking\n", light.proximity());
      speaker.stop();
      leds.alert(Leds::Alert::Hush, now);
      brainLink.sendJson("{\"type\":\"hush\"}");
      nearReads = 0;
    }
  }
  if (wake.takeDetection() && wakeOnRobot && brainConnected && face.asleep()) {
    // Heard the wake word: tell the brain, open his eyes, and stream from
    // 2 s back. The brain checks the transcript and puts him back to sleep
    // if the wake word isn't in it.
    Serial.printf("wake: \"%s\" heard (%lu)\n", wake.word(), (unsigned long)wake.count());
    brainLink.sendJson(String("{\"type\":\"wake\",\"word\":\"") + wake.word() + "\"}");
    mic.startWithPreroll();
    setAsleep(false);
  }
  {
    static uint8_t frame[Mic::FRAME_BYTES];
    while (brainLink.connected() && mic.nextPreroll(frame)) {
      brainLink.sendBinary(0x01, frame, sizeof(frame));
    }
    while (brainLink.connected() && mic.nextFrame(frame)) {
      brainLink.sendBinary(0x01, frame, sizeof(frame));
    }
  }
  if (brainLink.connected() && camera.streaming()) {
    // 0x03 frame: pan and tilt at capture (int16 tenths of a degree,
    // little-endian), then the JPEG. See docs/protocol.md.
    static const size_t POSE_BYTES = 4;
    static uint8_t* buf = static_cast<uint8_t*>(ps_malloc(64 * 1024));
    float pan = 0, tilt = 0;
    size_t n = buf ? camera.takeFrame(buf + POSE_BYTES, 64 * 1024 - POSE_BYTES, &pan, &tilt) : 0;
    if (n > 0) {
      int16_t p = static_cast<int16_t>(lroundf(pan * 10)), t = static_cast<int16_t>(lroundf(tilt * 10));
      memcpy(buf, &p, 2);  // the ESP32 is little-endian
      memcpy(buf + 2, &t, 2);
      uint32_t ts = micros();
      brainLink.sendBinary(0x03, buf, n + POSE_BYTES);
      diag.send = max(diag.send, micros() - ts);
      diag.framesSent++;
    }
  }
  if (brainLink.connected() && now >= nextTempMs) {
    nextTempMs = now + 10000;
    brainLink.sendJson(String("{\"type\":\"temp\",\"c\":") + String(temperatureRead(), 1) + "}");
    // The body's battery (INA226): volts, and mA (+ discharging, - charging).
    char battery[140];
    snprintf(battery, sizeof battery,
             "{\"type\":\"battery\",\"v\":%.2f,\"ma\":%.0f,\"deep\":%s,\"lux\":%.1f,\"prox\":%u,\"dark\":%s}",
             batteryVolts, batteryMa, deepSleep ? "true" : "false", light.lux(), light.proximity(),
             roomDark ? "true" : "false");
    brainLink.sendJson(battery);
  }
  if (brainLink.connected() && now >= nextStateMs) {
    nextStateMs = now + 5000;
    brainLink.sendState(panNeck.current(), emotionName(face.emotion()));
  }
#endif

  updatePower(now);
  clockScreen.update(now);

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
    uint32_t tf = micros();
    // Asleep for a moment (not in deep sleep, where the screen is off): the idle screen instead of the sleeping
    // face. Waking puts the face back.
    bool idle = CLOCK_SCREEN && face.asleep() && !deepSleep && asleepSinceMs &&
                msSince(now, asleepSinceMs) >= SLEEP_FACE_MS;
    IdleMode mode = static_cast<IdleMode>(min(screenCfg.idle, (uint8_t)5));
    Shown want = !idle ? Shown::Face
                 : mode == IdleMode::Clock ? Shown::Clock
                 : mode == IdleMode::Roam ? (isNight() ? Shown::Clock : Shown::Roam)
                                          : Shown::Game;  // a game keeps playing in the dark, dimmed
    if (want == Shown::Game && games.mode() != mode) shown = Shown::Face;  // another game: start it afresh
    if (static_cast<int32_t>(now - nextStatusMs) >= 0) {
      nextStatusMs = now + 1000;
      updateStatus();
    }
    if (want != shown) {
      Shown was = shown;
      shown = want;
      if (was == Shown::Game) face.restoreInks();
      if (was == Shown::Roam) roamStop();
      if (want == Shown::Clock) {
        clockScreen.draw(now, true);  // the whole screen, over the face
      } else if (want == Shown::Game) {
        M5.Display.fillScreen(TFT_BLACK);
        games.reset(mode);
        games.setInks();
        clockScreen.drawStrips(now, true, gfx.bandY(), gfx.bandY() + gfx.height());
        nextGameMs = now;
      } else {  // the face: awake or asleep (roaming too)
        M5.Display.fillScreen(BACKGROUND_COLOR);
        face.redrawBackdrop();
        if (want == Shown::Roam) roamStart(now);
      }
      // The clock and the games readable (night: dimmed); the sleeping face, roaming or not, a quarter.
      shownClockBrightness = want == Shown::Clock || want == Shown::Game ? clockBrightness()
                             : !face.asleep() ? awakeBrightness : max(awakeBrightness / 4, 8);
      M5.Display.setBrightness(shownClockBrightness);
      nextClockBrightMs = now + 1000;
    } else if (shown == Shown::Clock || shown == Shown::Game) {
      if (shown == Shown::Clock) clockScreen.draw(now, false);  // only what changed (a minute, the weather)
      else clockScreen.drawStrips(now, false, gfx.bandY(), gfx.bandY() + gfx.height());
      if (static_cast<int32_t>(now - nextClockBrightMs) >= 0) {  // new settings
        nextClockBrightMs = now + 1000;
        uint8_t b = clockBrightness();
        if (b != shownClockBrightness) M5.Display.setBrightness(shownClockBrightness = b);
      }
    }
    if (shown == Shown::Game) {
      if (static_cast<int32_t>(now - nextGameMs) >= 0) {
        nextGameMs = now + GAME_FRAME_MS;
        char t[16];
        clockScreen.timeText(t, sizeof t);
        games.step();
        games.draw(t);
      }
    } else if (shown != Shown::Clock && !deepSleep) {
      if (shown == Shown::Roam) roamUpdate(now);
      face.update(now);  // screen off in deep sleep: nothing to draw
    }
    diag.face = max(diag.face, micros() - tf);
    uint32_t tn = micros();
    panNeck.update(now);
    tiltNeck.update(now);
    diag.neck = max(diag.neck, micros() - tn);
  }

  diag.loop = max(diag.loop, micros() - t0);
  printDiag(now);
}
