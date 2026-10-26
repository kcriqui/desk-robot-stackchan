#pragma once

#include <Arduino.h>

// One head axis (pan or tilt) on the StackChan's SCS0009 serial-bus servos.
//
// Same job and interface as firmware/src/servo_neck.h, but the motion itself
// is the StackChan BSP's: it smooths moves with a spring, holds position with
// torque, and remembers each servo's zero point (factory calibration) in NVS.
// This class keeps the desk-robot's limits and direction flips. (Idle moves are
// the brain's body language, brain/body.py.)

class ServoNeck {
 public:
  enum class Axis : uint8_t { Pan, Tilt };

  // restDeg: the pose it goes to at boot.
  void begin(Axis axis, float minDeg, float maxDeg, int speed, bool invert, float restDeg = 0.0f);

  void setTarget(float deg);
  // Calibration only: go straight to a head angle, ignoring our limits (the
  // BSP still keeps the servo inside its own mechanical range).
  void setRaw(float deg);
  float current() const { return commandedDeg_; }
  float target() const { return targetDeg_; }
  // BSP spring speed, 0-1000; applies from the next move.
  void setSpeed(int speed) { speed_ = constrain(speed, 0, 1000); }
  int speed() const { return speed_; }

  // The next move starts from where the servo really is (read over the bus), not from where the BSP last put it:
  // after the servos' power comes back (deep sleep), when the head may have drooped.
  // holdMs: the servos just got their power back and take a moment to answer on the bus: moves wait that long,
  // or the read fails and the BSP starts the move from where it last put the head.
  void resync(uint32_t nowMs, uint32_t holdMs) {
    syncNext_ = true;
    holdUntilMs_ = (nowMs + holdMs) | 1;
  }

  // Call every frame.
  void update(uint32_t nowMs);

 private:
  void send(float deg);

  Axis axis_ = Axis::Pan;
  float minDeg_ = -90, maxDeg_ = 90;
  int speed_ = 500;
  bool invert_ = false;

  float targetDeg_ = 0;     // where we were told to go
  float commandedDeg_ = 0;  // last angle sent to the servo
  bool dirty_ = false;
  bool syncNext_ = true;    // the first move reads the servo too (see send())
  uint32_t holdUntilMs_ = 0;  // no moves before this (0 = none): see resync()
  uint32_t lastSendMs_ = 0;
};
