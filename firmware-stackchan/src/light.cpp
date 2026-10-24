#include "light.h"

#include <M5Unified.h>

namespace {
constexpr uint8_t ADDR = 0x23;
constexpr uint32_t FREQ = 400000;
// Registers (LTR-553ALS-01 datasheet).
constexpr uint8_t ALS_CONTR = 0x80, PS_CONTR = 0x81, PS_LED = 0x82, PS_N_PULSES = 0x83, PS_MEAS_RATE = 0x84, ALS_MEAS_RATE = 0x85,
                  PART_ID = 0x86, ALS_DATA = 0x88, ALS_PS_STATUS = 0x8C, PS_DATA = 0x8D;
constexpr float ALS_GAIN = 8;   // ALS_CONTR gain 8x: 0.125-8k lux, fine for a room after dark
}  // namespace

bool LightSensor::begin() {
  uint8_t id = M5.In_I2C.readRegister8(ADDR, PART_ID, FREQ);
  if ((id >> 4) != 0x9) {  // 0x92: part 9, revision 2
    Serial.printf("light: no LTR-553 (part id 0x%02X)\n", id);
    return false;
  }
  bool good = M5.In_I2C.writeRegister8(ADDR, ALS_MEAS_RATE, 0x02, FREQ)   // 100 ms integration, every 200 ms
              && M5.In_I2C.writeRegister8(ADDR, PS_MEAS_RATE, 0x02, FREQ)  // proximity every 100 ms
              && M5.In_I2C.writeRegister8(ADDR, PS_LED, 0x7F, FREQ)        // LED 60 kHz, 100% duty, 100 mA
              && M5.In_I2C.writeRegister8(ADDR, PS_N_PULSES, 0x0F, FREQ)   // 15 pulses (most): all the reach it has
              && M5.In_I2C.writeRegister8(ADDR, ALS_CONTR, 0x0D, FREQ)     // active, gain 8x
              && M5.In_I2C.writeRegister8(ADDR, PS_CONTR, 0x03, FREQ);     // proximity active
  ok_ = good;
  Serial.printf("light: LTR-553 %s\n", good ? "on" : "didn't take its settings");
  return ok_;
}

void LightSensor::update(uint32_t nowMs) {
  if (!ok_ || static_cast<int32_t>(nowMs - nextMs_) < 0) return;
  nextMs_ = nowMs + 100;
  uint8_t ps[2];
  if (M5.In_I2C.readRegister(ADDR, PS_DATA, ps, 2, FREQ)) {
    prox_ = ps[0] | ((ps[1] & 0x07) << 8);
    readings_++;
  }
  // Only a new, valid light reading (bit 7: invalid, bit 2: new): reading one mid-update gave 5-8k lux spikes.
  uint8_t st = M5.In_I2C.readRegister8(ADDR, ALS_PS_STATUS, FREQ);
  if ((st & 0x80) || !(st & 0x04)) return;
  uint8_t als[4];  // channel 1 then channel 0, low byte first
  if (!M5.In_I2C.readRegister(ADDR, ALS_DATA, als, 4, FREQ)) return;
  float ch1 = als[0] | (als[1] << 8), ch0 = als[2] | (als[3] << 8);
  float sum = ch0 + ch1, ratio = sum > 0 ? ch1 / sum : 0, lux;
  if (ratio < 0.45f) lux = 1.7743f * ch0 + 1.1059f * ch1;  // the datasheet's piecewise formula
  else if (ratio < 0.64f) lux = 4.2785f * ch0 - 1.9548f * ch1;
  else if (ratio < 0.85f) lux = 0.5926f * ch0 + 0.1185f * ch1;
  else lux = 0;
  lux = fmaxf(0, lux / ALS_GAIN);
  recent_[recentN_++ % 3] = lux;
  if (recentN_ < 3) return;
  if (recentN_ >= 6) recentN_ = 3;
  float a = recent_[0], b = recent_[1], c = recent_[2];
  float mid = fmaxf(fminf(a, b), fminf(fmaxf(a, b), c));  // the median
  lux_ = luxStarted_ ? lux_ + (mid - lux_) * 0.25f : mid;  // then smoothed: a passing shadow doesn't count
  luxStarted_ = true;
}

void LightSensor::printStatus() const {
  if (!ok_) {
    Serial.println(F("light: no sensor"));
    return;
  }
  Serial.printf("light: %.1f lux, proximity %u\n", lux_, prox_);
}
