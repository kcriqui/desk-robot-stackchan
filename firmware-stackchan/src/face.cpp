#include "face.h"

#include <ArduinoJson.h>
#include <Preferences.h>

// From the original project's V2 (cgro00/desk-robot firmware/src/face.cpp,
// MIT): the packs, animation and shapes are V2's. What changed for the
// StackChan: shapes are rasterized at the LCD's resolution (FaceGfx::SCALE
// screen pixels to the face pixel) instead of one OLED pixel at a time, and
// they're in colour (each emotion's eye colour, fading from one to the next,
// and coloured blush, tears, hearts and steam).

namespace {

constexpr int SCREEN_W = FaceGfx::FACE_W;  // face pixels
constexpr int SCREEN_H = FaceGfx::FACE_H;
constexpr float S = FaceGfx::SCALE;        // screen pixels per face pixel

const char* kEmotionNames[] = {
    "neutral", "happy", "sad", "angry", "surprised", "sleepy", "thinking",
    "curious", "skeptical", "excited", "love", "confused",
};
static_assert(sizeof(kEmotionNames) / sizeof(kEmotionNames[0]) == static_cast<int>(Emotion::COUNT), "emotion names");

struct FxName {
  const char* name;
  uint16_t bit;
};
const FxName kFx[] = {
    {"blush", FX_BLUSH}, {"tear", FX_TEAR},         {"dots", FX_DOTS},       {"pop", FX_POP},
    {"steam", FX_STEAM}, {"shake", FX_SHAKE},       {"hearts", FX_HEARTS},   {"question", FX_QUESTION},
    {"sparkle", FX_SPARKLE}, {"bounce", FX_BOUNCE}, {"wobble", FX_WOBBLE},
};

// Small pictures, a row per string ('#' = lit).
const char* const kBang[] = {"###", "###", "###", "###", "###", "###", "###", "...", "###", "###"};
const char* const kQuestion[] = {".#####.", "##...##", "##...##", ".....##", "....##.", "...##..",
                                 "..##...", "..##...", ".......", "..##...", "..##..."};

float approach(float cur, float target, float factor) { return cur + (target - cur) * factor; }

void morph(EyeShape& c, const EyeShape& t, float f) {
  c.w = approach(c.w, t.w, f);
  c.h = approach(c.h, t.h, f);
  c.rt = approach(c.rt, t.rt, f);
  c.rb = approach(c.rb, t.rb, f);
  c.st = approach(c.st, t.st, f);
  c.sb = approach(c.sb, t.sb, f);
  c.lid = approach(c.lid, t.lid, f);
  c.cheek = approach(c.cheek, t.cheek, f);
  c.dx = approach(c.dx, t.dx, f);
  c.dy = approach(c.dy, t.dy, f);
}

EyeShape eye(float w, float h, float rt, float rb, float st = 0, float sb = 0, float lid = 0, float cheek = 0,
             float dx = 0, float dy = 0) {
  EyeShape e;
  e.w = w; e.h = h; e.rt = rt; e.rb = rb; e.st = st; e.sb = sb; e.lid = lid; e.cheek = cheek; e.dx = dx; e.dy = dy;
  return e;
}

EmotionLook both(const EyeShape& e, uint16_t fx = 0, float pop = 0.12f, float gx = 0, float gy = 0,
                 int32_t color = -1) {
  EmotionLook l;
  l.l = l.r = e;
  l.fx = fx;
  l.pop = pop;
  l.gazeX = gx;
  l.gazeY = gy;
  l.color = color;
  return l;
}

// Rocky's own face: the pack the robot has before the brain sends one. Keep in step with server/faces/rocky.json.
void rockyPack(FacePack& p) {
  p = FacePack();
  using E = Emotion;
  auto& L = p.looks;
  L[(int)E::Neutral] = both(eye(34, 34, 12, 12));
  L[(int)E::Happy] = both(eye(36, 36, 12, 12, 0, 0, 0, 16), FX_BLUSH, 0.12f, 0, 0, 0xFFE6A0);
  L[(int)E::Sad] = both(eye(32, 26, 10, 10, -9, 0, 4), FX_TEAR, 0.12f, 0, 0, 0x8FB8FF);
  L[(int)E::Angry] = both(eye(34, 24, 8, 8, 10), FX_STEAM | FX_SHAKE, 0.12f, 0, 0, 0xFF6B5B);
  L[(int)E::Surprised] = both(eye(38, 44, 19, 19), FX_POP, 0.3f, 0, 0, 0xFFFFFF);
  L[(int)E::Sleepy] = both(eye(34, 26, 10, 10, 0, 0, 14), 0, 0.12f, 0, 0, 0x9AA3C2);
  L[(int)E::Thinking] = both(eye(30, 30, 11, 11, 0, 0, 5), FX_DOTS, 0.12f, 7, -5, 0xA8E6FF);
  L[(int)E::Curious] = both(eye(30, 30, 11, 11), 0, 0.15f, -5, -3, 0xB8F5C0);
  L[(int)E::Curious].r = eye(38, 40, 15, 15, 0, 0, 0, 0, 0, -2);
  L[(int)E::Skeptical] = both(eye(34, 38, 13, 12, -3), 0, 0.12f, 4, 0, 0xFFC98A);
  L[(int)E::Skeptical].r = eye(34, 34, 12, 12, 0, 0, 17);
  L[(int)E::Excited] = both(eye(38, 40, 16, 16, 0, 0, 0, 10), FX_SPARKLE | FX_BOUNCE, 0.25f, 0, 0, 0xFFF27A);
  L[(int)E::Love] = both(eye(36, 34, 14, 14, 0, 0, 0, 12), FX_HEARTS | FX_BLUSH, 0.2f, 0, 0, 0xFF9BCB);
  L[(int)E::Confused] = both(eye(32, 36, 13, 12, -5), FX_QUESTION | FX_WOBBLE, 0.12f, 0, 0, 0xD3B8FF);
  L[(int)E::Confused].r = eye(28, 24, 10, 10, 4, 0, 0, 0, 0, 4);
  p.asleep = eye(30, 3, 1, 1);
}

void readEye(JsonObjectConst o, EyeShape& e) {
  if (o.isNull()) return;
  e.w = constrain(o["w"] | e.w, 4.0f, 120.0f);
  e.h = constrain(o["h"] | e.h, 2.0f, 64.0f);
  e.rt = constrain(o["rt"] | e.rt, 0.0f, 32.0f);
  e.rb = constrain(o["rb"] | e.rb, 0.0f, 32.0f);
  e.st = constrain(o["st"] | e.st, -30.0f, 30.0f);
  e.sb = constrain(o["sb"] | e.sb, -30.0f, 30.0f);
  e.lid = constrain(o["lid"] | e.lid, 0.0f, 40.0f);
  e.cheek = constrain(o["cheek"] | e.cheek, 0.0f, 40.0f);
  e.dx = constrain(o["dx"] | e.dx, -30.0f, 30.0f);
  e.dy = constrain(o["dy"] | e.dy, -30.0f, 30.0f);
}

// "#RRGGBB" (or without the #) to RGB888; `fallback` if missing or malformed.
int32_t readColor(JsonVariantConst v, int32_t fallback) {
  const char* s = v | "";
  if (*s == '#') ++s;
  if (strlen(s) != 6) return fallback;
  char* end = nullptr;
  long c = strtol(s, &end, 16);
  return (end && *end == 0) ? static_cast<int32_t>(c) : fallback;
}

void readPanel(JsonObjectConst o, Panel& p, float maxH) {
  if (o.isNull()) return;
  p.w = constrain(o["w"] | 0.0f, 0.0f, 128.0f);
  p.h = constrain(o["h"] | 0.0f, 0.0f, maxH);
  p.r = constrain(o["r"] | 0.0f, 0.0f, 48.0f);
  p.rim = constrain(o["rim"] | 0.0f, 0.0f, 8.0f);
  p.color = static_cast<uint32_t>(readColor(o["color"], 0xD8DDDF));
  p.rimColor = static_cast<uint32_t>(readColor(o["rim_color"], 0x8E989E));
}

}  // namespace

const char* emotionName(Emotion e) { return kEmotionNames[static_cast<uint8_t>(e)]; }

bool emotionFromName(const char* name, Emotion& out) {
  for (uint8_t i = 0; i < static_cast<uint8_t>(Emotion::COUNT); i++) {
    if (strcasecmp(name, kEmotionNames[i]) == 0) {
      out = static_cast<Emotion>(i);
      return true;
    }
  }
  return false;
}

// ── packs ───────────────────────────────────────────────────────────────────

bool Face::applyPackJson(const char* json, bool save) {
  JsonDocument doc;
  if (deserializeJson(doc, json)) return false;
  JsonObjectConst p = doc.as<JsonObjectConst>();
  if (p.isNull()) return false;
  FacePack n;
  rockyPack(n);  // anything a pack leaves out stays as Rocky's
  strlcpy(n.name, p["name"] | "unnamed", sizeof(n.name));
  n.gap = constrain(p["gap"] | n.gap, 0.0f, 60.0f);
  n.y = constrain(p["y"] | n.y, -20.0f, 20.0f);
  n.cyclops = p["cyclops"] | false;
  n.outline = p["outline"] | false;
  n.glint = p["glint"] | true;
  const char* mouth = p["mouth"] | "talk";
  n.mouth = !strcmp(mouth, "always") ? 1 : !strcmp(mouth, "never") ? 2 : 0;
  n.color = static_cast<uint32_t>(readColor(p["color"], static_cast<int32_t>(n.color)));
  readEye(p["asleep"], n.asleep);
  readPanel(p["plate"], n.plate, 96.0f);
  readPanel(p["socket"], n.socket, 64.0f);
  n.lens = constrain(p["lens"]["w"] | 0.0f, 0.0f, 20.0f);
  n.lensColor = static_cast<uint32_t>(readColor(p["lens"]["color"], 0x2B323A));
  n.pupil = readColor(p["pupil"], -1);
  JsonObjectConst emos = p["emotions"];
  for (int i = 0; i < static_cast<int>(Emotion::COUNT); ++i) {
    JsonObjectConst e = emos[kEmotionNames[i]];
    if (e.isNull()) continue;
    EmotionLook& look = n.looks[i];
    EyeShape base;  // a listed emotion starts from the plain eye, not from Rocky's version of it
    readEye(e, base);
    look.l = base;
    look.r = base;
    readEye(e["right"], look.r);
    look.pop = constrain(e["pop"] | 0.12f, 0.0f, 0.6f);
    look.smile = constrain(e["smile"] | 0.0f, -6.0f, 6.0f);
    look.gazeX = constrain(e["gaze"][0] | 0.0f, -20.0f, 20.0f);
    look.gazeY = constrain(e["gaze"][1] | 0.0f, -20.0f, 20.0f);
    look.color = readColor(e["color"], -1);
    look.fx = 0;
    for (JsonVariantConst f : e["fx"].as<JsonArrayConst>()) {
      const char* name = f | "";
      for (const FxName& fx : kFx) {
        if (!strcmp(name, fx.name)) look.fx |= fx.bit;
      }
    }
  }
  pack_ = n;
  restoreInks();
  Emotion e = emotion_;
  backdropDirty_ = true;  // a plate came, went or changed: above and below the band too
  emotion_ = Emotion::COUNT;  // force setEmotion to pick up the new look
  setEmotion(e);
  if (still_) setStill(true, e);
  if (save) {
    Preferences prefs;
    prefs.begin("face", false);
    size_t len = strlen(json);
    bool same = prefs.getBytesLength("pack") == len;
    if (same) {
      char* buf = static_cast<char*>(malloc(len));
      same = buf && prefs.getBytes("pack", buf, len) == len && memcmp(buf, json, len) == 0;
      free(buf);
    }
    if (!same) prefs.putBytes("pack", json, len);  // only write flash when the pack really changed
    prefs.end();
  }
  return true;
}

void Face::restoreInks() {
  gfx_.resetInks();
  gfx_.setInk(FaceGfx::PLATE, pack_.plate.color);
  gfx_.setInk(FaceGfx::RIM, pack_.plate.rimColor);
  gfx_.setInk(FaceGfx::SOCKET, pack_.socket.color);
  gfx_.setInk(FaceGfx::LENS, pack_.lensColor);
  if (pack_.pupil >= 0) gfx_.setInk(FaceGfx::PUPIL, static_cast<uint32_t>(pack_.pupil));
  backdropDirty_ = true;
}

void Face::setStill(bool on, Emotion e) {
  still_ = on;
  if (!on) return;
  asleep_ = false;
  setEmotion(e);
  const EmotionLook& look = pack_.looks[static_cast<int>(e)];
  curL_ = look.l;
  curR_ = look.r;
  smile_ = look.smile;
  blinkAmount_ = 0;
  blinkClosing_ = false;
  pop_ = squint_ = twitch_ = 0;
  gazeX_ = gazeTargetX_;
  gazeY_ = gazeTargetY_;
  pupilX_ = pupilY_ = pupilTargetX_ = pupilTargetY_ = 0;
  mouth_ = 0;
  talking_ = false;
  jitterX_ = 0;
}

// ── state ───────────────────────────────────────────────────────────────────

void Face::begin() {
  gfx_.begin();
  rockyPack(pack_);
  {
    Preferences prefs;
    prefs.begin("face", true);
    size_t len = prefs.getBytesLength("pack");
    if (len > 0 && len < 16384) {
      char* buf = static_cast<char*>(malloc(len + 1));
      if (buf && prefs.getBytes("pack", buf, len) == len) {
        buf[len] = 0;
        prefs.end();
        applyPackJson(buf, false);
      } else {
        prefs.end();
      }
      free(buf);
    } else {
      prefs.end();
    }
  }
  curL_ = pack_.looks[0].l;
  curR_ = pack_.looks[0].r;
  // Wake-up: start with eyes shut, then open.
  blinkAmount_ = 1.0f;
  blinkClosing_ = false;
  nextBlinkMs_ = millis() + 3000;
  nextSaccadeMs_ = millis() + 1500;
  nextSquintMs_ = millis() + 9000;
}

void Face::setEmotion(Emotion e) {
  if (e >= Emotion::COUNT) return;
  if (e != emotion_) {
    pop_ = 1.0f;  // a little overshoot sells the change
    emotionSinceMs_ = millis();
    tearY_ = -1.0f;
    nextTearMs_ = millis() + 1500;
    thinkDots_ = 0;
    for (Heart& h : hearts_) h.alive = false;
    nextHeartMs_ = millis() + 200;
  }
  emotion_ = e;
  // Some emotions look somewhere (thinking: up and to the side); the rest release the gaze.
  const EmotionLook& look = pack_.looks[static_cast<int>(e)];
  gazeTargetX_ = look.gazeX;
  gazeTargetY_ = look.gazeY;
}

void Face::blink() { blinkClosing_ = true; }

void Face::setAsleep(bool on) {
  if (on == asleep_) return;
  asleep_ = on;
  if (on) {
    for (Zed& z : zeds_) z.alive = false;
    nextZedMs_ = millis() + 800;
    nextTwitchMs_ = millis() + random(5000, 9000);
    gazeTargetX_ = gazeTargetY_ = 0;
  } else {
    pop_ = 1.0f;  // eyes spring open
    blinkAmount_ = 0.0f;
    setEmotion(emotion_);
  }
}

void Face::lookToward(float x, float y, uint32_t holdMs) {
  pupilTargetX_ = constrain(x, -1.0f, 1.0f);
  pupilTargetY_ = constrain(y, -1.0f, 1.0f);
  gazeTargetX_ = pupilTargetX_ * 6.0f;   // the whole eye leans that way a little too
  gazeTargetY_ = pupilTargetY_ * 3.0f;
  nextPupilMs_ = nextSaccadeMs_ = millis() + holdMs;
}

void Face::setTalking(bool on, float level) {
  talking_ = on;
  mouthLevel_ = constrain(level, 0.0f, 1.0f);
}

void Face::stepAnimation(uint32_t nowMs) {
  frame_++;
  if (still_) return;
  const EmotionLook& look = pack_.looks[static_cast<int>(shown())];

  // Morph the eye shapes toward the current emotion (or shut, if asleep).
  float f = sleepLook() ? 0.12f : 0.25f;  // eyes close slowly, change fast
  morph(curL_, sleepLook() ? pack_.asleep : look.l, f);
  morph(curR_, sleepLook() ? pack_.asleep : look.r, f);
  smile_ = approach(smile_, sleepLook() ? 0 : look.smile, f);

  pop_ *= 0.82f;
  squint_ *= 0.85f;

  if (sleepLook()) {
    // Slow breathing; the odd dream twitch; Z's drifting up.
    breath_ += 0.035f;
    blinkAmount_ = 0.0f;
    blinkClosing_ = false;
    twitch_ *= 0.8f;
    if (nowMs >= nextTwitchMs_) {
      twitch_ = 1.0f;
      nextTwitchMs_ = nowMs + random(6000, 12000);
    }
    if (nowMs >= nextZedMs_) {
      for (Zed& z : zeds_) {
        if (!z.alive) {
          z.alive = true;
          z.age = 0;
          z.x = fminf(pack_.cyclops ? SCREEN_W / 2 + curL_.w / 2 + 2 : SCREEN_W / 2 + pack_.gap / 2 + curL_.w + 2, 108);
          z.y = SCREEN_H / 2 - 6;
          break;
        }
      }
      nextZedMs_ = nowMs + random(1100, 1700);
    }
    for (Zed& z : zeds_) {
      if (!z.alive) continue;
      z.age += 0.012f;
      z.y -= 0.32f;
      z.x += 0.22f;
      if (z.age >= 1.0f || z.y < -12) z.alive = false;
    }
    gazeX_ = approach(gazeX_, 0, 0.2f);
    gazeY_ = approach(gazeY_, 0, 0.2f);
    mouth_ = approach(mouth_, 0, 0.3f);
    return;
  }

  // Blink: snap shut fast, reopen a little slower — reads as natural.
  if (blinkClosing_) {
    blinkAmount_ += 0.45f;
    if (blinkAmount_ >= 1.0f) {
      blinkAmount_ = 1.0f;
      blinkClosing_ = false;
    }
  } else if (blinkAmount_ > 0.0f) {
    blinkAmount_ = max(0.0f, blinkAmount_ - 0.28f);
  }

  if (idle_) {
    if (nowMs >= nextBlinkMs_) {
      blinkClosing_ = true;
      nextBlinkMs_ = nowMs + random(2200, 6000);
      // Occasional double blink.
      if (random(100) < 20) nextBlinkMs_ = nowMs + 400;
    }
    bool looksSomewhere = look.gazeX != 0 || look.gazeY != 0;
    if (nowMs >= nextSaccadeMs_ && !looksSomewhere) {
      gazeTargetX_ = static_cast<float>(random(-8, 9));
      gazeTargetY_ = static_cast<float>(random(-4, 5));
      // Mostly return to center so the robot doesn't look shifty.
      if (random(100) < 40) gazeTargetX_ = gazeTargetY_ = 0;
      nextSaccadeMs_ = nowMs + random(1200, 4000);
    }
    if (nowMs >= nextPupilMs_) {
      // The pupils look at something new: a quick dart, about a third of the time back near the middle.
      if (random(100) < 35) {
        pupilTargetX_ = random(-25, 26) / 100.0f;
        pupilTargetY_ = random(-20, 21) / 100.0f;
      } else {
        pupilTargetX_ = random(-90, 91) / 100.0f;
        pupilTargetY_ = random(-60, 61) / 100.0f;
      }
      nextPupilMs_ = nowMs + random(600, 2600);
    }
    if (nowMs >= nextSquintMs_ && emotion_ == Emotion::Neutral) {
      squint_ = 1.0f;
      nextSquintMs_ = nowMs + random(8000, 16000);
    }
  }

  // Flourishes.
  if (look.fx & FX_TEAR) {
    if (tearY_ < 0 && nowMs >= nextTearMs_) tearY_ = 0;
    if (tearY_ >= 0) {
      tearY_ += 0.7f;
      if (tearY_ > SCREEN_H) {
        tearY_ = -1;
        nextTearMs_ = nowMs + random(2500, 5000);
      }
    }
  }
  if ((look.fx & FX_DOTS) && nowMs >= nextDotMs_) {
    thinkDots_ = (thinkDots_ + 1) % 4;
    nextDotMs_ = nowMs + 420;
  }
  if ((look.fx & FX_HEARTS) && nowMs >= nextHeartMs_) {
    for (Heart& h : hearts_) {
      if (!h.alive) {
        h.alive = true;
        h.age = 0;
        heartSide_ = !heartSide_;
        h.x = heartSide_ ? SCREEN_W / 2 + pack_.gap / 2 + curR_.w + 2 : SCREEN_W / 2 - pack_.gap / 2 - curL_.w - 9;
        if (pack_.cyclops) h.x = heartSide_ ? SCREEN_W / 2 + curL_.w / 2 + 3 : SCREEN_W / 2 - curL_.w / 2 - 10;
        h.y = SCREEN_H / 2 + 6;
        break;
      }
    }
    nextHeartMs_ = nowMs + random(600, 1000);
  }
  for (Heart& h : hearts_) {
    if (!h.alive) continue;
    h.age += 0.015f;
    h.y -= 0.45f;
    if (h.age >= 1.0f || h.y < -6) h.alive = false;
  }
  jitterX_ = ((look.fx & FX_SHAKE) && (frame_ % 3 == 0)) ? random(-1, 2) : 0;

  // Mouth follows the speaker's loudness while talking, closes otherwise.
  float mouthGoal = talking_ ? constrain(0.15f + mouthLevel_ * 4.0f, 0.15f, 1.0f) : 0.0f;
  mouth_ = approach(mouth_, mouthGoal, talking_ ? 0.5f : 0.3f);

  gazeX_ = approach(gazeX_, gazeTargetX_, 0.35f);
  gazeY_ = approach(gazeY_, gazeTargetY_, 0.35f);
  pupilX_ = approach(pupilX_, pupilTargetX_, 0.5f);   // eyes dart: there in a few frames
  pupilY_ = approach(pupilY_, pupilTargetY_, 0.5f);
}

// The eyes' colour fades toward the emotion's (asleep: the sleepy one).
void Face::stepColor() {
  const EmotionLook& look = pack_.looks[static_cast<int>(sleepLook() ? Emotion::Sleepy : shown())];
  uint32_t t = look.color >= 0 ? static_cast<uint32_t>(look.color) : pack_.color;
  float f = still_ ? 1.0f : 0.12f;
  colR_ = approach(colR_, (t >> 16) & 0xFF, f);
  colG_ = approach(colG_, (t >> 8) & 0xFF, f);
  colB_ = approach(colB_, t & 0xFF, f);
  gfx_.setInk(FaceGfx::EYE, (static_cast<uint32_t>(lroundf(colR_)) << 16) |
                                (static_cast<uint32_t>(lroundf(colG_)) << 8) | static_cast<uint32_t>(lroundf(colB_)));
}

// ── drawing ─────────────────────────────────────────────────────────────────
// Face-pixel coordinates are continuous: pixel x covers [x, x+1).

void Face::dot(int x, int y, uint8_t ink) {
  if (x < 0 || y < 0 || x >= SCREEN_W || y >= SCREEN_H) return;
  int sx = lroundf(x * S), sy = lroundf(y * S);
  gfx_.fillRect(sx, sy, lroundf((x + 1) * S) - sx, lroundf((y + 1) * S) - sy, ink);
}

void Face::rect(int x, int y, int w, int h, uint8_t ink) {
  int sx = lroundf(x * S), sy = lroundf(y * S);
  gfx_.fillRect(sx, sy, lroundf((x + w) * S) - sx, lroundf((y + h) * S) - sy, ink);
}

void Face::roundBox(float x, float y, float w, float h, float r, uint8_t ink) {
  r = fminf(r, fminf(w, h) * 0.5f);
  int sy0 = static_cast<int>(floorf(y * S)), sy1 = static_cast<int>(ceilf((y + h) * S));
  for (int sy = sy0; sy < sy1; ++sy) {
    float py = (sy + 0.5f) / S - y;
    if (py < 0 || py >= h) continue;
    float inset = 0;
    if (py < r || py > h - r) {
      float d = py < r ? r - py : py - (h - r);
      inset = r - sqrtf(fmaxf(0, r * r - d * d));
    }
    gfx_.span(sy, lroundf((x + inset) * S), lroundf((x + w - inset) * S), ink);
  }
}

// Between pixel centres, a face pixel thick.
void Face::line(float x0, float y0, float x1, float y1, uint8_t ink) {
  gfx_.line((x0 + 0.5f) * S, (y0 + 0.5f) * S, (x1 + 0.5f) * S, (y1 + 0.5f) * S, S * 0.55f, ink);
}

void Face::disc(float cx, float cy, float r, uint8_t ink) {
  gfx_.fillCircle((cx + 0.5f) * S, (cy + 0.5f) * S, (r + 0.5f) * S, ink);
}

void Face::glyph(const char* const* rows, int n, int x, int y, uint8_t ink) {
  for (int j = 0; j < n; ++j)
    for (int i = 0; rows[j][i]; ++i)
      if (rows[j][i] == '#') dot(x + i, y + j, ink);
}

namespace {
// Where one eye is, worked out once per frame.
struct Geo {
  float cx, w, left, right, top, bot, rt, rb, st, sb, lid, cheek;
  float invHalfW, invH;  // 1 / (w / 2) and 1 / height: inEye runs for every screen pixel, so no divisions there
  int inner;  // +1: the inner edge is to the right (the left eye), -1: to the left
};

bool inEye(const Geo& g, float x, float y, bool cheek = true) {
  if (x < g.left || x >= g.right || y < g.top || y >= g.bot) return false;
  float u = (x - g.cx) * g.invHalfW * g.inner;  // -1 outer edge .. +1 inner edge
  float topCut = g.st >= 0 ? g.st * (u + 1) * 0.5f : -g.st * (1 - u) * 0.5f;
  float botCut = g.sb >= 0 ? g.sb * (u + 1) * 0.5f : -g.sb * (1 - u) * 0.5f;
  // Slants tilt the whole rounded eye (a shear), so slanted corners stay round: look the point up in the
  // unslanted eye.
  float yy = y - (topCut * (g.bot - y) - botCut * (y - g.top)) * g.invH;
  if (yy < g.top + g.lid || yy >= g.bot) return false;
  if (yy < g.top + g.rt) {
    float qx = constrain(x, g.left + g.rt, g.right - g.rt), dx = x - qx, dy = yy - (g.top + g.rt);
    if (dx * dx + dy * dy > g.rt * g.rt) return false;
  }
  if (yy > g.bot - g.rb) {
    float qx = constrain(x, g.left + g.rb, g.right - g.rb), dx = x - qx, dy = yy - (g.bot - g.rb);
    if (dx * dx + dy * dy > g.rb * g.rb) return false;
  }
  if (cheek && g.cheek > 0.5f) {  // a round cheek pushing up from below
    float rr = g.w, ccy = g.bot + rr - g.cheek, dx = x - g.cx, dy = y - ccy;
    if (dx * dx + dy * dy < rr * rr) return false;
  }
  return true;
}
}  // namespace

void Face::drawEye(const EyeShape& e, float cx, float cy, int inner, float hScale) {
  float w = e.w, h = e.h * hScale * (1.0f - blinkAmount_);
  if (h < 2) h = 2;
  Geo g;
  g.cx = cx - inner * e.dx;
  float ecy = cy + e.dy;
  g.w = w;
  g.left = g.cx - w * 0.5f;
  g.right = g.cx + w * 0.5f;
  g.top = ecy - h * 0.5f;
  g.bot = ecy + h * 0.5f;
  g.rt = fmaxf(0, fminf(e.rt, fminf(w, h) * 0.5f));
  g.rb = fmaxf(0, fminf(e.rb, fminf(w, h) * 0.5f));
  g.st = e.st;
  g.sb = e.sb;
  g.lid = e.lid;
  g.cheek = e.cheek;
  g.inner = inner;
  g.invHalfW = 2.0f / w;
  g.invH = 1.0f / h;

  // The eye, a screen row at a time, as runs of lit pixels. Without an
  // outline, a row of the eye is one run (find its two ends and fill
  // between), minus where a cheek's circle bites into it from below, worked
  // out exactly. Outlined eyes test every pixel.
  int sx0 = static_cast<int>(floorf(g.left * S)), sx1 = static_cast<int>(ceilf(g.right * S));
  int sy0 = static_cast<int>(floorf(g.top * S)), sy1 = static_cast<int>(ceilf(g.bot * S));
  const bool cheek = g.cheek > 0.5f;
  const float rr = g.w, ccy = g.bot + rr - g.cheek;  // the cheek's circle
  for (int sy = sy0; sy < sy1; ++sy) {
    float py = (sy + 0.5f) / S;
    if (!pack_.outline) {
      int a = sx0, b = sx1 - 1;
      while (a <= b && !inEye(g, (a + 0.5f) / S, py, false)) ++a;
      while (b >= a && !inEye(g, (b + 0.5f) / S, py, false)) --b;
      if (a > b) continue;
      float dy = py - ccy;
      if (cheek && dy * dy < rr * rr) {
        // Pixels whose centre is inside the circle: |sx + 0.5 - cx*S| < half*S.
        float half = sqrtf(rr * rr - dy * dy) * S, c = g.cx * S - 0.5f;
        int ha = static_cast<int>(floorf(c - half)) + 1, hb = static_cast<int>(ceilf(c + half)) - 1;
        gfx_.span(sy, a, min(b + 1, ha), FaceGfx::EYE);
        gfx_.span(sy, max(a, hb + 1), b + 1, FaceGfx::EYE);
      } else {
        gfx_.span(sy, a, b + 1, FaceGfx::EYE);
      }
      continue;
    }
    int run = -1;
    for (int sx = sx0; sx <= sx1; ++sx) {
      bool on = false;
      if (sx < sx1) {
        float px = (sx + 0.5f) / S;
        on = inEye(g, px, py);
        if (on && pack_.outline)
          on = !(inEye(g, px - 2, py) && inEye(g, px + 2, py) && inEye(g, px, py - 2) && inEye(g, px, py + 2));
      }
      if (on && run < 0) run = sx;
      if (!on && run >= 0) {
        gfx_.span(sy, run, sx, FaceGfx::EYE);
        run = -1;
      }
    }
  }

  // Pupil: a dark disc (lit in outline packs) with a highlight, somewhere in the part of the eye that shows, so
  // lids and cheeks cover it like real ones. It also leans the way the whole eye is glancing.
  if (pack_.glint && h > 12 && !sleepLook()) {
    int x0 = static_cast<int>(floorf(g.left)), x1 = static_cast<int>(ceilf(g.right));
    int y0 = static_cast<int>(floorf(g.top)), y1 = static_cast<int>(ceilf(g.bot));
    float r = constrain(g.w * 0.14f, 3.0f, 6.0f);   // ~10 face px across on a 34 px eye
    int top = -1, bot = -1;
    for (int y = y0; y < y1; ++y) {
      if (inEye(g, g.cx, y + 0.5f)) {
        if (top < 0) top = y;
        bot = y;
      }
    }
    if (top < 0) return;
    float vcy = (top + bot + 1) * 0.5f;
    int left = -1, right = -1;
    for (int x = x0; x < x1; ++x) {
      if (inEye(g, x + 0.5f, vcy)) {
        if (left < 0) left = x;
        right = x;
      }
    }
    if (left < 0) return;
    float vcx = (left + right + 1) * 0.5f;
    float roomX = fmaxf(0, (right - left + 1) * 0.5f - r - 2.0f), roomY = fmaxf(0, (bot - top + 1) * 0.5f - r - 1.5f);
    float px = constrain(pupilX_ + gazeX_ / 12.0f, -1.0f, 1.0f), py = constrain(pupilY_ + gazeY_ / 8.0f, -1.0f, 1.0f);
    float pcx = vcx + px * roomX, pcy = vcy + py * roomY;
    uint8_t ink = pack_.outline ? FaceGfx::EYE : FaceGfx::PUPIL;
    int psy0 = static_cast<int>(floorf((pcy - r) * S)), psy1 = static_cast<int>(ceilf((pcy + r) * S));
    for (int sy = psy0; sy < psy1; ++sy) {
      float yy = (sy + 0.5f) / S, dy = yy - pcy;
      if (dy * dy >= r * r) continue;
      float half = sqrtf(r * r - dy * dy);
      // The circle's run on this row, trimmed at both ends to the eye (a pupil
      // is small: a lid or cheek only ever covers it from one side).
      int a = static_cast<int>(lroundf((pcx - half) * S)), b = static_cast<int>(lroundf((pcx + half) * S)) - 1;
      while (a <= b && !inEye(g, (a + 0.5f) / S, yy)) ++a;
      while (b >= a && !inEye(g, (b + 0.5f) / S, yy)) --b;
      if (a <= b) gfx_.span(sy, a, b + 1, ink);
    }
    if (!pack_.outline && r >= 3.5f) {   // the highlight: up and to the left in the pupil
      float hx = pcx - r * 0.42f, hy = pcy - r * 0.45f;
      if (inEye(g, hx, hy)) gfx_.fillCircle(hx * S, hy * S, r * 0.3f * S, FaceGfx::SHINE);
    }
  }
}

void Face::drawMouth(int cx, int cy, int eyeBottom) {
  (void)eyeBottom;
  float my0 = floorf(cy + curL_.h * 0.5f);
  if (mouth_ >= 0.05f && pack_.mouth != 2) {
    float mh = 2 + mouth_ * 9;
    float mw = 20 + mouth_ * 8;
    float my = my0 + 4;
    if (my + mh > SCREEN_H - 1) my = SCREEN_H - 1 - mh;
    roundBox(cx - mw / 2, my, mw, mh, fminf(mh / 2, 4), FaceGfx::EYE);
    if (mh > 6) roundBox(cx - mw / 2 + 3, my + 2, mw - 6, mh - 4, 2, FaceGfx::PUPIL);  // darker inside, wide open
  } else if (pack_.mouth == 1) {
    // Resting mouth: a short curve, smiling or frowning, two face pixels thick.
    const int mw = 14;
    float my = fminf(my0 + 6, SCREEN_H - 4);
    float px = 0, py = 0;
    for (int i = 0; i <= mw; ++i) {
      float t = (i - mw * 0.5f) / (mw * 0.5f);
      float x = cx - mw / 2 + i, y = my + smile_ * (1 - t * t) + 0.5f;
      if (i > 0) gfx_.line((px + 0.5f) * S, (py + 0.5f) * S, (x + 0.5f) * S, (y + 0.5f) * S, S, FaceGfx::EYE);
      px = x;
      py = y;
    }
  }
}

void Face::drawFlourishes(float leftOuter, float rightOuter, int eyeTop, int eyeBottom) {
  const EmotionLook& look = pack_.looks[static_cast<int>(shown())];
  uint16_t fx = look.fx;
  uint32_t since = millis() - emotionSinceMs_;
  int lo = static_cast<int>(floorf(leftOuter)), ro = static_cast<int>(floorf(rightOuter));

  if ((fx & FX_POP) && since < 900) glyph(kBang, 10, 116, 4, FaceGfx::EYE);
  if (fx & FX_BLUSH) {
    for (int i = 0; i < 2; ++i) {  // two short slashes below the outer corner of each eye
      int lx = lo - 8 + i * 3, rx = ro + 3 + i * 3;
      line(lx, eyeBottom + 1, lx + 3, eyeBottom - 3, FaceGfx::BLUSH);
      line(rx, eyeBottom + 1, rx + 3, eyeBottom - 3, FaceGfx::BLUSH);
    }
  }
  if ((fx & FX_TEAR) && tearY_ >= 0) {
    float tx = ro - 3, ty = eyeBottom + tearY_;
    disc(tx, ty, 2, FaceGfx::TEAR);
    line(tx, ty - 5, tx, ty - 2, FaceGfx::TEAR);
  }
  if (fx & FX_DOTS) {
    for (int i = 0; i < thinkDots_; ++i) disc(SCREEN_W - 26 + i * 8, 9, 2, FaceGfx::EYE);
  }
  if ((fx & FX_STEAM) && (frame_ / 6) % 2 == 0) {  // two short strokes above the outer corners
    line(lo - 4, eyeTop - 4, lo - 2, eyeTop - 9, FaceGfx::STEAM);
    line(ro + 4, eyeTop - 4, ro + 2, eyeTop - 9, FaceGfx::STEAM);
  }
  if (fx & FX_HEARTS) {
    for (const Heart& h : hearts_) {
      if (!h.alive || (h.age > 0.8f && frame_ % 2 == 0)) continue;
      // A smooth heart in the 7x6 face-pixel box at h.x, h.y: two lobes and a point.
      float x = h.x, y = h.y;
      gfx_.fillCircle((x + 2.0f) * S, (y + 1.9f) * S, 1.95f * S, FaceGfx::HEART);
      gfx_.fillCircle((x + 5.0f) * S, (y + 1.9f) * S, 1.95f * S, FaceGfx::HEART);
      gfx_.fillTriangle((x + 0.1f) * S, (y + 2.4f) * S, (x + 6.9f) * S, (y + 2.4f) * S, (x + 3.5f) * S,
                        (y + 6.2f) * S, FaceGfx::HEART);
    }
  }
  if (fx & FX_QUESTION)
    glyph(kQuestion, 11, 112, 3 + static_cast<int>(floorf(sinf(frame_ * 0.15f) * 2)), FaceGfx::EYE);
  if (fx & FX_SPARKLE) {
    bool a = (frame_ / 5) % 2 == 0;
    int pts[4] = {a ? lo - 6 : lo - 5, a ? eyeTop + 3 : eyeBottom - 5, a ? ro + 6 : ro + 5, a ? eyeBottom - 4 : eyeTop + 2};
    for (int k = 0; k < 2; ++k) {
      int x = pts[k * 2], y = pts[k * 2 + 1];
      line(x - 2, y, x + 2, y, FaceGfx::SHINE);
      line(x, y - 2, x, y + 2, FaceGfx::SHINE);
    }
  }
}

void Face::drawZeds() {
  for (const Zed& z : zeds_) {
    if (!z.alive) continue;
    if (z.age > 0.8f && (frame_ % 2 == 0)) continue;  // flicker out at the end
    int s = z.age < 0.33f ? 4 : z.age < 0.66f ? 6 : 8;
    float x = z.x, y = z.y - s;
    if (x < 0 || x >= SCREEN_W - 4 || y < 0) continue;
    for (int b = 0; b < (s >= 8 ? 2 : 1); ++b) {  // the biggest Z is drawn bold
      line(x + b, y, x + s + b, y, FaceGfx::EYE);
      line(x + s + b, y, x + b, y + s, FaceGfx::EYE);
      line(x + b, y + s, x + s + b, y + s, FaceGfx::EYE);
    }
  }
}

namespace {
// Row sy (the band's screen rows; < 0 or past its height = off it) of a box centred on the screen: [a, b) in
// screen pixels, false if the row misses it. The band and the screen around it both draw plates with this, so
// they meet without a seam.
bool boxRow(float w, float h, float r, int sy, int& a, int& b) {
  if (w <= 0 || h <= 0) return false;
  float x = SCREEN_W * 0.5f - w * 0.5f, y = SCREEN_H * 0.5f - h * 0.5f;
  r = fminf(r, fminf(w, h) * 0.5f);
  float py = (sy + 0.5f) / S - y;
  if (py < 0 || py >= h) return false;
  float inset = 0;
  if (py < r || py > h - r) {
    float d = py < r ? r - py : py - (h - r);
    inset = r - sqrtf(fmaxf(0, r * r - d * d));
  }
  a = lroundf((x + inset) * S);
  b = lroundf((x + w - inset) * S);
  return b > a;
}
}  // namespace

void Face::drawPanel(const Panel& p, uint8_t ink, uint8_t rimInk) {
  if (p.w <= 0) return;
  for (int sy = 0; sy < gfx_.height(); ++sy) {
    int a, b, ia, ib;
    if (!boxRow(p.w, p.h, p.r, sy, a, b)) continue;
    if (p.rim > 0 && boxRow(p.w - 2 * p.rim, p.h - 2 * p.rim, p.r - p.rim, sy, ia, ib)) {
      gfx_.span(sy, a, ia, rimInk);
      gfx_.span(sy, ia, ib, ink);
      gfx_.span(sy, ib, b, rimInk);
    } else {
      gfx_.span(sy, a, b, p.rim > 0 ? rimInk : ink);
    }
  }
}

// The plate's rows above and below the band, straight onto the screen: it doesn't move, so only when the pack
// changes or something else has drawn there.
void Face::drawBackdrop() {
  backdropDirty_ = false;
  const Panel& p = pack_.plate;
  uint32_t bg = gfx_.background();
  int top = -gfx_.bandY(), bottom = M5.Display.height() - gfx_.bandY();
  for (int sy = top; sy < bottom; ++sy) {
    if (sy == 0) sy = gfx_.height();  // the band draws its own
    if (sy >= bottom) break;
    int w = gfx_.width(), a, b, ia, ib;
    if (!boxRow(p.w, p.h, p.r, sy, a, b)) {
      gfx_.screenSpan(sy, 0, w, bg);
      continue;
    }
    gfx_.screenSpan(sy, 0, a, bg);
    gfx_.screenSpan(sy, b, w, bg);
    if (p.rim > 0 && boxRow(p.w - 2 * p.rim, p.h - 2 * p.rim, p.r - p.rim, sy, ia, ib)) {
      gfx_.screenSpan(sy, a, ia, p.rimColor);
      gfx_.screenSpan(sy, ia, ib, p.color);
      gfx_.screenSpan(sy, ib, b, p.rimColor);
    } else {
      gfx_.screenSpan(sy, a, b, p.rim > 0 ? p.rimColor : p.color);
    }
  }
}

void Face::render() {
  gfx_.clear();
  if (backdropDirty_) drawBackdrop();
  drawPanel(pack_.plate, FaceGfx::PLATE, FaceGfx::RIM);
  drawPanel(pack_.socket, FaceGfx::SOCKET, FaceGfx::SOCKET);
  const EmotionLook& look = pack_.looks[static_cast<int>(shown())];
  bool lively = !still_ && !sleepLook();

  // Vertical bob: breathing while asleep, a bounce with the mouth while talking.
  float bob = sleepLook() ? sinf(breath_) * 2.0f : -mouth_ * 2.0f;
  if (lively && (look.fx & FX_BOUNCE)) bob -= fabsf(sinf(frame_ * 0.18f)) * 3.0f;
  float sway = (lively && (look.fx & FX_WOBBLE)) ? sinf(frame_ * 0.08f) * 3.0f : 0.0f;
  float cy = SCREEN_H / 2 + pack_.y + gazeY_ + bob;
  if (talking_) cy -= 2;  // make room for the mouth
  float gx = gazeX_ + jitterX_ + sway;

  // Height scale: emotion-change overshoot, idle squint, dream twitch.
  float hScale = 1.0f + pop_ * look.pop;
  hScale *= 1.0f - squint_ * 0.4f;
  if (sleepLook()) hScale += twitch_ * 2.5f;  // eyelids flutter open a crack

  float leftOuter, rightOuter;
  if (pack_.cyclops) {
    float cx = SCREEN_W / 2 + gx;
    if (pack_.socket.w > 0) {  // glances stay inside the socket: the lens (or the eye) never leaves it
      float rl = fmaxf(curL_.w, curL_.h) * 0.5f + pack_.lens;
      float roomX = fmaxf(0, pack_.socket.w * 0.5f - rl), roomY = fmaxf(0, pack_.socket.h * 0.5f - rl);
      cx = constrain(cx - curL_.dx, SCREEN_W / 2 - roomX, SCREEN_W / 2 + roomX) + curL_.dx;
      cy = constrain(cy + curL_.dy, SCREEN_H / 2 - roomY, SCREEN_H / 2 + roomY) - curL_.dy;
    }
    if (pack_.lens > 0)  // the lens the eye shines through, round whatever the eye's shape
      gfx_.fillCircle((cx - curL_.dx) * S, (cy + curL_.dy) * S, (fmaxf(curL_.w, curL_.h) * 0.5f + pack_.lens) * S,
                      FaceGfx::LENS);
    drawEye(curL_, cx, cy, 1, hScale);
    leftOuter = cx - curL_.dx - curL_.w / 2;
    rightOuter = cx - curL_.dx + curL_.w / 2;
  } else {
    float lcx = SCREEN_W / 2 - pack_.gap / 2 - curL_.w / 2 + gx;
    float rcx = SCREEN_W / 2 + pack_.gap / 2 + curR_.w / 2 + gx;
    drawEye(curL_, lcx, cy, 1, hScale);
    drawEye(curR_, rcx, cy, -1, hScale);
    leftOuter = lcx - curL_.dx - curL_.w / 2;
    rightOuter = rcx + curR_.dx + curR_.w / 2;
  }

  int eyeTop = static_cast<int>(floorf(cy - curL_.h * hScale / 2));
  int eyeBottom = static_cast<int>(floorf(cy + curL_.h * hScale / 2));
  if (sleepLook()) {
    drawZeds();
  } else {
    if (!still_) drawFlourishes(leftOuter, rightOuter, eyeTop, eyeBottom);
    drawMouth(SCREEN_W / 2 + static_cast<int>(floorf(gazeX_ * 0.5f)), static_cast<int>(floorf(cy)), eyeBottom);
  }
  stepColor();
  gfx_.push();
}

void Face::update(uint32_t nowMs) {
  lastFrameMs_ = nowMs;
  stepAnimation(nowMs);
  render();
}
