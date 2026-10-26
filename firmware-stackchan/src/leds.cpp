#include "leds.h"

#include <M5StackChan.h>

#include "config.h"

namespace {

constexpr uint32_t FRAME_MS = 25;
constexpr uint32_t DRIFT_FRAME_MS = 50;
constexpr uint32_t CYLON_STEP_MS = 400;  // the scanner's light moves one LED this often: a sweep, up and back, in 4 s
constexpr uint32_t DANCE_TIMEOUT_MS = 60000;  // a dance whose end never came
constexpr uint32_t DANCE_STEP_MS = 150;       // each dance pattern holds this long: long enough to see
// Full colours only: anything mixed shifts in RGB565's few steps.
constexpr float DANCE_COLOURS[6][3] = {{1, 0, 0}, {1, 1, 0}, {0, 1, 0}, {0, 1, 1}, {0, 0, 1}, {1, 0, 1}};
constexpr int PER_BAR = 6;
constexpr float TWO_PI_F = 6.2831853f;  // Arduino's PI is a double: software maths on the ESP32-S3

// Position along a bar (0 = front) to the LED's index, on each side (config.h: 0-5 go front to back on the robot's
// right, 6-11 come back to the front on its left).
int ledIndex(int side, int pos) { return side == 0 ? pos : 2 * PER_BAR - 1 - pos; }

// Time since `at`, 0 if `at` is later: a command handled after the loop read its clock is stamped a moment ahead
// of the `nowMs` it passes here, and an unsigned difference wrapped round to ~49 days (cancelling it at once).
uint32_t since(uint32_t nowMs, uint32_t at) { return static_cast<int32_t>(nowMs - at) > 0 ? nowMs - at : 0; }

void hueToRgb(float h, float out[3]) {  // h 0..1, full saturation and value
  h = h - floorf(h);
  float r = fabsf(h * 6 - 3) - 1, g = 2 - fabsf(h * 6 - 2), b = 2 - fabsf(h * 6 - 4);
  out[0] = constrain(r, 0.0f, 1.0f);
  out[1] = constrain(g, 0.0f, 1.0f);
  out[2] = constrain(b, 0.0f, 1.0f);
}

}  // namespace

void Leds::alert(Alert a, uint32_t nowMs) {
  alert_ = a;
  alertAt_ = nowMs;
}

void Leds::setDance(bool on, uint32_t nowMs) {
  dance_ = on;
  danceAt_ = nowMs;
  danceStep_ = UINT32_MAX;
}

void Leds::beat() { colour_ = (colour_ + 1) % 6; }

void Leds::test(uint32_t nowMs) { testAt_ = nowMs | 1; }

void Leds::printStatus() const {
  Serial.printf("leds: brightness %u%%, asleep %s, %s\n", brightness_, asleepGlow_ ? "a scanner" : "off",
                dance_ ? "dancing" : alert_ != Alert::None ? "an alert" : "body language");
}

void Leds::update(uint32_t nowMs, uint32_t eye, bool asleep, bool deepSleep, bool thinking, bool speaking,
                  float level) {
  if (static_cast<int32_t>(nowMs - nextMs_) < 0) return;
  nextMs_ = nowMs + FRAME_MS;
  const float t = nowMs / 1000.0f;
  float base[3] = {((eye >> 16) & 0xFF) / 255.0f, ((eye >> 8) & 0xFF) / 255.0f, (eye & 0xFF) / 255.0f};
  float out[12][3] = {};
  bool drift = false;  // awake: each LED drifting through colours (drawn after the brightness, below)
  auto fill = [&](const float c[3], float k) {
    for (int i = 0; i < 12; ++i)
      for (int j = 0; j < 3; ++j) out[i][j] = c[j] * k;
  };

  if (dance_ && since(nowMs, danceAt_) > DANCE_TIMEOUT_MS) dance_ = false;
  if (testAt_ && since(nowMs, testAt_) > 12 * 600) testAt_ = 0;
  uint32_t sinceAlert = since(nowMs, alertAt_);
  if (alert_ != Alert::None &&
      sinceAlert > (alert_ == Alert::Hush ? 500u : alert_ == Alert::Timer ? 2400u : 3600u))
    alert_ = Alert::None;

  if (deepSleep || brightness_ == 0) {
    // all off
  } else if (testAt_) {  // one at a time, red to violet: LED 0 first
    int i = since(nowMs, testAt_) / 600;
    float c[3];
    hueToRgb(i / 12.0f, c);
    for (int j = 0; j < 3; ++j) out[i][j] = c[j];
  } else if (alert_ == Alert::Timer) {  // four bright flashes, in its colour
    bool on = (sinceAlert / 300) % 2 == 0;
    float white[3] = {1, 1, 1};
    fill(on ? white : base, on ? 1.0f : 0.15f);
  } else if (alert_ == Alert::Battery || alert_ == Alert::Hot) {  // three slow pulses, amber or red
    float c[3] = {1.0f, alert_ == Alert::Battery ? 0.55f : 0.08f, 0.0f};
    float k = 0.5f - 0.5f * cosf(sinceAlert / 1200.0f * TWO_PI_F);
    fill(c, 0.1f + 0.9f * k);
  } else if (alert_ == Alert::Hush) {  // a quick dip to dark and back
    float k = fabsf(sinceAlert / 250.0f - 1.0f);
    fill(base, 0.5f * k);
  } else if (dance_) {  // simple patterns, one step every 150 ms, a new colour on each head move
    uint32_t step = since(nowMs, danceAt_) / DANCE_STEP_MS;
    if (step != danceStep_) {  // a step holds its pattern and colour till the next one
      danceStep_ = step;
      shownColour_ = colour_;
    }
    const float* c = DANCE_COLOURS[shownColour_];
    const float* other = DANCE_COLOURS[(shownColour_ + 3) % 6];  // its opposite
    int mode = (step / 24) % 3;                                  // a new pattern every 3.6 s
    for (int side = 0; side < 2; ++side)
      for (int p = 0; p < PER_BAR; ++p) {
        const float* lit = nullptr;
        if (mode == 0) lit = p == static_cast<int>(step % PER_BAR) ? c : nullptr;  // one LED running front to back
        else if (mode == 1) lit = ((p + step / 2) & 1) ? c : other;              // alternating, swapping
        else lit = p < static_cast<int>(step % (PER_BAR + 1)) ? c : nullptr;     // filling front to back
        if (lit)
          for (int j = 0; j < 3; ++j) out[ledIndex(side, p)][j] = lit[j];
      }
  } else if (speaking) {  // a level meter: both bars fill from the back with the voice
    float want = constrain(level * 7.0f, 0.0f, 1.0f);
    meter_ = want > meter_ ? meter_ + (want - meter_) * 0.6f : meter_ + (want - meter_) * 0.15f;
    float lit = meter_ * PER_BAR;
    for (int side = 0; side < 2; ++side)
      for (int p = 0; p < PER_BAR; ++p) {
        float k = constrain(lit - (PER_BAR - 1 - p), 0.0f, 1.0f);
        k = 0.08f + 0.92f * k;
        for (int j = 0; j < 3; ++j) out[ledIndex(side, p)][j] = base[j] * k;
      }
  } else if (thinking) {  // a light sweeping front to back along both bars
    float phase = fmodf(t / 1.4f, 1.0f), pos = (phase < 0.5f ? phase * 2 : 2 - phase * 2) * (PER_BAR - 1);
    for (int side = 0; side < 2; ++side)
      for (int p = 0; p < PER_BAR; ++p) {
        float d = fabsf(p - pos), k = max(0.0f, 1.0f - d * 0.6f);
        k = 0.06f + 0.94f * k * k;
        for (int j = 0; j < 3; ++j) out[ledIndex(side, p)][j] = base[j] * k;
      }
  } else if (asleep) {  // off, or a Cylon scanner: a light sweeping front to back and back, trailing off
    if (asleepGlow_) {
      uint32_t n = nowMs / CYLON_STEP_MS;
      int at = n % 10, pos = at < PER_BAR ? at : 10 - at;  // 0..5..1, both sides together
      uint32_t sweep = n / 10;  // a sweep, up and back, is 10 steps
      if (sweep != cylonRound_) {  // each sweep shifts the colour a little (it starts at a random one)
        cylonHue_ = cylonRound_ == UINT32_MAX ? (esp_random() % 1000) / 1000.0f : cylonHue_ + 0.01f;
        cylonRound_ = sweep;
      }
      // One LED, no tail: the diffuser spread any tail both ways.
      for (int p = 0; p < PER_BAR; ++p) cylon_[p] = p == pos ? 1.0f : 0.0f;
      float c[3];
      hueToRgb(cylonHue_, c);
      for (int side = 0; side < 2; ++side)
        for (int p = 0; p < PER_BAR; ++p)
          for (int j = 0; j < 3; ++j) out[ledIndex(side, p)][j] = c[j] * cylon_[p];
    }
  } else {  // awake, listening: every LED drifting slowly through colours of its own
    drift = true;
    nextMs_ = nowMs + DRIFT_FRAME_MS;  // slow changes: fewer frames, less I2C
  }
  if (!speaking) meter_ *= 0.8f;

  // Brightness, and squared so fades look even to the eye.
  float scale = brightness_ / 100.0f;
  for (int i = 0; i < 12; ++i)
    for (int j = 0; j < 3; ++j) {
      float v = constrain(out[i][j], 0.0f, 1.0f);
      out[i][j] = v * v * scale;
    }
  // The drift: each LED eases from its colour to a random new one over 4-10 s, then picks another. Full colours
  // at the brightness, not squared: hue is what RGB565 does smoothly (one channel ramps at a time).
  if (drift) {
    for (int i = 0; i < 12; ++i) {
      uint32_t el = since(nowMs, driftAt_[i]);
      if (!driftMs_[i] || el >= driftMs_[i]) {  // arrived (or the first time): a new colour to head for
        driftFrom_[i] = driftMs_[i] ? driftTo_[i] : (esp_random() % 1000) / 1000.0f;
        float step = 0.15f + (esp_random() % 1000) / 1000.0f * 0.35f;  // 0.15-0.5 of the way round, either way
        driftTo_[i] = driftFrom_[i] + (esp_random() & 1 ? step : -step);
        driftAt_[i] = nowMs;
        driftMs_[i] = 4000 + esp_random() % 6000;
        el = 0;
      }
      float f = static_cast<float>(el) / driftMs_[i];
      f = f * f * (3 - 2 * f);  // eases in and out
      float c[3];
      hueToRgb(driftFrom_[i] + (driftTo_[i] - driftFrom_[i]) * f, c);
      for (int j = 0; j < 3; ++j) out[i][j] = c[j] * scale;
    }
  }
  show(out);
}

// The IO expander takes RGB565: only 32 steps for red and blue and 64 for green, so a slow, dim fade climbs in
// visible steps, and a colour wobbles as its channels step at different moments. (Dithering between steps
// flickered: the loop's frames aren't even. 0.6.9.8 tried it, with one bulk I2C write, which also garbled them.)
void Leds::show(const float rgb[12][3]) {
  uint16_t px[12];
  bool same = shownValid_;
  for (int i = 0; i < 12; ++i) {
    int r = constrain(static_cast<int>(lroundf(rgb[i][0] * 31)), 0, 31);
    int g = constrain(static_cast<int>(lroundf(rgb[i][1] * 63)), 0, 63);
    int b = constrain(static_cast<int>(lroundf(rgb[i][2] * 31)), 0, 31);
    px[i] = (r << 11) | (g << 5) | b;
    same &= px[i] == shown_[i];
  }
  if (same) return;  // nothing changed: no I2C traffic
  for (int i = 0; i < 12; ++i)  // only the LEDs that changed (each is an I2C write, ~0.4 ms at 100 kHz)
    if (!shownValid_ || px[i] != shown_[i])  // the library keeps the top bits: send each step's bottom value
      M5StackChan.setRgbColor(i, (px[i] >> 11) << 3, ((px[i] >> 5) & 0x3F) << 2, (px[i] & 0x1F) << 3);
  M5StackChan.refreshRgb();
  memcpy(shown_, px, sizeof px);
  shownValid_ = true;
}
