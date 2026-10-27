#pragma once

#include <Arduino.h>

#include <atomic>
#include <functional>

// Mic: the CoreS3's ES7210 microphones → 16 kHz mono s16le frames.
//
// A capture task records 30 ms chunks through M5Unified and drops each one
// into a queue; loop() drains the queue and ships frames to the brain
// (the WebSocket client isn't safe to call from another task).
//
// On the CoreS3 the mic and the speaker share one I2S bus. With Duplex (duplex.h) running, the capture task owns
// it both ways and plays what the speaker queues; while the robot talks (and the brain asked for it) the frames
// carry the echo reference too, for the brain's echo canceller. Without it, through M5Unified, they can't run at
// once: the speaker calls release() before it plays and reclaim() after, and the brain mutes the mic meanwhile.

class Duplex;

class Mic {
 public:
  static const size_t FRAME_SAMPLES = 480;  // 30 ms at 16 kHz
  static const size_t FRAME_BYTES = FRAME_SAMPLES * 2;
  // The largest frame nextFrame() hands out: 0x04, a uint16 gain (x256) then the mic and the reference interleaved.
  static const size_t MAX_FRAME_BYTES = 2 + 2 * FRAME_BYTES;

  // duplex: run the bus both ways if it's wanted and starts (null: M5Unified, one way at a time).
  void begin(float gain, Duplex* duplex = nullptr);
  void setStreaming(bool on) { streaming_ = on; }
  bool streaming() const { return streaming_; }

  // Every captured frame also goes to `tap` (from the capture task), e.g.
  // the wake-word engine. Set once, before audio matters.
  using Tap = std::function<void(const int16_t* samples, size_t n)>;
  void setTap(Tap tap) { tap_ = std::move(tap); }

  // Start streaming with the last PREROLL_FRAMES (2 s) that weren't sent,
  // so the brain hears the wake word and what came right after it. The
  // capture task copies them out; loop() sends them with nextPreroll()
  // before any newer frame from nextFrame().
  static const size_t PREROLL_FRAMES = 66;
  void startWithPreroll() { prerollWanted_ = true; }
  bool nextPreroll(uint8_t* out);

  // Pops one captured frame into `out` (MAX_FRAME_BYTES of room): returns its type (0x01 mic audio, 0x04 the mic
  // and the echo reference; docs/protocol.md) and sets len, or returns 0 if there's none.
  uint8_t nextFrame(uint8_t* out, size_t& len);

  // The brain cancels the echo itself: while the speaker plays (and DUPLEX_REF_TAIL_MS after), send 0x04 frames.
  void setRefWanted(bool on) { refWanted_ = on; }

  // RMS (0..1) of the most recent frame, for level checks over serial.
  float level() const { return level_; }

  // Hand the I2S bus to the speaker: blocks until the mic has stopped
  // (or timeoutMs passes; returns whether it stopped). reclaim() restarts it.
  bool release(uint32_t timeoutMs = 300);
  void reclaim() { wantBus_ = true; }

 private:
  struct Queued {
    uint8_t type;
    uint16_t len;
    uint8_t data[MAX_FRAME_BYTES];
  };

  static void taskEntry(void* self);
  void task();
  void runDuplex();
  // A finished mono frame (and, in duplex, the raw mic and the reference): to the tap, the preroll, the queue.
  void deliver(int16_t* frame, const int16_t* rawMic, const int16_t* ref, float rawGain);
  void enqueue(Queued& q);

  QueueHandle_t queue_ = nullptr;
  Tap tap_;
  int16_t* ring_ = nullptr;   // PSRAM: the last PREROLL_FRAMES frames
  size_t ringHead_ = 0;       // next slot to write
  size_t ringCount_ = 0;
  int16_t* preroll_ = nullptr;  // PSRAM: a copy of the ring, oldest first
  std::atomic<bool> prerollWanted_{false};
  std::atomic<size_t> prerollCount_{0};
  size_t prerollSent_ = 0;
  volatile bool streaming_ = false;
  volatile float level_ = 0.0f;
  volatile bool wantBus_ = true;   // set by the speaker side
  volatile bool hasBus_ = false;   // set by the capture task
  float gain_ = 1.0f;
  Duplex* duplex_ = nullptr;
  volatile bool refWanted_ = false;
  Queued* scratch_ = nullptr;  // PSRAM: a frame being put together, and one dropped when the queue is full
  Queued* dropped_ = nullptr;
};
