#pragma once

#include <Arduino.h>

#include <array>
#include <functional>

// The touch strip on top of the head (three zones: front, middle, back; the BSP's Si12T), as raw gestures for the
// brain, which decides what they mean (main.py on_touch: a pat, a double tap, a bonk, petting, a scratch, a hold):
//   {"type":"touch","g":"tap","zones":<bits>,"level":<1-3>,"ms":<n>}   a short touch, sent when it lifts
//   {"type":"touch","g":"swipe","dir":"fwd"|"back"}                     a stroke along the head (the BSP's)
//   {"type":"touch","g":"hold","s":1|3}                                 a touch resting 1 s, then 3 s
//   {"type":"touch","g":"release"}                                      the end of a hold
class TouchGestures {
 public:
  using Send = std::function<void(const char* json)>;

  // Every loop. levels: each zone's 0-3 (front, middle, back); swiped: the BSP's swipe this update (+1 front to
  // back, -1 back to front, 0 none).
  void update(uint32_t nowMs, const std::array<uint8_t, 3>& levels, int swiped, const Send& send);

 private:
  bool down_ = false;
  uint32_t downAt_ = 0, lastTouchAt_ = 0;
  uint8_t maxLevel_ = 0, zones_ = 0;
  bool swiped_ = false;
  uint8_t holdSent_ = 0;  // 0, then 1 (1 s), then 3 (3 s)
};
