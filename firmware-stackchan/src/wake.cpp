#include "wake.h"

#include <esp_heap_caps.h>
#include <freertos/idf_additions.h>

#include <esp_afe_config.h>
#include <esp_afe_sr_iface.h>
#include <esp_afe_sr_models.h>
#include <model_path.h>

static const esp_afe_sr_iface_t* iface(const void* p) { return static_cast<const esp_afe_sr_iface_t*>(p); }
static esp_afe_sr_data_t* data(void* p) { return static_cast<esp_afe_sr_data_t*>(p); }

bool Wake::begin(const char* partition, const char* model) {
  srmodel_list_t* models = esp_srmodel_init(partition);
  if (models == nullptr) {
    snprintf(status_, sizeof(status_), "no models in partition \"%s\"", partition);
  } else if (esp_srmodel_exists(models, const_cast<char*>(model)) < 0) {  // index, or -1
    snprintf(status_, sizeof(status_), "%d model(s) in \"%s\", but not %s", models->num, partition, model);
    models = nullptr;
  }
  if (models == nullptr) {
    Serial.printf("wake: %s (tools/srmodels.py upload); the brain will listen for the name instead\n", status_);
    return false;
  }
  models_ = models;
  strlcpy(model_, model, sizeof(model_));
  // "wn9_computer_tts" -> "computer"
  const char* w = strchr(model, '_');
  strlcpy(word_, w ? w + 1 : model, sizeof(word_));
  if (char* t = strstr(word_, "_tts")) *t = '\0';
  strlcpy(status_, "ready, not started (the brain listens)", sizeof(status_));
  Serial.printf("wake: \"%s\" ready; it starts when the brain asks for robot-side waking\n", word_);
  return true;
}

bool Wake::start() {
  if (afe_ != nullptr) return true;
  if (models_ == nullptr) return false;
  // ~33 KB of internal RAM, so only when the robot does the waking (the brain's "robot" setting). Everything that
  // can goes to PSRAM; the cost is logged so it stays visible.
  size_t freeBefore = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
  srmodel_list_t* models = static_cast<srmodel_list_t*>(models_);
  const char* model = model_;
  // One mic channel ("M"), wake word only: no echo cancelling (the mic is
  // off while he talks), no beamforming, noise suppression or VAD.
  afe_config_t* cfg = afe_config_init("M", models, AFE_TYPE_SR, AFE_MODE_LOW_COST);
  cfg->aec_init = false;
  cfg->se_init = false;
  cfg->ns_init = false;
  cfg->vad_init = false;
  cfg->agc_init = false;
  cfg->wakenet_init = true;
  cfg->wakenet_model_name = const_cast<char*>(model);
  cfg->wakenet_model_name_2 = nullptr;
  cfg->wakenet_mode = DET_MODE_90;  // "normal"; DET_MODE_95 triggers more readily
  cfg->memory_alloc_mode = AFE_MEMORY_ALLOC_MORE_PSRAM;
  cfg->afe_ringbuf_size = 8;  // frames; the default (50) is sized for multi-stage pipelines
  cfg = afe_config_check(cfg);
  const esp_afe_sr_iface_t* h = esp_afe_handle_from_config(cfg);
  esp_afe_sr_data_t* d = h ? h->create_from_config(cfg) : nullptr;
  afe_config_free(cfg);
  if (d == nullptr) {
    strlcpy(status_, "esp-sr's audio front end didn't start", sizeof(status_));
    Serial.printf("wake: %s\n", status_);
    return false;
  }

  iface_ = h;
  afe_ = d;  // before the tasks: the detect task fetches from it at once
  chunk_ = h->get_feed_chunksize(d);
  in_ = xStreamBufferCreateWithCaps(16000 * 2, chunk_ * 2, MALLOC_CAP_SPIRAM);  // ~1 s of slack
  xTaskCreatePinnedToCoreWithCaps(feedEntry, "wake-feed", 4096, this, 2, nullptr, 0,  // like the mic task
                                  MALLOC_CAP_SPIRAM);
  // Priority 1 like loop(): if esp-sr ever spins, it shares core 1 instead of freezing loop().
  xTaskCreatePinnedToCoreWithCaps(detectEntry, "wake-detect", 8192, this, 1, nullptr, 1, MALLOC_CAP_SPIRAM);
  snprintf(status_, sizeof(status_), "ok, using %u KB of internal RAM",
           (unsigned)((freeBefore - heap_caps_get_free_size(MALLOC_CAP_INTERNAL)) / 1024));
  Serial.printf("wake: listening for \"%s\" when asleep (%d-sample chunks; %s)\n", word_, chunk_, status_);
  return true;
}

void Wake::setListening(bool on) {
  if (on && !listening_) {
    // Sound from before he slept may still be in esp-sr's pipeline: don't
    // let it trigger. (Not reset_buffer(): calling that while the detect
    // task is inside fetch() on the other core crashed the robot.)
    quietUntilMs_ = millis() + 2000;
    flushPending_ = true;
  }
  listening_ = on;
}

void Wake::push(const int16_t* samples, size_t n) {
  if (!listening_ || in_ == nullptr) return;
  xStreamBufferSend(in_, samples, n * sizeof(int16_t), 0);  // full: drop, never block the mic
}

void Wake::setThreshold(float t) {
  if (afe_) iface(iface_)->set_wakenet_threshold(data(afe_), 1, t);
}

void Wake::feedEntry(void* self) { static_cast<Wake*>(self)->feedTask(); }
void Wake::detectEntry(void* self) { static_cast<Wake*>(self)->detectTask(); }

void Wake::feedTask() {
  int16_t* buf = static_cast<int16_t*>(heap_caps_malloc(chunk_ * sizeof(int16_t), MALLOC_CAP_INTERNAL));
  for (;;) {
    if (flushPending_) {  // only this task reads the stream buffer, and the mic never blocks on it
      flushPending_ = false;
      xStreamBufferReset(in_);
    }
    size_t got = 0;
    while (got < chunk_ * sizeof(int16_t)) {
      size_t n = xStreamBufferReceive(in_, reinterpret_cast<uint8_t*>(buf) + got,
                                      chunk_ * sizeof(int16_t) - got, pdMS_TO_TICKS(100));
      if (n == 0 && !listening_) break;
      got += n;
    }
    if (got == chunk_ * sizeof(int16_t)) iface(iface_)->feed(data(afe_), buf);
  }
}

void Wake::detectTask() {
  for (;;) {
    afe_fetch_result_t* res = iface(iface_)->fetch_with_delay(data(afe_), pdMS_TO_TICKS(200));
    if (res == nullptr || res->ret_value == ESP_FAIL) continue;
    if (res->wakeup_state == WAKENET_DETECTED && listening_ && millis() >= quietUntilMs_) {
      quietUntilMs_ = millis() + 2000;
      count_ = count_ + 1;
      detected_ = true;
    }
  }
}
