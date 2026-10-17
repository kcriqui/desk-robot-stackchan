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
  xTaskCreatePinnedToCore(taskEntry, "mic", 4096, this, 2, nullptr, 0);
}

bool Mic::nextFrame(uint8_t* out) {
  return queue_ != nullptr && xQueueReceive(queue_, out, 0) == pdTRUE;
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
