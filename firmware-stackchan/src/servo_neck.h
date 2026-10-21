#pragma once

#include <Arduino.h>

// One head axis (pan or tilt) on the StackChan's SCS0009 serial-bus servos.
//
// Same job and interface as firmware/src/servo_neck.h, but the motion itself
// is the StackChan BSP's: it smooths moves with a spring, holds position with
// torque, and remembers each servo's zero point (factory calibration) in NVS.
// This class keeps the desk-robot's limits, direction flips and idle glances.

class ServoNeck {
 public:
  enum class Axis : uint8_t { Pan, Tilt };

  // restDeg: the pose it goes to at boot.
  void begin(Axis axis, float minDeg, float maxDeg, int speed,
             float glanceRangeDeg, bool invert, float restDeg = 0.0f);

  void setTarget(float deg);
  // Calibration only: go straight to a head angle, ignoring our limits (the
  // BSP still keeps the servo inside its own mechanical range).
  void setRaw(float deg);
  float current() const { return commandedDeg_; }
  float target() const { return targetDeg_; }
  // BSP spring speed, 0-1000; applies from the next move.
  void setSpeed(int speed) { speed_ = constrain(speed, 0, 1000); }
  int speed() const { return speed_; }

  // When idle glances are on, the head occasionally turns a little on its
  // own — paired with the face saccades it makes the robot feel alive.
  void setIdleGlances(bool on);

  // Call every frame.
  void update(uint32_t nowMs);

 private:
  void send(float deg);

  Axis axis_ = Axis::Pan;
  float minDeg_ = -90, maxDeg_ = 90;
  int speed_ = 500;
  float glanceRange_ = 25;
  bool invert_ = false;

  float targetDeg_ = 0;     // where we were told to go
  float restDeg_ = 0;       // where glances wander around and return to
  float commandedDeg_ = 0;  // last angle sent to the servo
  bool dirty_ = false;
  uint32_t lastSendMs_ = 0;
  uint32_t nextGlanceMs_ = 0;
  bool idleGlances_ = false;
};
