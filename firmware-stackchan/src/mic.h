#pragma once

#include <Arduino.h>

// Mic: the CoreS3's ES7210 microphones → 16 kHz mono s16le frames.
//
// A capture task records 30 ms chunks through M5Unified and drops each one
// into a queue; loop() drains the queue and ships frames to the brain
// (the WebSocket client isn't safe to call from another task).
//
// On the CoreS3 the mic and the speaker share one I2S bus and can't run at
// once. The speaker calls release() before it plays and reclaim() after;
// the brain mutes the mic while the robot talks anyway, so nothing is lost.

class Mic {
 public:
  static const size_t FRAME_SAMPLES = 480;  // 30 ms at 16 kHz
  static const size_t FRAME_BYTES = FRAME_SAMPLES * 2;

  void begin(float gain);
  void setStreaming(bool on) { streaming_ = on; }
  bool streaming() const { return streaming_; }

  // Pops one captured frame into `out` (FRAME_BYTES). Returns false if none.
  bool nextFrame(uint8_t* out);

  // RMS (0..1) of the most recent frame, for level checks over serial.
  float level() const { return level_; }

  // Hand the I2S bus to the speaker: blocks until the mic has stopped
  // (or timeoutMs passes; returns whether it stopped). reclaim() restarts it.
  bool release(uint32_t timeoutMs = 300);
  void reclaim() { wantBus_ = true; }

 private:
  static void taskEntry(void* self);
  void task();

  QueueHandle_t queue_ = nullptr;
  volatile bool streaming_ = false;
  volatile float level_ = 0.0f;
  volatile bool wantBus_ = true;   // set by the speaker side
  volatile bool hasBus_ = false;   // set by the capture task
  float gain_ = 1.0f;
};
