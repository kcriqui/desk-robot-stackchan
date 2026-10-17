#include "camera.h"

#include <M5Unified.h>
#include <esp_camera.h>
#include <img_converters.h>

#include "config.h"

static const size_t LATEST_CAP = 64 * 1024;  // QVGA JPEGs are ~8-20 KB

bool Camera::begin() {
  // CoreS3 GC0308 wiring, as in M5Stack's M5CoreS3 library (GC0308.cpp).
  // XCLK comes from an oscillator on the board, so there's no clock pin.
  camera_config_t cfg = {};
  cfg.pin_pwdn = -1;
  cfg.pin_reset = -1;
  cfg.pin_xclk = -1;
  cfg.pin_sccb_sda = 12;
  cfg.pin_sccb_scl = 11;
  cfg.pin_d7 = 47;
  cfg.pin_d6 = 48;
  cfg.pin_d5 = 16;
  cfg.pin_d4 = 15;
  cfg.pin_d3 = 42;
  cfg.pin_d2 = 41;
  cfg.pin_d1 = 40;
  cfg.pin_d0 = 39;
  cfg.pin_vsync = 46;
  cfg.pin_href = 38;
  cfg.pin_pclk = 45;
  cfg.xclk_freq_hz = 20000000;
  cfg.ledc_timer = LEDC_TIMER_0;
  cfg.ledc_channel = LEDC_CHANNEL_0;
  cfg.pixel_format = PIXFORMAT_RGB565;  // no hardware JPEG on this sensor
  cfg.frame_size = FRAMESIZE_QVGA;      // 320x240: what the tracker and live view expect
  cfg.jpeg_quality = 0;
  cfg.fb_count = 2;
  cfg.fb_location = CAMERA_FB_IN_PSRAM;
  cfg.grab_mode = CAMERA_GRAB_LATEST;
  cfg.sccb_i2c_port = -1;

  // The camera's control bus is the board's internal I2C. M5Stack's own
  // CoreS3 camera code releases it first; M5Unified takes it back as needed.
  M5.In_I2C.release();
  if (esp_camera_init(&cfg) != ESP_OK) {
    Serial.println(F("camera: init failed"));
    return false;
  }
  sensor_t* s = esp_camera_sensor_get();
  if (s != nullptr) {
    s->set_vflip(s, CAMERA_VFLIP ? 1 : 0);
    s->set_hmirror(s, CAMERA_HMIRROR ? 1 : 0);
  }

  latest_ = static_cast<uint8_t*>(ps_malloc(LATEST_CAP));
  latestCap_ = latest_ ? LATEST_CAP : 0;
  ok_ = latest_ != nullptr;
  // Core 0 at low priority: encoding is the heaviest thing on the robot, and
  // the face animation on core 1 shouldn't stutter for it.
  if (ok_) xTaskCreatePinnedToCore(taskEntry, "camera", 6144, this, 1, nullptr, 0);
  return ok_;
}

void Camera::setStreaming(bool on, float fps) {
  if (fps < 0.5f) fps = 0.5f;
  if (fps > CAMERA_MAX_FPS) fps = CAMERA_MAX_FPS;
  intervalMs_ = static_cast<uint32_t>(1000.0f / fps);
  streaming_ = on;
}

size_t Camera::takeFrame(uint8_t* out, size_t cap) {
  size_t n = 0;
  portENTER_CRITICAL(&lock_);
  if (fresh_ && latestLen_ <= cap) {
    n = latestLen_;
    memcpy(out, latest_, n);
    fresh_ = false;
  }
  portEXIT_CRITICAL(&lock_);
  return n;
}

void Camera::taskEntry(void* self) { static_cast<Camera*>(self)->task(); }

void Camera::task() {
  for (;;) {
    if (!streaming_) {
      vTaskDelay(pdMS_TO_TICKS(50));
      continue;
    }
    uint32_t t0 = millis();
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb != nullptr) {
      uint8_t* jpg = nullptr;
      size_t len = 0;
      bool encoded = frame2jpg(fb, CAMERA_JPEG_QUALITY, &jpg, &len);
      esp_camera_fb_return(fb);
      encodeMs_ = millis() - t0;
      if (encoded && len <= latestCap_) {
        portENTER_CRITICAL(&lock_);
        memcpy(latest_, jpg, len);
        latestLen_ = len;
        fresh_ = true;
        portEXIT_CRITICAL(&lock_);
        captured_ = captured_ + 1;
      }
      free(jpg);
    }
    uint32_t spent = millis() - t0;
    uint32_t interval = intervalMs_;
    vTaskDelay(pdMS_TO_TICKS(spent < interval ? interval - spent : 1));
  }
}
