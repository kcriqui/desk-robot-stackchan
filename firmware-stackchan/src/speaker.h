#pragma once

#include <Arduino.h>
#include <functional>

class Duplex;
class Mic;

// Speaker: plays 16 kHz mono s16le PCM through the CoreS3's AW88298 amp.
//
// The brain streams a whole reply as binary WebSocket frames. They land in a
// big ring buffer in PSRAM; a playback task drains it into M5Unified's
// speaker queue. A short pre-buffer hides WiFi jitter at the start of a line.
// The mic shares the I2S bus, so the playback task borrows it for each line; with Duplex (duplex.h) running it
// queues the samples for the bus instead, which plays and records at once.
//
//   beginSpeech()  → reset the buffer, start waiting for audio
//   feed(pcm, n)   → append audio (any chunk size)
//   endSpeech()    → no more audio coming; play out what's left, then idle
//   onDone         → called (from the playback task) when playback finishes

class Speaker {
 public:
  using DoneHandler = std::function<void()>;

  void begin(Mic* mic, float volume, DoneHandler onDone = nullptr, Duplex* duplex = nullptr);
  // expectedBytes: total size of the reply if the brain knows it (0 = unknown).
  // Playback starts once that much (capped at ~1 s) has arrived.
  void beginSpeech(size_t expectedBytes = 0);
  void feed(const uint8_t* pcm, size_t len);
  void endSpeech();
  // Stop now, mid-line: the queued audio is dropped and whatever the brain is still sending is ignored until the
  // next beginSpeech(). The usual done handler runs (the brain hears speak_done).
  void stop() { stopped_ = true; }
  bool speaking() const { return speaking_; }
  void setVolume(float v);
  // Loudness (RMS 0..1) of the audio being played right now; 0 when silent.
  float level() const;

  // Diagnostics for the last line played: times the buffer ran dry mid-line,
  // bytes buffered before playback started, ms spent waiting for that.
  uint32_t lastUnderruns() const { return underruns_; }
  size_t lastPrebuffered() const { return prebuffered_; }
  uint32_t lastPrebufferMs() const { return prebufferMs_; }
  // How much the next line buffers before it plays, in ms. It starts at
  // 400 ms after power-on, while the WiFi link settles, and adapts from there
  // (see task()).
  uint32_t headStartMs() const { return prebufferBytes_ / 32; }

 private:
  static void taskEntry(void* self);
  void task();
  size_t available() const;
  size_t readInto(uint8_t* out, size_t maxLen);
  float duplexGain() const;  // the samples' scale for the volume, in duplex

  Mic* mic_ = nullptr;
  Duplex* duplex_ = nullptr;
  uint8_t* ring_ = nullptr;
  size_t ringSize_ = 0;
  volatile size_t head_ = 0;  // write index
  volatile size_t tail_ = 0;  // read index
  volatile bool speaking_ = false;
  volatile bool ended_ = false;
  volatile bool stopped_ = false;
  size_t expectedBytes_ = 0;
  volatile uint32_t underruns_ = 0;   // 5 ms polls that found the buffer dry mid-line
  size_t prebufferBytes_ = 12800;     // 400 ms
  volatile size_t prebuffered_ = 0;
  volatile uint32_t prebufferMs_ = 0;
  uint32_t speechStartMs_ = 0;
  float volume_ = 0.5f;
  volatile float level_ = 0.0f;
  DoneHandler onDone_;
  portMUX_TYPE lock_ = portMUX_INITIALIZER_UNLOCKED;
};
