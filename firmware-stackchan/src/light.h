#pragma once

#include <Arduino.h>

// The CoreS3's ambient light and proximity sensor: one Lite-On LTR-553ALS on the internal I2C bus (0x23), just to
// the right of the camera, looking out. The light level tells the clock screen when the room is dark (night mode);
// the proximity reading, how near something is (a hand), wakes a sleeping robot. Read every 100 ms from loop().
// Its proximity reach is short: with 400 as "near", only ~1 cm (the user, 2026-10-24); a hand covering it also
// darkens the light reading.
//
// Shares the bus with the camera's control lines, which only use it while the camera starts.

class LightSensor {
 public:
  bool begin();                 // false if it doesn't answer (the readings then stay 0)
  void update(uint32_t nowMs);
  bool ok() const { return ok_; }
  float lux() const { return lux_; }             // smoothed, roughly lux (the cover glass dims it a little)
  uint16_t proximity() const { return prox_; }  // 0-2047: higher is nearer
  uint32_t readings() const { return readings_; }  // counts proximity readings, to act on each new one
  void printStatus() const;

 private:
  bool ok_ = false;
  float lux_ = 0;
  bool luxStarted_ = false;
  float recent_[3] = {};  // the last three readings: the middle one counts, so a one-off spike doesn't
  uint8_t recentN_ = 0;
  uint16_t prox_ = 0;
  uint32_t readings_ = 0;
  uint32_t nextMs_ = 0;
};
