#include "speaker.h"

#include <M5Unified.h>
#include <freertos/idf_additions.h>

#include "mic.h"

static const int SAMPLE_RATE = 16000;
static const size_t RING_BYTES = 1024 * 1024;  // 32 s of audio, in PSRAM
static const size_t MIN_PREBUFFER = 6400;      // 200 ms, if the size is unknown...
static const size_t GAPPY_PREBUFFER = 19200;   // ...up to 600 ms while lines keep running dry
static const size_t MAX_PREBUFFER = 32000;     // 1 s: never wait longer than this
static const size_t CHUNK_SAMPLES = 512;       // 32 ms per playRaw()
// M5Unified queues two buffers per channel; with a single producer, three
// in rotation means the one being refilled is never still queued.
static const int NUM_CHUNKS = 3;
static const int CHANNEL = 0;

void Speaker::begin(Mic* mic, float volume, DoneHandler onDone) {
  mic_ = mic;
  volume_ = constrain(volume, 0.0f, 1.0f);
  onDone_ = std::move(onDone);

  ring_ = static_cast<uint8_t*>(ps_malloc(RING_BYTES));
  if (ring_ == nullptr) {
    // No PSRAM? Fall back to a small internal buffer (~2 s).
    ringSize_ = 64 * 1024;
    ring_ = static_cast<uint8_t*>(malloc(ringSize_));
  } else {
    ringSize_ = RING_BYTES;
  }

  // Stack and buffers in PSRAM: internal RAM is what the robot runs short of.
  xTaskCreatePinnedToCoreWithCaps(taskEntry, "speaker", 4096, this, 3, nullptr, 0, MALLOC_CAP_SPIRAM);
}

void Speaker::setVolume(float v) {
  volume_ = constrain(v, 0.0f, 1.0f);
  if (M5.Speaker.isRunning()) M5.Speaker.setVolume(static_cast<uint8_t>(volume_ * 255));
}

void Speaker::beginSpeech(size_t expectedBytes) {
  portENTER_CRITICAL(&lock_);
  head_ = 0;
  tail_ = 0;
  ended_ = false;
  stopped_ = false;
  expectedBytes_ = expectedBytes;
  underruns_ = 0;
  prebuffered_ = 0;
  speechStartMs_ = millis();
  speaking_ = true;
  portEXIT_CRITICAL(&lock_);
}

void Speaker::feed(const uint8_t* pcm, size_t len) {
  if (!speaking_ || stopped_ || ring_ == nullptr) return;
  portENTER_CRITICAL(&lock_);
  size_t used = (head_ + ringSize_ - tail_) % ringSize_;
  size_t space = ringSize_ - 1 - used;
  if (len > space) len = space;  // drop the tail rather than wrap over unread audio
  size_t first = min(len, ringSize_ - head_);  // up to the end of the ring, then the rest from its start
  memcpy(ring_ + head_, pcm, first);
  memcpy(ring_, pcm + first, len - first);
  head_ = (head_ + len) % ringSize_;
  portEXIT_CRITICAL(&lock_);
}

void Speaker::endSpeech() { ended_ = true; }

size_t Speaker::available() const {
  return (head_ + ringSize_ - tail_) % ringSize_;
}

size_t Speaker::readInto(uint8_t* out, size_t maxLen) {
  portENTER_CRITICAL(&lock_);
  size_t n = min(maxLen, available());
  for (size_t i = 0; i < n; ++i) {
    out[i] = ring_[tail_];
    tail_ = (tail_ + 1) % ringSize_;
  }
  portEXIT_CRITICAL(&lock_);
  return n;
}

void Speaker::taskEntry(void* self) { static_cast<Speaker*>(self)->task(); }

void Speaker::task() {
  const size_t size = sizeof(int16_t) * NUM_CHUNKS * CHUNK_SAMPLES;
  void* mem = heap_caps_malloc(size, MALLOC_CAP_SPIRAM);
  if (mem == nullptr) mem = heap_caps_malloc(size, MALLOC_CAP_INTERNAL);  // no PSRAM to spare: as before
  if (mem == nullptr) {
    Serial.println(F("speaker: no memory for its buffers"));
    vTaskDelete(nullptr);
  }
  auto chunks = static_cast<int16_t(*)[CHUNK_SAMPLES]>(mem);
  int chunk = 0;
  bool started = false;
  bool haveBus = false;

  for (;;) {
    if (!speaking_) {
      started = false;
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }
    if (!haveBus) {
      // Take the I2S bus from the mic while we pre-buffer.
      if (mic_ != nullptr && !mic_->release()) {
        Serial.println(F("speaker: mic didn't let go of the bus"));
      }
      M5.Speaker.begin();
      M5.Speaker.setVolume(static_cast<uint8_t>(volume_ * 255));
      haveBus = true;
    }
    if (stopped_) {  // stop(): drop what's queued and finish as if the line had ended
      M5.Speaker.stop(CHANNEL);
      portENTER_CRITICAL(&lock_);
      tail_ = head_;
      portEXIT_CRITICAL(&lock_);
      ended_ = true;
      started = true;
    }
    size_t avail = available();
    if (!started) {
      // Buffer the whole line if it's short, else up to 1 s, before playing.
      size_t want = expectedBytes_ ? min(expectedBytes_, MAX_PREBUFFER) : prebufferBytes_;
      if (avail < want && !ended_) {
        vTaskDelay(pdMS_TO_TICKS(5));
        continue;
      }
      started = true;
      prebuffered_ = avail;
      prebufferMs_ = millis() - speechStartMs_;
    }
    if (avail < 2) {
      if (ended_) {
        // Done: let the queue play out, then give the bus back to the mic.
        while (M5.Speaker.isPlaying()) vTaskDelay(pdMS_TO_TICKS(5));
        M5.Speaker.end();
        haveBus = false;
        if (mic_ != nullptr) mic_->reclaim();
        speaking_ = false;
        level_ = 0;
        // The next line's head start: 100 ms more after a line that ran dry,
        // back toward 200 ms (50 ms a line) after clean ones. Gaps cost more
        // than the wait does. (From the original V2.)
        if (underruns_ > 0) {
          prebufferBytes_ = min(prebufferBytes_ + 3200, GAPPY_PREBUFFER);
        } else if (prebufferBytes_ > MIN_PREBUFFER) {
          prebufferBytes_ = max(prebufferBytes_ - 1600, MIN_PREBUFFER);
        }
        if (onDone_) onDone_();
      } else {
        underruns_ = underruns_ + 1;  // ran dry mid-line: audible gap
        vTaskDelay(pdMS_TO_TICKS(5));
      }
      continue;
    }

    int16_t* buf = chunks[chunk];
    chunk = (chunk + 1) % NUM_CHUNKS;
    size_t whole = min(sizeof(chunks[0]), avail & ~size_t(1));  // whole samples only
    size_t n = readInto(reinterpret_cast<uint8_t*>(buf), whole) / 2;
    float sumSq = 0;
    for (size_t i = 0; i < n; ++i) {
      float f = buf[i] / 32768.0f;
      sumSq += f * f;
    }
    if (n > 0) level_ = 0.6f * level_ + 0.4f * sqrtf(sumSq / n);
    // With an explicit channel this waits for a free queue slot.
    M5.Speaker.playRaw(buf, n, SAMPLE_RATE, false, 1, CHANNEL);
  }
}
