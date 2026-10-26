#pragma once

#include <Arduino.h>
#include <freertos/stream_buffer.h>

// Wake: Espressif's WakeNet (esp-sr, already linked into the Arduino core)
// listening for the wake word on the robot itself, so that while Rocky is
// asleep no audio leaves him until he hears it.
//
// The model (sr_models/, packed and uploaded by tools/srmodels.py) lives in
// the flash partition labelled "spiffs". With no model there, begin()
// returns false and the robot behaves as before: the brain listens for his
// name in the streamed audio.
//
// The mic task pushes every frame in; a feed task hands them to esp-sr's
// audio front end and a detect task fetches the results.

class Wake {
 public:
  // Checks `model` is in the partition (false if not); start() loads it.
  bool begin(const char* partition, const char* model);
  // The engine itself (~33 KB of internal RAM): started the first time the brain asks the robot to do the waking,
  // then kept till the next boot. True once running.
  bool start();
  bool ready() const { return afe_ != nullptr; }
  const char* word() const { return word_; }
  const char* status() const { return status_; }  // why it isn't ready, or "ok"

  // Listen or not (asleep and the brain asked for it). For 2 s after it's
  // turned on, detections are ignored, so old sound can't trigger it.
  void setListening(bool on);
  bool listening() const { return listening_; }

  // From the mic task: one frame of 16 kHz mono samples.
  void push(const int16_t* samples, size_t n);

  // From loop(): true once for each detection.
  bool takeDetection() {
    if (!detected_) return false;
    detected_ = false;
    return true;
  }

  // WakeNet's trigger threshold (lower = triggers more easily), and how
  // many times it has fired since boot.
  void setThreshold(float t);
  uint32_t count() const { return count_; }

 private:
  static void feedEntry(void* self);
  static void detectEntry(void* self);
  void feedTask();
  void detectTask();

  const void* iface_ = nullptr;  // const esp_afe_sr_iface_t*
  void* afe_ = nullptr;          // esp_afe_sr_data_t*
  void* models_ = nullptr;       // srmodel_list_t*: the partition's models (begin)
  char model_[40] = "";
  StreamBufferHandle_t in_ = nullptr;
  int chunk_ = 0;  // samples per feed
  char word_[24] = "";
  char status_[96] = "not started";
  volatile bool listening_ = false;
  volatile bool flushPending_ = false;
  volatile bool detected_ = false;
  volatile uint32_t count_ = 0;
  volatile uint32_t quietUntilMs_ = 0;  // ignore detections until then
};
