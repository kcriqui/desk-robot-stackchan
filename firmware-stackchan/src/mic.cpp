#include "mic.h"

#include <M5Unified.h>

static const int SAMPLE_RATE = 16000;
// record() queues up to two buffers and blocks until a slot frees, so once
// buffer i is queued, buffer i-2 is complete. Four in rotation leaves the
// finished one untouched while it's processed.
static const int NUM_BUFS = 4;

void Mic::begin(float gain) {
  gain_ = gain;
  queue_ = xQueueCreate(16, FRAME_BYTES);  // ~0.5 s of frames
  ring_ = static_cast<int16_t*>(ps_malloc(PREROLL_FRAMES * FRAME_BYTES));
  preroll_ = static_cast<int16_t*>(ps_malloc(PREROLL_FRAMES * FRAME_BYTES));
  xTaskCreatePinnedToCore(taskEntry, "mic", 4096, this, 2, nullptr, 0);
}

bool Mic::nextFrame(uint8_t* out) {
  return queue_ != nullptr && xQueueReceive(queue_, out, 0) == pdTRUE;
}

bool Mic::nextPreroll(uint8_t* out) {
  size_t count = prerollCount_.load();
  if (prerollSent_ >= count) {
    if (count) {  // all sent
      prerollCount_ = 0;
      prerollSent_ = 0;
    }
    return false;
  }
  memcpy(out, preroll_ + prerollSent_ * FRAME_SAMPLES, FRAME_BYTES);
  prerollSent_++;
  return true;
}

bool Mic::release(uint32_t timeoutMs) {
  wantBus_ = false;
  uint32_t start = millis();
  while (hasBus_) {
    if (millis() - start > timeoutMs) return false;
    delay(2);
  }
  return true;
}

void Mic::taskEntry(void* self) { static_cast<Mic*>(self)->task(); }

void Mic::task() {
  static int16_t bufs[NUM_BUFS][FRAME_SAMPLES];
  int idx = 0;
  int queued = 0;

  for (;;) {
    if (!wantBus_) {
      if (hasBus_) {
        M5.Mic.end();  // drops anything still queued
        hasBus_ = false;
      }
      level_ = 0;
      vTaskDelay(pdMS_TO_TICKS(5));
      continue;
    }
    if (!hasBus_) {
      M5.Speaker.end();  // M5.begin() may have left the speaker holding the bus
      if (!M5.Mic.begin()) {
        vTaskDelay(pdMS_TO_TICKS(100));
        continue;
      }
      hasBus_ = true;
      queued = 0;
    }

    if (!M5.Mic.record(bufs[idx], FRAME_SAMPLES, SAMPLE_RATE)) {
      vTaskDelay(pdMS_TO_TICKS(5));
      continue;
    }
    int done = (idx + NUM_BUFS - 2) % NUM_BUFS;
    idx = (idx + 1) % NUM_BUFS;
    if (queued < 2) {  // the first two records have nothing finished behind them
      queued++;
      continue;
    }

    int16_t* frame = bufs[done];
    float sumSq = 0;
    for (size_t i = 0; i < FRAME_SAMPLES; ++i) {
      float s = frame[i] * gain_;
      s = constrain(s, -32768.0f, 32767.0f);
      frame[i] = static_cast<int16_t>(s);
      sumSq += (s / 32768.0f) * (s / 32768.0f);
    }
    level_ = sqrtf(sumSq / FRAME_SAMPLES);

    if (tap_) tap_(frame, FRAME_SAMPLES);
    if (prerollWanted_ && ring_ && preroll_) {
      // Hand the frames that weren't sent to loop(), oldest first, then
      // stream from this frame on.
      size_t n = ringCount_, start = (ringHead_ + PREROLL_FRAMES - n) % PREROLL_FRAMES;
      for (size_t i = 0; i < n; ++i)
        memcpy(preroll_ + i * FRAME_SAMPLES, ring_ + ((start + i) % PREROLL_FRAMES) * FRAME_SAMPLES, FRAME_BYTES);
      ringCount_ = 0;
      prerollSent_ = 0;
      prerollCount_ = n;
      prerollWanted_ = false;
      streaming_ = true;
    }
    if (!streaming_ && ring_) {  // keep what isn't sent, for the next preroll
      memcpy(ring_ + ringHead_ * FRAME_SAMPLES, frame, FRAME_BYTES);
      ringHead_ = (ringHead_ + 1) % PREROLL_FRAMES;
      if (ringCount_ < PREROLL_FRAMES) ringCount_++;
    }

    if (streaming_) {
      // If loop() has fallen behind, drop the oldest frame rather than block.
      if (uxQueueSpacesAvailable(queue_) == 0) {
        static uint8_t scratch[FRAME_BYTES];
        xQueueReceive(queue_, scratch, 0);
      }
      xQueueSend(queue_, frame, 0);
    }
  }
}
