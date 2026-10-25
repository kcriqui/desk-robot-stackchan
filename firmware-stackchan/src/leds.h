#pragma once

#include <Arduino.h>

// The two light bars along the body's sides: 12 WS2812C LEDs (0-5 on its right, 6-11 on its left; config.h), driven
// through the body's IO expander over I2C. They're part of the robot's body language, drawn here about 40 times a
// second from what the robot already knows:
//   awake and listening   a slow fade from black to white and back (5 s)
//   thinking              a light sweeping front to back along both bars
//   talking               both bars fill from the back with the loudness of its voice
//   asleep                off, or a faint slow green breath (a setting)
//   dancing               simple patterns, 150 ms a step: a running light, alternating, filling; moves change the colour
//   alerts                a timer flashes them, low battery pulses amber, heat pulses red, a hush dips them
// Off in deep sleep. The brain sets the brightness and the asleep look (the `screen` message), starts and ends
// dances and sends alerts (the `leds` message).

class Leds {
 public:
  enum class Alert : uint8_t { None, Timer, Battery, Hot, Hush };

  void setBrightness(uint8_t pct) { brightness_ = min<uint8_t>(pct, 100); }  // 0 = off
  void setAsleepGlow(bool on) { asleepGlow_ = on; }
  void alert(Alert a, uint32_t nowMs);
  void setDance(bool on, uint32_t nowMs);
  bool dancing() const { return dance_; }
  void beat(uint32_t nowMs);          // a dance move: the next colour
  void test(uint32_t nowMs);          // one LED at a time, 0 to 11, to see which is where
  void printStatus() const;

  // Every loop (it draws at most every 25 ms). eyeRgb: the face's colour now (RGB888); level: the voice's
  // loudness (RMS 0..1) while speaking.
  void update(uint32_t nowMs, uint32_t eyeRgb, bool asleep, bool deepSleep, bool thinking, bool speaking,
              float level);

 private:
  void show(const float rgb[12][3]);

  uint8_t brightness_ = 30;
  bool asleepGlow_ = false;
  Alert alert_ = Alert::None;
  uint32_t alertAt_ = 0;
  bool dance_ = false;
  uint32_t danceAt_ = 0, danceStep_ = 0;
  uint8_t colour_ = 0, shownColour_ = 0;  // the dance's colour: the next one, and the step's
  uint32_t testAt_ = 0;
  float meter_ = 0;                  // the voice meter, eased (fast up, slower down)
  uint32_t nextMs_ = 0;
  uint16_t shown_[12] = {};         // RGB565, as last sent
  bool shownValid_ = false;
};
