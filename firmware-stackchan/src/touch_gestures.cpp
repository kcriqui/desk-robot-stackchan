#include "touch_gestures.h"

// A touch counts as lifted after this long with no zone reading (the sensor can drop a reading mid-touch).
static const uint32_t RELEASE_MS = 80;
// Longer than this is no tap; resting this long is a hold, and this long again the long one.
static const uint32_t TAP_MAX_MS = 600, HOLD_MS = 1000, LONG_HOLD_MS = 3000;

void TouchGestures::update(uint32_t now, const std::array<uint8_t, 3>& levels, int swiped, const Send& send) {
  uint8_t level = max(levels[0], max(levels[1], levels[2]));
  if (level > 0) {
    if (!down_) {
      down_ = true;
      downAt_ = now;
      maxLevel_ = 0;
      zones_ = 0;
      swiped_ = false;
      holdSent_ = 0;
    }
    lastTouchAt_ = now;
    maxLevel_ = max(maxLevel_, level);
    for (int i = 0; i < 3; ++i)
      if (levels[i]) zones_ |= 1 << i;
  }
  if (swiped) {
    swiped_ = true;
    send(swiped > 0 ? "{\"type\":\"touch\",\"g\":\"swipe\",\"dir\":\"fwd\"}"
                    : "{\"type\":\"touch\",\"g\":\"swipe\",\"dir\":\"back\"}");
  }
  if (!down_) return;

  if (level > 0) {  // still touching: a hand resting there (not stroking) is a hold
    uint32_t held = now - downAt_;
    if (!swiped_ && holdSent_ < 1 && held >= HOLD_MS) {
      holdSent_ = 1;
      send("{\"type\":\"touch\",\"g\":\"hold\",\"s\":1}");
    }
    if (!swiped_ && holdSent_ < 3 && held >= LONG_HOLD_MS) {
      holdSent_ = 3;
      send("{\"type\":\"touch\",\"g\":\"hold\",\"s\":3}");
    }
    return;
  }
  if (now - lastTouchAt_ < RELEASE_MS) return;

  down_ = false;  // lifted
  uint32_t ms = lastTouchAt_ - downAt_;
  if (holdSent_) {
    send("{\"type\":\"touch\",\"g\":\"release\"}");
  } else if (!swiped_ && ms <= TAP_MAX_MS) {
    char msg[80];
    snprintf(msg, sizeof msg, "{\"type\":\"touch\",\"g\":\"tap\",\"zones\":%u,\"level\":%u,\"ms\":%u}",
             static_cast<unsigned>(zones_), static_cast<unsigned>(maxLevel_), static_cast<unsigned>(ms));
    send(msg);
  }
}
