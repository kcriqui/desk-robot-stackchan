#include "link.h"

#include <ArduinoJson.h>
#include <WiFi.h>

static const char* FW_VERSION = "0.6.9.26";

void Link::begin(const char* ssid, const char* pass, const char* host,
                 uint16_t port, const char* token, CommandHandler onCommand,
                 StateHandler onState) {
  ssid_ = ssid;
  pass_ = pass;
  token_ = token;
  onCommand_ = std::move(onCommand);
  onState_ = std::move(onState);

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // lower latency; main.cpp turns power saving on in deep sleep
  // Hear every channel before joining, then pick the strongest access point
  // with this name. The default stops at the first one it hears, which with a
  // mesh or a second router can be a far one. (From the original V2.)
  WiFi.setScanMethod(WIFI_ALL_CHANNEL_SCAN);
  WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);
  WiFi.begin(ssid_, pass_);
  Serial.printf("wifi: joining \"%s\"...\n", ssid_);

  ws_.begin(host, port, "/");
  ws_.onEvent([this](WStype_t type, uint8_t* payload, size_t length) {
    onEvent(type, payload, length);
  });
  ws_.setReconnectInterval(3000);
  ws_.enableHeartbeat(15000, 3000, 2);  // ping every 15 s, drop after 2 misses
}

void Link::update(uint32_t nowMs) {
  bool wifiUp = WiFi.status() == WL_CONNECTED;
  if (wifiUp && !wifiReported_) {
    Serial.printf("wifi: connected, ip %s\n", WiFi.localIP().toString().c_str());
    wifiReported_ = true;
  } else if (!wifiUp && wifiReported_) {
    Serial.println(F("wifi: lost, retrying"));
    wifiReported_ = false;
  }
  if (!wifiUp) {
    // The WiFi stack rejoins by itself after most drops, but not after every
    // failed join (in the original V2 it sat on "joining" for minutes after a
    // reboot). So if it has been down for a while, start the join over.
    if (wifiDownSinceMs_ == 0) {
      wifiDownSinceMs_ = nowMs | 1;
    } else if (nowMs - wifiDownSinceMs_ > 20000) {
      Serial.printf("wifi: still not connected (status %d), joining \"%s\" again\n",
                    static_cast<int>(WiFi.status()), ssid_);
      WiFi.disconnect();
      WiFi.begin(ssid_, pass_);
      wifiDownSinceMs_ = nowMs | 1;
    }
    return;
  }
  wifiDownSinceMs_ = 0;

  // Transmit power to suit the signal: sending is the radio's biggest draw,
  // and full power is only needed far from the router. 11 dBm while the
  // router comes in loud, full power again once it fades; the gap between the
  // thresholds stops it flapping. RSSI is how loud the router is to us, which
  // our own power doesn't change. (From the original V2.)
  if (nowMs - lastTxCheckMs_ >= 5000) {
    lastTxCheckMs_ = nowMs;
    int rssi = WiFi.RSSI();
    bool low = lowTxPower_ ? rssi > -62 : rssi >= -50;
    if (rssi != 0 && low != lowTxPower_) {
      lowTxPower_ = low;
      WiFi.setTxPower(low ? WIFI_POWER_11dBm : WIFI_POWER_19_5dBm);
      Serial.printf("wifi: signal %d dBm, transmit power %s\n", rssi, low ? "11 dBm (router close)" : "full");
    }
  }

  // Each ws_.loop() reads at most one message. While a reply streams in (ten
  // audio messages a second, plus heartbeat replies), one read per pass of
  // loop() falls behind as soon as a pass also sends a camera frame: gaps in
  // the voice, and a heartbeat reply stuck behind audio until the link gives
  // up. So read everything that's waiting, for a few ms at most. (From the
  // original V2.)
  uint32_t t0 = millis();
  ws_.loop();
  for (int i = 1; connected_ && i < 32 && millis() - t0 < 12; ++i) ws_.loop();
}

void Link::sendJson(const String& json) {
  if (!connected_) return;
  ws_.sendTXT(json.c_str(), json.length());
}

void Link::sendBinary(uint8_t type, const uint8_t* data, size_t len) {
  if (!connected_) return;
  static uint8_t* buf = nullptr;
  static const size_t CAP = 64 * 1024;  // mic frames are ~1 KB, JPEGs ~10-20 KB
  if (buf == nullptr) buf = static_cast<uint8_t*>(ps_malloc(CAP));
  if (buf == nullptr || len > CAP - 1) return;
  buf[0] = type;
  memcpy(buf + 1, data, len);
  ws_.sendBIN(buf, len + 1);
}

void Link::sendState(float panDeg, const char* emotion) {
  if (!connected_) return;
  JsonDocument doc;
  doc["type"] = "state";
  doc["pan"] = panDeg;
  doc["emotion"] = emotion;
  String out;
  serializeJson(doc, out);
  sendJson(out);
}

void Link::onEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED: {
      connected_ = true;
      Serial.println(F("brain: connected"));
      JsonDocument doc;
      doc["type"] = "hello";
      doc["who"] = "desk-robot";
      doc["fw"] = FW_VERSION;
      doc["token"] = token_;  // the brain drops connections without it
      if (wakeWord_) doc["wake"] = wakeWord_;
      if (asleep_) doc["asleep"] = asleep_();
      String out;
      serializeJson(doc, out);
      sendJson(out);
      if (onState_) onState_(true);
      break;
    }
    case WStype_DISCONNECTED:
      if (connected_) {
        Serial.println(F("brain: disconnected, will retry"));
        if (onState_) onState_(false);
      }
      connected_ = false;
      break;
    case WStype_TEXT: {
      // Copy so we can guarantee a terminator.
      String msg(reinterpret_cast<const char*>(payload), length);
      handleMessage(msg.c_str());
      break;
    }
    case WStype_BIN:
      if (length > 1 && payload[0] == 0x01 && onAudio_) {
        onAudio_(payload + 1, length - 1);
      }
      break;
    default:
      break;
  }
}

void Link::handleMessage(const char* json) {
  JsonDocument doc;
  if (deserializeJson(doc, json)) {
    Serial.printf("brain: bad json: %s\n", json);
    return;
  }
  const char* type = doc["type"] | "";
  String cmd;
  if (!strcmp(type, "emotion")) {
    cmd = String("emo ") + (doc["name"] | "neutral");
  } else if (!strcmp(type, "pan") || !strcmp(type, "tilt")) {
    cmd = String(type) + " " + String(doc["deg"] | 0.0f, 1);
  } else if (!strcmp(type, "speak_begin")) {
    cmd = String("speak_begin ") + String(doc["bytes"] | 0);
  } else if (!strcmp(type, "speak_end")) {
    cmd = type;
  } else if (!strcmp(type, "volume")) {
    cmd = String("volume ") + String(doc["level"] | 0.5f, 2);
  } else if (!strcmp(type, "mic")) {
    cmd = String("mic ") + ((doc["on"] | false) ? "on" : "off");
  } else if (!strcmp(type, "asleep")) {
    cmd = String("sleep ") + ((doc["on"] | false) ? "on" : "off");
  } else if (!strcmp(type, "wake_word")) {
    cmd = String("wake ") + ((doc["on"] | false) ? "on" : "off");
  } else if (!strcmp(type, "face")) {
    // A face pack (server/faces/*.json); save = keep it in flash.
    String pack;
    serializeJson(doc["pack"], pack);
    cmd = String((doc["save"] | true) ? "facepack " : "facepreview ") + pack;
  } else if (!strcmp(type, "clock")) {
    cmd = String("clock ") + (doc["tz"] | "UTC0") + ((doc["h24"] | false) ? " 24" : " 12");
  } else if (!strcmp(type, "screen")) {
    cmd = String("screen ") + (doc["face"] | 0) + " " + (doc["clock"] | 0) + " " + (doc["night"] | 0) + " " +
          (doc["dark_lux"] | 3) + " " + (doc["night_pct"] | 10) + " " + (doc["idle"] | 0) + " " +
          ((doc["near"] | true) ? 1 : 0) + " " + (doc["near_level"] | 60) + " " + (doc["led"] | 30) + " " +
          (doc["led_sleep"] | 0) + " " + ((doc["bank"] | false) ? 1 : 0);
  } else if (!strcmp(type, "leds")) {
    // {"test": true} | {"dance": true|false} | {"alert": "timer"|"battery"|"hot"|"hush"}
    if (doc["test"] | false) cmd = "leds test";
    else if (!doc["dance"].isNull()) cmd = String("leds dance ") + ((doc["dance"] | false) ? "on" : "off");
    else cmd = String("leds alert ") + (doc["alert"] | "none");
  } else if (!strcmp(type, "weather") && (doc["none"] | false)) {
    cmd = "weather none";
  } else if (!strcmp(type, "weather")) {
    cmd = String("weather ") + (doc["temp"] | 0) + " " + (doc["hi"] | 0) + " " + (doc["lo"] | 0) + " " +
          (doc["code"] | -1) + " " + ((doc["day"] | true) ? 1 : 0) + " " + (doc["unit"] | "F") + " " +
          (doc["sky"] | "");
  } else if (!strcmp(type, "power_off")) {
    cmd = String("poweroff ") + (doc["why"] | "brain");
  } else if (!strcmp(type, "stream")) {
    cmd = String("stream ") + ((doc["on"] | false) ? "on " : "off ") + String(doc["fps"] | 10.0f, 1);
  } else {
    Serial.printf("brain: unknown message type \"%s\"\n", type);
    return;
  }
  if (onCommand_) onCommand_(cmd);
}
