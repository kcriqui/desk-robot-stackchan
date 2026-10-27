#include "duplex.h"

#include <M5Unified.h>
#include <Preferences.h>
#include <freertos/idf_additions.h>

#include "config.h"

static const uint32_t RATE = 16000;
static const int SLOTS = 4;            // as they come: MIC1, MIC3 (the reference), MIC2, MIC4 (unused)
static const size_t MAX_BLOCK = 480;   // frames per transfer(): 30 ms
static const size_t DMA_FRAMES = 240;  // per DMA buffer; 6 of them = 90 ms each way (LATENCY_MS)
static const size_t FIFO_BYTES = 8192; // the speaker's queue: 256 ms

static const uint8_t ES7210 = 0x40, AW88298 = 0x36, AW9523 = 0x58;
static const uint32_t I2C_HZ = 400000;

static void es7210(uint8_t reg, uint8_t v) { M5.In_I2C.writeRegister(ES7210, reg, &v, 1, I2C_HZ); }

static void aw88298(uint8_t reg, uint16_t v) {
  uint8_t b[2] = {static_cast<uint8_t>(v >> 8), static_cast<uint8_t>(v & 0xFF)};
  M5.In_I2C.writeRegister(AW88298, reg, b, 2, I2C_HZ);
}

void Duplex::load() {
  Preferences p;
  if (!p.begin("audio", true)) return;  // nothing saved: the default (on)
  wanted_ = p.getBool("duplex", wanted_);
  p.end();
}

void Duplex::save(bool on) {
  wanted_ = on;
  Preferences p;
  if (!p.begin("audio", false)) return;
  p.putBool("duplex", on);
  p.end();
}

// The I2S interrupt's callbacks: in IRAM (CONFIG_I2S_ISR_IRAM_SAFE: the driver refuses any that aren't).
static bool IRAM_ATTR onUnderrun(i2s_chan_handle_t, i2s_event_data_t*, void* self) {
  static_cast<Duplex*>(self)->underruns_ = static_cast<Duplex*>(self)->underruns_ + 1;
  return false;
}

static bool IRAM_ATTR onOverflow(i2s_chan_handle_t, i2s_event_data_t*, void* self) {
  static_cast<Duplex*>(self)->overflows_ = static_cast<Duplex*>(self)->overflows_ + 1;
  return false;
}

bool Duplex::open() {
  if (fifo_ == nullptr) fifo_ = xStreamBufferCreateWithCaps(FIFO_BYTES, 2, MALLOC_CAP_SPIRAM);
  if (out_ == nullptr) out_ = static_cast<int16_t*>(heap_caps_calloc(MAX_BLOCK * SLOTS, 2, MALLOC_CAP_INTERNAL));
  if (in_ == nullptr) in_ = static_cast<int16_t*>(heap_caps_malloc(MAX_BLOCK * SLOTS * 2, MALLOC_CAP_INTERNAL));
  if (mono_ == nullptr) mono_ = static_cast<int16_t*>(heap_caps_malloc(MAX_BLOCK * 2, MALLOC_CAP_INTERNAL));
  if (fifo_ == nullptr || out_ == nullptr || in_ == nullptr || mono_ == nullptr) {
    Serial.println(F("duplex: no memory"));
    return false;
  }
  i2s_chan_config_t chan = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
  chan.dma_desc_num = 6;
  chan.dma_frame_num = DMA_FRAMES;
  // If the mic task is late with the next block, send silence: by default the DMA replays its old buffers, a loud
  // buzz of whatever was played last.
  chan.auto_clear_after_cb = true;
  esp_err_t err = i2s_new_channel(&chan, &tx_, &rx_);
  if (err == ESP_OK) {
    i2s_tdm_config_t cfg = {
        .clk_cfg = I2S_TDM_CLK_DEFAULT_CONFIG(RATE),
        .slot_cfg = I2S_TDM_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO,
                                                       static_cast<i2s_tdm_slot_mask_t>(I2S_TDM_SLOT0 | I2S_TDM_SLOT1 |
                                                                                        I2S_TDM_SLOT2 | I2S_TDM_SLOT3)),
        .gpio_cfg = {.mclk = GPIO_NUM_0, .bclk = GPIO_NUM_34, .ws = GPIO_NUM_33, .dout = GPIO_NUM_13,
                     .din = GPIO_NUM_14, .invert_flags = {}},
    };
    cfg.clk_cfg.mclk_multiple = I2S_MCLK_MULTIPLE_256;
    err = i2s_channel_init_tdm_mode(tx_, &cfg);
    if (err == ESP_OK) err = i2s_channel_init_tdm_mode(rx_, &cfg);
    // Count the times the mic task fell behind: the speaker ran out of data, or the recording overflowed.
    i2s_event_callbacks_t txCb = {}, rxCb = {};
    txCb.on_send_q_ovf = onUnderrun;
    rxCb.on_recv_q_ovf = onOverflow;
    if (err == ESP_OK) err = i2s_channel_register_event_callback(tx_, &txCb, this);
    if (err == ESP_OK) err = i2s_channel_register_event_callback(rx_, &rxCb, this);
    if (err == ESP_OK) err = i2s_channel_enable(tx_);
    if (err == ESP_OK) err = i2s_channel_enable(rx_);
  }
  if (err != ESP_OK) {
    Serial.printf("duplex: I2S setup failed (%s), the mic and speaker take turns as before\n", esp_err_to_name(err));
    if (tx_) i2s_del_channel(tx_);
    if (rx_) i2s_del_channel(rx_);
    tx_ = rx_ = nullptr;
    return false;
  }

  // Clocks running: set the chips up for them. The ES7210 as M5Unified does (_microphone_enabled_cb_cores3), with
  // MIC3/4 powered and clocked and TDM on (REG12 = 0x02, as Espressif's es7210 driver for the ESP32-S3-BOX).
  struct Reg {
    uint8_t reg, value;
  };
  static const Reg regs[] = {
      {0x00, 0xFF}, {0x00, 0x41}, {0x01, 0x1f}, {0x06, 0x00}, {0x07, 0x20}, {0x08, 0x10}, {0x09, 0x30},
      {0x0A, 0x30}, {0x20, 0x0a}, {0x21, 0x2a}, {0x22, 0x0a}, {0x23, 0x2a}, {0x02, 0xC1}, {0x04, 0x01},
      {0x05, 0x00}, {0x11, 0x60}, {0x12, 0x02},  // 16-bit I2S; TDM
      {0x40, 0x42}, {0x41, 0x70}, {0x42, 0x70},
      {0x46, 0x00},  // MIC4 unused
      {0x47, 0x00}, {0x48, 0x00}, {0x49, 0x00}, {0x4A, 0x00},
      {0x4B, 0x00}, {0x4C, 0x00},  // MIC12 and MIC34 powered
  };
  for (const Reg& r : regs) es7210(r.reg, r.value);
  es7210(0x45, DUPLEX_REF_PGA);  // MIC3: the echo reference
  setMicPga(DUPLEX_MIC_PGA);
  es7210(0x01, 0x00);  // all ADC clocks on

  // The amp as M5Unified's speaker callback (_speaker_enabled_cb_cores3), but with BCK at 64 * fs for the TDM frame.
  M5.In_I2C.bitOn(AW9523, 0x02, 0x04, I2C_HZ);  // the amp's enable
  aw88298(0x61, 0x0673);  // boost off
  aw88298(0x04, 0x4040);  // I2S on, amp on
  aw88298(0x05, 0x0008);
  aw88298(0x06, 0x14E3);  // RX on, left, I2S, BCK 64 * fs (32 * 2), 16 kHz
  aw88298(0x0C, 0x0064);  // the amp's own volume as M5Unified (full); the speaker scales the samples
  running_ = true;
  Serial.printf("duplex: the mic and speaker run at once (mic PGA 0x%02X, reference 0x%02X)\n", micPga_,
                DUPLEX_REF_PGA);
  return true;
}

void Duplex::setMicPga(uint8_t reg) {
  reg = constrain(reg, 0x10, 0x1E);
  micPga_ = reg;
  // M5Unified: +33 dB (0x1B), its software gain 1. Each PGA step is 3 dB.
  micScale_ = powf(10.0f, (0x1B - reg) * 3.0f / 20.0f);
  es7210(0x43, reg);
  es7210(0x44, reg);
}

bool Duplex::transfer(int16_t* mic, int16_t* ref, size_t n) {
  if (!running_ || n > MAX_BLOCK) return false;
  size_t got = xStreamBufferReceive(fifo_, mono_, n * 2, 0) / 2;
  if (got) lastPlayedMs_ = millis();
  float sumSq = 0;
  for (size_t k = 0; k < got; ++k) {
    float f = mono_[k] / 32768.0f;
    sumSq += f * f;
  }
  level_ = got ? 0.6f * level_ + 0.4f * sqrtf(sumSq / got) : 0.6f * level_;  // as the speaker task smoothed it
  levels_[levelAt_] = level_;
  levelAt_ = (levelAt_ + 1) % LEVEL_DELAY;
  for (size_t k = 0; k < n; ++k) out_[k * SLOTS] = k < got ? mono_[k] : 0;  // the other slots stay 0
  size_t done = 0;
  i2s_channel_write(tx_, out_, n * SLOTS * 2, &done, 200);
  if (i2s_channel_read(rx_, in_, n * SLOTS * 2, &done, 200) != ESP_OK || done != n * SLOTS * 2) return false;
  for (size_t k = 0; k < n; ++k) {
    const int16_t* f = in_ + k * SLOTS;
    mic[k] = static_cast<int16_t>((static_cast<int32_t>(f[0]) + f[2]) / 2);
    ref[k] = f[1];
  }
  return true;
}

size_t Duplex::play(const int16_t* s, size_t n, uint32_t waitMs) {
  if (fifo_ == nullptr) return 0;
  // Whole samples only: a send into a nearly full buffer can take an odd number of bytes, and every sample after
  // it is then read a byte out of step, which plays as loud white noise. (The speaker task is the only writer, so
  // the room only grows while we wait.)
  uint32_t start = millis();
  size_t room;
  while ((room = xStreamBufferSpacesAvailable(fifo_) & ~size_t(1)) == 0) {
    if (millis() - start >= waitMs) return 0;
    vTaskDelay(pdMS_TO_TICKS(2));
  }
  return xStreamBufferSend(fifo_, s, min(room, n * 2), 0) / 2;
}

size_t Duplex::pending() const { return fifo_ ? xStreamBufferBytesAvailable(fifo_) / 2 : 0; }

void Duplex::flush() {
  if (fifo_) xStreamBufferReset(fifo_);
}
