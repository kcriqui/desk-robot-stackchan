#pragma once

#include <Arduino.h>
#include <functional>

// Camera: the CoreS3's GC0308 → a stream of small JPEGs.
//
// The GC0308 only gives raw RGB565, so the capture task compresses each
// frame in software, then keeps only the latest JPEG (in PSRAM). loop()
// picks it up with takeFrame() and ships it to the brain, so a slow WiFi
// moment just skips frames instead of piling them up.
//
// Each frame also carries the head's angle at the moment it was grabbed, so
// the face tracker knows where the head was pointing in *that* picture.

class Camera {
 public:
  // Reads the head's actual pan/tilt in head degrees.
  using PoseSource = std::function<void(float& pan, float& tilt)>;

  bool begin();  // false if the camera didn't come up
  bool ok() const { return ok_; }
  void setPoseSource(PoseSource src) { poseSource_ = std::move(src); }

  void setStreaming(bool on, float fps);
  bool streaming() const { return streaming_; }

  // Copies the newest unsent JPEG into `out` (capacity `cap`) and its head
  // angle into pan/tilt; returns its size, or 0 if there's nothing new or
  // it didn't fit.
  size_t takeFrame(uint8_t* out, size_t cap, float* pan = nullptr, float* tilt = nullptr);

  uint32_t framesCaptured() const { return captured_; }
  uint32_t lastEncodeMs() const { return encodeMs_; }
  // Grab + JPEG encode time per frame since the last call: average and worst, ms.
  void takeEncodeStats(float& avgMs, uint32_t& maxMs) {
    uint32_t n = encN_;
    avgMs = n ? static_cast<float>(encSum_) / n : 0.0f;
    maxMs = encMax_;
    encSum_ = 0;
    encN_ = 0;
    encMax_ = 0;
  }

 private:
  static void taskEntry(void* self);
  void task();

  bool ok_ = false;
  volatile bool streaming_ = false;
  volatile uint32_t intervalMs_ = 125;
  uint8_t* latest_ = nullptr;      // PSRAM
  size_t latestCap_ = 0;
  volatile size_t latestLen_ = 0;
  float latestPan_ = 0, latestTilt_ = 0;  // head angle when the latest frame was grabbed
  PoseSource poseSource_;
  volatile bool fresh_ = false;
  volatile uint32_t captured_ = 0;
  volatile uint32_t encodeMs_ = 0;
  volatile uint32_t encSum_ = 0, encN_ = 0, encMax_ = 0;
  portMUX_TYPE lock_ = portMUX_INITIALIZER_UNLOCKED;
};
