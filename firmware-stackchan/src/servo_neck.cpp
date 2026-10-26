#include "servo_neck.h"

#include <M5StackChan.h>

// Face tracking sends a new angle every camera frame. Each move restarts the
// BSP's spring, so pass on at most one per this interval.
static const uint32_t SEND_INTERVAL_MS = 40;

void ServoNeck::begin(Axis axis, float minDeg, float maxDeg, int speed, bool invert, float restDeg) {
  axis_ = axis;
  minDeg_ = minDeg;
  maxDeg_ = maxDeg;
  speed_ = constrain(speed, 0, 1000);
  invert_ = invert;
  targetDeg_ = constrain(restDeg, minDeg_, maxDeg_);
  dirty_ = true;  // go to the start pose on the first update
}

void ServoNeck::setTarget(float deg) {
  targetDeg_ = constrain(deg, minDeg_, maxDeg_);
  dirty_ = true;
}

void ServoNeck::setRaw(float deg) {
  targetDeg_ = deg;
  dirty_ = true;
  lastSendMs_ = 0;  // right now
}

void ServoNeck::send(float deg) {
  // BSP angles are tenths of a degree.
  int a10 = static_cast<int>(lroundf((invert_ ? -deg : deg) * 10.0f));
  // The BSP reads each servo's position once, at M5StackChan.begin(), and its spring starts from there. Right after
  // power-up the servo often doesn't answer yet and it takes 0 instead: for the tilt that's the bottom of its range,
  // so the first move snapped the head down to the limit and back up. So the first move (and the first after the
  // servos' power returns) reads the servo again; the others don't (tracking's stream of moves stutters if they do).
  if (syncNext_) M5StackChan.Motion.setAutoAngleSyncEnabled(true);
  if (axis_ == Axis::Pan) {
    M5StackChan.Motion.moveYaw(a10, speed_);
  } else {
    M5StackChan.Motion.movePitch(a10, speed_);
  }
  if (syncNext_) {
    M5StackChan.Motion.setAutoAngleSyncEnabled(false);
    syncNext_ = false;
  }
  commandedDeg_ = deg;
}

void ServoNeck::update(uint32_t nowMs) {
  if (holdUntilMs_) {
    if (static_cast<int32_t>(nowMs - holdUntilMs_) < 0) return;  // the servos are still starting up
    holdUntilMs_ = 0;
  }
  if (dirty_ && nowMs - lastSendMs_ >= SEND_INTERVAL_MS) {
    send(targetDeg_);
    dirty_ = false;
    lastSendMs_ = nowMs;
  }
}
