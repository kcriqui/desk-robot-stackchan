#include "servo_neck.h"

#include <M5StackChan.h>

// Face tracking sends a new angle every camera frame. Each move restarts the
// BSP's spring, so pass on at most one per this interval.
static const uint32_t SEND_INTERVAL_MS = 40;

void ServoNeck::begin(Axis axis, float minDeg, float maxDeg, int speed,
                      float glanceRangeDeg, bool invert) {
  axis_ = axis;
  minDeg_ = minDeg;
  maxDeg_ = maxDeg;
  speed_ = constrain(speed, 0, 1000);
  glanceRange_ = glanceRangeDeg;
  invert_ = invert;
  targetDeg_ = restDeg_ = constrain(0.0f, minDeg_, maxDeg_);
  nextGlanceMs_ = millis() + 5000;
  dirty_ = true;  // go to the start pose on the first update
}

void ServoNeck::setTarget(float deg) {
  targetDeg_ = restDeg_ = constrain(deg, minDeg_, maxDeg_);
  dirty_ = true;
}

void ServoNeck::setRaw(float deg) {
  targetDeg_ = restDeg_ = deg;
  dirty_ = true;
  lastSendMs_ = 0;  // right now
}

void ServoNeck::setIdleGlances(bool on) {
  if (idleGlances_ && !on && targetDeg_ != restDeg_) {
    targetDeg_ = restDeg_;  // come back from a glance in progress
    dirty_ = true;
  }
  idleGlances_ = on;
}

void ServoNeck::send(float deg) {
  // BSP angles are tenths of a degree.
  int a10 = static_cast<int>(lroundf((invert_ ? -deg : deg) * 10.0f));
  if (axis_ == Axis::Pan) {
    M5StackChan.Motion.moveYaw(a10, speed_);
  } else {
    M5StackChan.Motion.movePitch(a10, speed_);
  }
  commandedDeg_ = deg;
}

void ServoNeck::update(uint32_t nowMs) {
  if (idleGlances_ && nowMs >= nextGlanceMs_) {
    // Small wander around the rest pose, half the time straight back to it.
    int range = static_cast<int>(glanceRange_);
    float glance = static_cast<float>(random(-range, range + 1));
    if (random(100) < 50) glance = 0;
    targetDeg_ = constrain(restDeg_ + glance, minDeg_, maxDeg_);
    dirty_ = true;
    nextGlanceMs_ = nowMs + random(6000, 16000);
  }

  if (dirty_ && nowMs - lastSendMs_ >= SEND_INTERVAL_MS) {
    send(targetDeg_);
    dirty_ = false;
    lastSendMs_ = nowMs;
  }
}
