#pragma once

#include <Arduino.h>
#include <driver/i2s_tdm.h>
#include <freertos/stream_buffer.h>

// Duplex: the CoreS3's shared I2S bus run both ways at once, so the robot can hear while it talks.
//
// M5Unified runs the mic and the speaker one at a time on that bus. Here one I2S port does both, in 4-slot TDM
// frames (16 kHz, 16-bit, 64 BCK a frame): the ES7210 sends MIC1, MIC3, MIC2, MIC4 (as measured), where MIC3 is
// wired to the speaker amp's output (AEC_P/N on the schematic): the echo reference, what the speaker really
// played. The AW88298 amp reads slot 0 as its left channel.
//
// The mic task owns the bus (Mic::task calls transfer() every 30 ms); the speaker task hands it samples through
// play(), a small FIFO. Whatever the FIFO doesn't have is played as silence. The brain cancels the echo: while the
// robot talks it gets the mic and the reference together (docs/protocol.md, 0x04 frames).
//
// The mics' gain (the ES7210's PGA, 3 dB a step from 0x10 = 0 dB) is lower than M5Unified's (0x1B, +33 dB) so the
// echo doesn't clip; micScale() makes up the difference in software for everything but the brain's echo canceller,
// which wants the raw samples (it scales after cancelling).

class Duplex {
 public:
  // Whether to try duplex at all (kept in flash: `duplex on|off`, then a restart). Read before begin().
  void load();
  void save(bool on);
  bool wanted() const { return wanted_; }

  // From the mic task: takes the bus (M5Unified's mic and speaker must be stopped) and sets the chips up. False if
  // the I2S port wouldn't start; the robot then runs as before, one way at a time.
  bool open();
  bool running() const { return running_; }

  // From the mic task: plays the next n samples from the FIFO (silence if it runs short) and records n frames: the
  // two mics averaged (raw, at the PGA's gain) and the reference. n up to 480. Blocks for about n / 16 ms.
  bool transfer(int16_t* mic, int16_t* ref, size_t n);

  // The PGA in register steps (0x10 = 0 dB ... 0x1B = +33 dB); takes effect at once. The software gain that brings
  // the mics back up to M5Unified's level.
  void setMicPga(uint8_t reg);
  uint8_t micPga() const { return micPga_; }
  float micScale() const { return micScale_; }

  // From the speaker task: queue samples to play; waits up to waitMs for room. Returns how many were taken.
  size_t play(const int16_t* s, size_t n, uint32_t waitMs);
  // Samples queued that the bus hasn't taken yet.
  size_t pending() const;
  // Drop what's queued (stop mid-line).
  void flush();
  // Loudness (RMS 0..1, as queued: after the volume) of what the speaker is playing right now: measured as each
  // block goes to the bus, held back by the DMA's latency so it matches the sound (the face and light bars).
  float playingLevel() const { return levels_[levelAt_]; }
  // When the speaker last had something to play (millis()); 0 = never.
  uint32_t lastPlayedMs() const { return lastPlayedMs_; }
  // How long a sample takes from the FIFO to the speaker (the DMA queue), in ms.
  static const uint32_t LATENCY_MS = 90;
  // Times the mic task was too late since power-on: the speaker ran dry (it plays silence), the recording overflowed.
  uint32_t underruns() const { return underruns_; }
  uint32_t overflows() const { return overflows_; }

  volatile uint32_t underruns_ = 0;  // counted from the I2S interrupts
  volatile uint32_t overflows_ = 0;

 private:
  bool wanted_ = true;
  volatile bool running_ = false;
  i2s_chan_handle_t tx_ = nullptr, rx_ = nullptr;
  StreamBufferHandle_t fifo_ = nullptr;
  int16_t* out_ = nullptr;  // one block of TDM frames each way
  int16_t* in_ = nullptr;
  int16_t* mono_ = nullptr;
  uint8_t micPga_ = 0x13;
  float micScale_ = 1.0f;
  volatile uint32_t lastPlayedMs_ = 0;
  // The last few blocks' smoothed levels: the oldest is the one being heard (LATENCY_MS / 30 ms blocks back).
  static const int LEVEL_DELAY = 3;
  volatile float levels_[LEVEL_DELAY] = {};
  volatile int levelAt_ = 0;  // the oldest entry, overwritten next
  float level_ = 0.0f;
};
