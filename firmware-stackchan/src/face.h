#pragma once

#include <Arduino.h>

#include "face_gfx.h"

// Expressive two-eye face, drawn from a face pack. From the original
// project's V2 (cgro00/desk-robot firmware/src/face.h, MIT), drawn on the
// StackChan's colour LCD at its own resolution and in colour.
//
// A pack gives every emotion a shape for each eye (size, corner rounding, slanted edges, eyelids, cheeks), a colour
// and a set of flourishes (blush, tears, hearts, "?"...). The face morphs smoothly from one shape (and colour) to the
// next and adds life on top: blinks, glances, pupils that look around, a mouth that moves with the speaker's audio,
// and a sleep state with slow breathing and floating Z's.
//
// Packs come from the brain (server/faces/*.json) and are kept in flash. Shapes are in the original 128x64 OLED's
// "face pixels" and worked out with plain arithmetic, so packs from the original project fit as they are.

enum class Emotion : uint8_t {
  Neutral,
  Happy,
  Sad,
  Angry,
  Surprised,
  Sleepy,
  Thinking,
  Curious,
  Skeptical,
  Excited,
  Love,
  Confused,
  COUNT,
};

const char* emotionName(Emotion e);
bool emotionFromName(const char* name, Emotion& out);

// One eye. Face pixels. "Inner" is the side toward the other eye.
struct EyeShape {
  float w = 34, h = 34;    // size
  float rt = 12, rb = 12;  // corner rounding, top / bottom
  float st = 0, sb = 0;    // edge slant, top / bottom: > 0 the inner corner moves in by this much (angry brow,
                           // squint), < 0 the outer corner does (sad)
  float lid = 0;           // upper lid: a flat droop from the top (sleepy)
  float cheek = 0;         // lower lid: a round cheek pushing up from below (happy crescent)
  float dx = 0, dy = 0;    // offset: + = outward / down
};

// Flourishes an emotion can switch on.
enum : uint16_t {
  FX_BLUSH = 1 << 0,     // slashes under the outer corners
  FX_TEAR = 1 << 1,      // a tear falls from the right eye
  FX_DOTS = 1 << 2,      // thinking dots, top right
  FX_POP = 1 << 3,       // "!" for a moment after the change
  FX_STEAM = 1 << 4,     // flickering strokes above the outer corners
  FX_SHAKE = 1 << 5,     // jitters side to side
  FX_HEARTS = 1 << 6,    // hearts float up
  FX_QUESTION = 1 << 7,  // a bobbing "?"
  FX_SPARKLE = 1 << 8,   // twinkles beside the eyes
  FX_BOUNCE = 1 << 9,    // eyes bounce
  FX_WOBBLE = 1 << 10,   // eyes sway
};

struct EmotionLook {
  EyeShape l, r;            // left / right eye as you look at the screen
  float gazeX = 0, gazeY = 0;  // where the eyes rest (thinking looks up and to the side)
  float pop = 0.12f;        // how much the eyes overshoot when this emotion arrives
  float smile = 0;          // the resting mouth (packs with mouth "always"): + smile, - frown
  uint16_t fx = 0;
  int32_t color = -1;       // RGB888 of the eyes; -1 = the pack's colour
};

// A flat shape behind the eyes, centred on the screen, in face pixels: a face plate (it may be taller than the
// face's band, up to the whole screen: 96), or the socket the eye sits in. w 0 = none.
struct Panel {
  float w = 0, h = 0, r = 0;
  float rim = 0;               // a darker edge this thick
  uint32_t color = 0, rimColor = 0;
};

struct FacePack {
  char name[32] = "Rocky";
  float gap = 16;           // between the eyes
  float y = 0;              // eye height: + = lower
  bool cyclops = false;     // one eye in the middle (the left eye's shape)
  bool outline = false;     // eyes drawn as outlines instead of solid
  bool glint = true;        // a pupil in each eye (dark, with a highlight) that looks around on its own
  uint8_t mouth = 0;        // 0 = only while talking, 1 = always (a resting smile), 2 = never
  float mouthW = 14;        // the resting mouth's width, face pixels (StackChan's own face: a wide bar)
  float mouthTilt = 0;      // idle, the resting mouth now and then tilts by up to this many degrees (StackChan's)
  uint32_t color = 0xF4F1EA;  // the eyes' colour for emotions that don't set one
  EmotionLook looks[static_cast<int>(Emotion::COUNT)];
  EyeShape asleep;          // the shut eyes while sleeping
  Panel plate, socket;      // a face behind the eyes (GLaDOS's long white one) and the socket the eye sits in
  float lens = 0;           // a ring this much wider than the eye around it, that moves with it (0 = none)
  uint32_t lensColor = 0;
  int32_t pupil = -1;       // the pupils' colour (RGB888); -1 = the usual dark
};

class Face {
 public:
  explicit Face(FaceGfx& gfx) : gfx_(gfx) {}

  // Starts the canvas and loads the face pack saved in flash (Rocky's own if none).
  void begin();
  void setEmotion(Emotion e);
  Emotion emotion() const { return emotion_; }
  void blink();

  // Asleep: eyes shut, breathing, Z's. Overrides the emotion while on.
  void setAsleep(bool on);
  bool asleep() const { return asleep_; }

  // Talking: draws a mouth that opens with `level` (0..1, the speaker's
  // current loudness). Call every frame while audio plays.
  void setTalking(bool on, float level = 0.0f);

  // Hearing you (the brain says when): the eyes open a little wider and look straight at you.
  void setListening(bool on) { listening_ = on; }

  // When idle behavior is on, the face blinks and glances around on its own.
  void setIdle(bool on) { idle_ = on; }

  // A face pack (JSON, as server/faces/*.json) from the brain. save = keep it in flash. False if it doesn't parse.
  bool applyPackJson(const char* json, bool save = true);
  const char* packName() const { return pack_.name; }
  // Testing: hold `e` settled and still (no blinks, glances or flourishes).
  void setStill(bool on, Emotion e = Emotion::Neutral);

  // Call every frame: advances animation, then renders.
  void update(uint32_t nowMs);
  // The eyes' colour right now (RGB888), easing between emotions: the light bars glow in it.
  uint32_t eyeColor() const {
    return (static_cast<uint32_t>(lroundf(colR_)) << 16) | (static_cast<uint32_t>(lroundf(colG_)) << 8) |
           static_cast<uint32_t>(lroundf(colB_));
  }
  // Something else drew over the screen: the next frame repaints the plate above and below the band too.
  void redrawBackdrop() { backdropDirty_ = true; }
  // An idle game borrowed the canvas's inks: put the face's back (its pack's included).
  void restoreInks();

 private:
  struct Zed {
    bool alive = false;
    float x = 0, y = 0;
    float age = 0;  // 0..1 over its life
  };
  struct Heart {
    bool alive = false;
    float x = 0, y = 0, age = 0;
  };

  bool sleepLook() const { return asleep_; }        // eyes shut, breathing, Z's
  Emotion shown() const { return emotion_; }
  void stepAnimation(uint32_t nowMs);
  void stepColor();
  void render();
  void drawEye(const EyeShape& e, float cx, float cy, int inner, float hScale);
  void drawMouth(int cx, int cy, int eyeBottom);
  void drawFlourishes(float leftOuter, float rightOuter, int eyeTop, int eyeBottom);
  void drawZeds();
  void drawPanel(const Panel& p, uint8_t ink, uint8_t rimInk);
  void drawBackdrop();
  // Drawing in face pixels: blocks for the pixel-art bits, smooth shapes at
  // the LCD's resolution for the rest.
  void dot(int x, int y, uint8_t ink);
  void roundBox(float x, float y, float w, float h, float r, uint8_t ink);
  void line(float x0, float y0, float x1, float y1, uint8_t ink);  // between pixel centres, a pixel thick
  void disc(float cx, float cy, float r, uint8_t ink);
  void glyph(const char* const* rows, int n, int x, int y, uint8_t ink);

  FaceGfx& gfx_;
  FacePack pack_;
  Emotion emotion_ = Emotion::Neutral;
  EyeShape curL_, curR_;
  uint32_t emotionSinceMs_ = 0;
  bool still_ = false;

  // The eyes' colour, eased toward the emotion's.
  float colR_ = 244, colG_ = 241, colB_ = 234;

  // Blink state: 0 = open, 1 = fully closed.
  float blinkAmount_ = 0.0f;
  bool blinkClosing_ = false;
  uint32_t nextBlinkMs_ = 0;

  // Gaze offset from center, eased toward gazeTarget.
  float gazeX_ = 0, gazeY_ = 0;
  float gazeTargetX_ = 0, gazeTargetY_ = 0;
  uint32_t nextSaccadeMs_ = 0;

  // Pupils (packs with `glint`): where they look inside the eye, -1..1 of the room they have, eased toward the
  // target. They dart about on their own, more often than the whole eye glances, as if he's looking at things.
  float pupilX_ = 0, pupilY_ = 0;
  float pupilTargetX_ = 0, pupilTargetY_ = 0;
  uint32_t nextPupilMs_ = 0;

  // Flourish state.
  float pop_ = 0.0f;            // overshoot on emotion change, decays to 0
  float squint_ = 0.0f;         // 1 while squinting, decays
  uint32_t nextSquintMs_ = 0;
  float tearY_ = -1.0f;         // -1 = no tear; else its y
  uint32_t nextTearMs_ = 0;
  uint8_t thinkDots_ = 0;
  uint32_t nextDotMs_ = 0;
  int jitterX_ = 0;
  Heart hearts_[3];
  uint32_t nextHeartMs_ = 0;
  bool heartSide_ = false;

  // Sleep.
  bool asleep_ = false;
  float breath_ = 0.0f;         // phase
  Zed zeds_[3];
  uint32_t nextZedMs_ = 0;
  uint32_t nextTwitchMs_ = 0;
  float twitch_ = 0.0f;

  // Mouth.
  bool talking_ = false;
  float mouthLevel_ = 0.0f;
  float mouth_ = 0.0f;          // eased openness 0..1
  float smile_ = 0.0f;          // eased resting-mouth curve
  float tilt_ = 0.0f;           // eased resting-mouth tilt, degrees (pack mouthTilt), and where it's heading
  float tiltTarget_ = 0.0f;

  bool idle_ = true;
  bool listening_ = false;
  float listen_ = 0.0f;         // eased 0..1
  bool backdropDirty_ = true;
  uint32_t lastFrameMs_ = 0;
  uint32_t frame_ = 0;
};
