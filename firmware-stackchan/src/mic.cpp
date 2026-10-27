#include "mic.h"

#include <M5Unified.h>
#include <freertos/idf_additions.h>

#include "config.h"
#include "duplex.h"

static const int SAMPLE_RATE = 16000;
// record() queues up to two buffers and blocks until a slot frees, so once
// buffer i is queued, buffer i-2 is complete. Four in rotation leaves the
// finished one untouched while it's processed.
static const int NUM_BUFS = 4;
static const int QUEUE_FRAMES = 16;  // ~0.5 s of frames

void Mic::begin(float gain, Duplex* duplex) {
  gain_ = gain;
  duplex_ = duplex;
  // In PSRAM, like the stack: internal RAM is what the robot runs short of.
  queue_ = xQueueCreateWithCaps(QUEUE_FRAMES, sizeof(Queued), MALLOC_CAP_SPIRAM);
  scratch_ = static_cast<Queued*>(ps_malloc(sizeof(Queued)));
  dropped_ = static_cast<Queued*>(ps_malloc(sizeof(Queued)));
  ring_ = static_cast<int16_t*>(ps_malloc(PREROLL_FRAMES * FRAME_BYTES));
  preroll_ = static_cast<int16_t*>(ps_malloc(PREROLL_FRAMES * FRAME_BYTES));
  // In duplex it feeds the speaker too: above the speaker and wake tasks on its core, or the speaker runs dry.
  UBaseType_t priority = duplex != nullptr && duplex->wanted() ? 6 : 2;
  xTaskCreatePinnedToCoreWithCaps(taskEntry, "mic", 4096, this, priority, nullptr, 0, MALLOC_CAP_SPIRAM);
}

uint8_t Mic::nextFrame(uint8_t* out, size_t& len) {
  // The copy out of the queue goes through a static buffer: only loop() calls this.
  static Queued q;
  if (queue_ == nullptr || xQueueReceive(queue_, &q, 0) != pdTRUE) return 0;
  len = q.len;
  memcpy(out, q.data, len);
  return q.type;
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
  if (duplex_ != nullptr && duplex_->running()) return true;  // both ways at once: nothing to hand over
  wantBus_ = false;
  uint32_t start = millis();
  while (hasBus_) {
    if (millis() - start > timeoutMs) return false;
    delay(2);
  }
  return true;
}

void Mic::taskEntry(void* self) { static_cast<Mic*>(self)->task(); }

void Mic::enqueue(Queued& q) {
  // If loop() has fallen behind, drop the oldest frame rather than block.
  if (uxQueueSpacesAvailable(queue_) == 0 && dropped_ != nullptr) xQueueReceive(queue_, dropped_, 0);
  xQueueSend(queue_, &q, 0);
}

void Mic::deliver(int16_t* frame, const int16_t* rawMic, const int16_t* ref, float rawGain) {
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
  if (!streaming_ || scratch_ == nullptr) return;

  Queued& q = *scratch_;
  if (ref != nullptr) {
    // 0x04: the gain the brain applies after cancelling the echo (x256), then the raw mic and the reference.
    q.type = 0x04;
    q.len = 2 + 2 * FRAME_BYTES;
    uint16_t g = static_cast<uint16_t>(constrain(rawGain * 256.0f, 1.0f, 65535.0f));
    q.data[0] = g & 0xFF;
    q.data[1] = g >> 8;
    int16_t* s = reinterpret_cast<int16_t*>(q.data + 2);
    for (size_t i = 0; i < FRAME_SAMPLES; ++i) {
      s[2 * i] = rawMic[i];
      s[2 * i + 1] = ref[i];
    }
  } else {
    q.type = 0x01;
    q.len = FRAME_BYTES;
    memcpy(q.data, frame, FRAME_BYTES);
  }
  enqueue(q);
}

void Mic::runDuplex() {
  int16_t* raw = static_cast<int16_t*>(heap_caps_malloc(FRAME_BYTES * 3, MALLOC_CAP_SPIRAM));
  if (raw == nullptr) {
    Serial.println(F("mic: no memory for its buffers"));
    vTaskDelete(nullptr);
  }
  int16_t* ref = raw + FRAME_SAMPLES;
  int16_t* frame = ref + FRAME_SAMPLES;
  for (;;) {
    if (!duplex_->transfer(raw, ref, FRAME_SAMPLES)) {
      vTaskDelay(pdMS_TO_TICKS(5));
      continue;
    }
    float scale = gain_ * duplex_->micScale();
    float sumSq = 0;
    for (size_t i = 0; i < FRAME_SAMPLES; ++i) {
      float s = constrain(raw[i] * scale, -32768.0f, 32767.0f);
      frame[i] = static_cast<int16_t>(s);
      sumSq += (s / 32768.0f) * (s / 32768.0f);
    }
    level_ = sqrtf(sumSq / FRAME_SAMPLES);
    uint32_t played = duplex_->lastPlayedMs();
    bool echo = refWanted_ && played != 0 && millis() - played < DUPLEX_REF_TAIL_MS;
    deliver(frame, raw, echo ? ref : nullptr, scale);
  }
}

void Mic::task() {
  if (duplex_ != nullptr && duplex_->wanted()) {
    M5.Speaker.end();  // M5.begin() may have left either holding the bus
    M5.Mic.end();
    hasBus_ = true;
    if (duplex_->open()) runDuplex();  // doesn't return
    hasBus_ = false;
  }

  const size_t size = sizeof(int16_t) * NUM_BUFS * FRAME_SAMPLES;
  void* mem = heap_caps_malloc(size, MALLOC_CAP_SPIRAM);
  if (mem == nullptr) mem = heap_caps_malloc(size, MALLOC_CAP_INTERNAL);  // no PSRAM to spare: as before
  if (mem == nullptr) {
    Serial.println(F("mic: no memory for its buffers"));
    vTaskDelete(nullptr);
  }
  auto bufs = static_cast<int16_t(*)[FRAME_SAMPLES]>(mem);
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
    deliver(frame, nullptr, nullptr, 1.0f);
  }
}
