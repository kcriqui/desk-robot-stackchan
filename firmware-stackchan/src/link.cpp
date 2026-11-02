#include "link.h"

#include <ArduinoJson.h>
#include <Preferences.h>
#include <WiFi.h>
#include <algorithm>
#include <esp_system.h>

static const char* FW_VERSION = "0.6.10.16";

static const size_t MAX_NETWORKS = 8;
static const uint32_t JOIN_MS = 15000;           // give up on a network after this long
static const uint32_t REJOIN_MS = 20000;         // after a drop, how long the WiFi stack gets to rejoin by itself
static const uint32_t RESCAN_MS = 3000;          // nothing to join: look again (a hotspot just turned on)
static const uint16_t FINDER_PORT = 8767;        // server/brain/finder.py
static const uint32_t ASK_MS = 3000;             // how often to ask for a brain while there's none
static const uint32_t BRAIN_PATIENCE_MS = 12000;  // an address that fails this long gives way to a brain that answered

void Link::begin(const char* ssid, const char* pass, const char* host,
                 uint16_t port, const char* token, CommandHandler onCommand,
                 StateHandler onState) {
  home_ = ssid;
  homeBrain_ = host;
  brainPort_ = port;
  token_ = token;
  onCommand_ = std::move(onCommand);
  onState_ = std::move(onState);

  loadNetworks(ssid, pass);
  WiFi.persistent(false);  // the list above is the only copy; don't wear the flash on every join
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // lower latency; main.cpp turns power saving on in deep sleep
  // Hear every channel before joining, then pick the strongest access point
  // with this name. The default stops at the first one it hears, which with a
  // mesh or a second router can be a far one. (From the original V2.)
  WiFi.setScanMethod(WIFI_ALL_CHANNEL_SCAN);
  WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);

  ws_.onEvent([this](WStype_t type, uint8_t* payload, size_t length) {
    onEvent(type, payload, length);
  });
  startScan(millis());
}

void Link::loadNetworks(const char* homeSsid, const char* homePass) {
  Preferences p;
  p.begin("wifi", true);
  String saved = p.getString("list", "[]");
  last_ = p.getString("last", homeSsid);
  p.end();
  networks_.push_back({homeSsid, homePass});
  JsonDocument doc;
  if (!deserializeJson(doc, saved)) {
    for (JsonObject n : doc.as<JsonArray>()) {
      String ssid = n["s"] | "";
      if (!ssid.isEmpty() && !knows(ssid) && networks_.size() < MAX_NETWORKS) {
        networks_.push_back({ssid, String(n["p"] | "")});
      }
    }
  }
}

// Everything but the first: that one comes from secrets.h.
void Link::saveNetworks() const {
  JsonDocument doc;
  JsonArray list = doc.to<JsonArray>();
  for (size_t i = 1; i < networks_.size(); ++i) {
    JsonObject n = list.add<JsonObject>();
    n["s"] = networks_[i].ssid;
    n["p"] = networks_[i].pass;
  }
  String out;
  serializeJson(doc, out);
  Preferences p;
  p.begin("wifi", false);
  p.putString("list", out);
  p.putString("last", last_);
  p.end();
}

bool Link::knows(const String& ssid) const {
  for (const Network& n : networks_) {
    if (n.ssid == ssid) return true;
  }
  return false;
}

void Link::startScan(uint32_t nowMs) {
  if (scanning_) return;
  scanning_ = WiFi.scanNetworks(true) == WIFI_SCAN_RUNNING;
  if (!scanning_) nextScanMs_ = nowMs + RESCAN_MS;  // the radio is busy: again in a moment
}

void Link::onScanDone(int found, uint32_t nowMs, bool wifiUp) {
  std::vector<Seen> seen;
  for (int i = 0; i < found; ++i) {
    String ssid = WiFi.SSID(i);
    if (ssid.isEmpty()) continue;
    auto same = std::find_if(seen.begin(), seen.end(), [&](const Seen& s) { return s.ssid == ssid; });
    int rssi = WiFi.RSSI(i);
    if (same == seen.end()) seen.push_back({ssid, rssi, WiFi.encryptionType(i) == WIFI_AUTH_OPEN});
    else same->rssi = max(same->rssi, rssi);  // a mesh: the same name more than once
  }
  WiFi.scanDelete();
  std::sort(seen.begin(), seen.end(), [](const Seen& a, const Seen& b) { return a.rssi > b.rssi; });
  if (scanWanted_) {
    scanWanted_ = false;
    seen_ = seen;
    if (seen_.size() > 20) seen_.resize(20);
    sendWifi();
  }
  if (wifiUp) return;

  // The last one used first, so a choice made in the live view sticks while that network is there; then by signal.
  toTry_.clear();
  for (const Seen& s : seen) {
    if (knows(s.ssid) && s.ssid != last_) toTry_.push_back(s.ssid);
  }
  bool lastInSight = std::any_of(seen.begin(), seen.end(), [&](const Seen& s) { return s.ssid == last_; });
  if (lastInSight && knows(last_)) toTry_.insert(toTry_.begin(), last_);
  if (toTry_.empty() && knows(last_)) {
    tryLastBlind_ = !tryLastBlind_;
    if (tryLastBlind_) toTry_.push_back(last_);  // a hidden network never shows in a scan
  }
  if (toTry_.empty()) Serial.printf("wifi: no known network in sight (%d seen)\n", static_cast<int>(seen.size()));
  joinNext(nowMs);
}

void Link::joinNext(uint32_t nowMs) {
  joining_ = false;
  if (toTry_.empty()) {
    nextScanMs_ = nowMs + RESCAN_MS;
    return;
  }
  String ssid = toTry_.front();
  toTry_.erase(toTry_.begin());
  for (const Network& n : networks_) {
    if (n.ssid != ssid) continue;
    Serial.printf("wifi: joining \"%s\"...\n", ssid.c_str());
    WiFi.begin(ssid.c_str(), n.pass.isEmpty() ? nullptr : n.pass.c_str());
    joining_ = true;
    joinAtMs_ = nowMs;
    return;
  }
  joinNext(nowMs);  // forgotten since the scan
}

void Link::onJoined(uint32_t nowMs) {
  joining_ = false;
  toTry_.clear();
  String ssid = WiFi.SSID();
  Serial.printf("wifi: connected to \"%s\", ip %s\n", ssid.c_str(), WiFi.localIP().toString().c_str());
  if (ssid != last_) {
    last_ = ssid;
    saveNetworks();
  }
  finder_.stop();
  finder_.begin(FINDER_PORT);
  nextAskMs_ = nowMs;
  if (ssid == here_) return;  // back on the same network: the link picks up by itself
  here_ = ssid;
  startBrain(ssid == home_ ? homeBrain_ : String(), brainPort_, nowMs);
}

void Link::startBrain(const String& host, uint16_t port, uint32_t nowMs) {
  if (brainStarted_) ws_.disconnect();
  brainHost_ = host;
  brainPort_ = port;
  brainSinceMs_ = nowMs;
  brainStarted_ = !host.isEmpty();
  if (!brainStarted_) return;
  Serial.printf("brain: trying %s:%u\n", host.c_str(), port);
  ws_.begin(brainHost_.c_str(), brainPort_, "/");
  ws_.setReconnectInterval(3000);
  ws_.enableHeartbeat(15000, 3000, 2);  // ping every 15 s, drop after 2 misses
}

// With no brain: ask the network, and take an answer if there's no address yet or the one we have isn't working.
void Link::findBrain(uint32_t nowMs) {
  for (int size = finder_.parsePacket(); size > 0; size = finder_.parsePacket()) {
    char reply[32] = {};
    finder_.read(reply, sizeof reply - 1);
    int port = 0;
    if (sscanf(reply, "rockybrain! %d", &port) != 1 || port <= 0 || port > 65535) continue;
    String from = finder_.remoteIP().toString();
    if (from != brainHost_ && (brainHost_.isEmpty() || static_cast<int32_t>(nowMs - brainSinceMs_) >= static_cast<int32_t>(BRAIN_PATIENCE_MS))) {
      Serial.printf("brain: found one at %s\n", from.c_str());
      startBrain(from, port, nowMs);
    }
  }
  if (static_cast<int32_t>(nowMs - nextAskMs_) >= 0) {
    nextAskMs_ = nowMs + ASK_MS;
    finder_.beginPacket(WiFi.broadcastIP(), FINDER_PORT);
    finder_.print("rockybrain?");
    finder_.endPacket();
  }
}

// The live view's picture of the WiFi. No passwords.
void Link::sendWifi() {
  JsonDocument doc;
  doc["type"] = "wifi";
  doc["now"] = WiFi.status() == WL_CONNECTED ? WiFi.SSID() : String();
  doc["rssi"] = WiFi.RSSI();
  doc["ip"] = WiFi.localIP().toString();
  JsonArray known = doc["known"].to<JsonArray>();
  for (const Network& n : networks_) {
    JsonObject o = known.add<JsonObject>();
    o["ssid"] = n.ssid;
    o["home"] = n.ssid == home_;
  }
  JsonArray seen = doc["seen"].to<JsonArray>();
  for (const Seen& s : seen_) {
    JsonObject o = seen.add<JsonObject>();
    o["ssid"] = s.ssid;
    o["rssi"] = s.rssi;
    o["open"] = s.open;
  }
  String out;
  serializeJson(doc, out);
  sendJson(out);
}

void Link::printWifi() const {
  Serial.printf("wifi: %s, ip %s, %d dBm, brain %s\n",
                WiFi.status() == WL_CONNECTED ? WiFi.SSID().c_str() : "not connected",
                WiFi.localIP().toString().c_str(), static_cast<int>(WiFi.RSSI()),
                brainHost_.isEmpty() ? "not found yet" : brainHost_.c_str());
  for (const Network& n : networks_) Serial.printf("  knows \"%s\"\n", n.ssid.c_str());
}

// wifi_add's password goes no further than the list: not to the console, not back to the brain.
bool Link::handleWifiMessage(const char* type, JsonDocument& doc, uint32_t nowMs) {
  if (strncmp(type, "wifi_", 5) != 0) return false;
  String ssid = doc["ssid"] | "";
  if (!strcmp(type, "wifi_scan")) {
    scanWanted_ = true;
    startScan(nowMs);  // already scanning: that one reports
    return true;
  }
  if (!strcmp(type, "wifi_add") && !ssid.isEmpty()) {
    String pass = doc["password"] | "";
    auto n = std::find_if(networks_.begin(), networks_.end(), [&](const Network& x) { return x.ssid == ssid; });
    if (n != networks_.end() && n != networks_.begin()) {
      n->pass = pass;
    } else if (n == networks_.end() && networks_.size() < MAX_NETWORKS) {
      networks_.push_back({ssid, pass});
    } else {
      sendNote(n == networks_.end() ? "wifi: already knows 8 networks, forget one first"
                                    : "wifi: that network's password is in secrets.h");
    }
    saveNetworks();
  } else if (!strcmp(type, "wifi_forget") && !ssid.isEmpty()) {
    auto n = std::find_if(networks_.begin() + 1, networks_.end(), [&](const Network& x) { return x.ssid == ssid; });
    if (n != networks_.end()) networks_.erase(n);
    if (last_ == ssid) last_ = home_;
    saveNetworks();
  } else if (!strcmp(type, "wifi_use") && knows(ssid)) {
    last_ = ssid;
    saveNetworks();
    switchWanted_ = true;  // from update(), not from inside the socket's own callback
  }
  sendWifi();
  return true;
}

void Link::update(uint32_t nowMs) {
  if (switchWanted_) {
    switchWanted_ = false;
    Serial.printf("wifi: asked to use \"%s\"\n", last_.c_str());
    switching_ = true;
    WiFi.disconnect();
    joining_ = false;
    toTry_.clear();
  }
  bool wifiUp = WiFi.status() == WL_CONNECTED;
  if (scanning_) {
    int found = WiFi.scanComplete();
    if (found != WIFI_SCAN_RUNNING) {
      scanning_ = false;
      onScanDone(found, nowMs, wifiUp);
    }
  }
  if (wifiUp && !wifiReported_) {
    wifiReported_ = true;
    onJoined(nowMs);
  } else if (!wifiUp && wifiReported_) {
    Serial.println(F("wifi: lost, retrying"));
    wifiReported_ = false;
    // The WiFi stack rejoins by itself after most drops, but not after every
    // failed join (in the original V2 it sat on "joining" for minutes after a
    // reboot), and it can't move to another network. So if it has been down
    // for a while, look around and start over.
    nextScanMs_ = switching_ ? nowMs : nowMs + REJOIN_MS;
    switching_ = false;
  }
  if (!wifiUp) {
    if (joining_) {
      if (nowMs - joinAtMs_ >= JOIN_MS) {
        Serial.printf("wifi: couldn't join (status %d)\n", static_cast<int>(WiFi.status()));
        WiFi.disconnect();
        joinNext(nowMs);
      }
    } else if (!scanning_ && static_cast<int32_t>(nowMs - nextScanMs_) >= 0) {
      WiFi.disconnect();
      startScan(nowMs);
    }
    return;
  }

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

  if (!connected_) findBrain(nowMs);
  if (!brainStarted_) return;

  // Each ws_.loop() reads at most one message. While a reply streams in (ten
  // audio messages a second, plus heartbeat replies), one read per pass of
  // loop() falls behind as soon as a pass also sends a camera frame: gaps in
  // the voice, and a heartbeat reply stuck behind audio until the link gives
  // up. So read everything that's waiting, for a few ms at most. (From the
  // original V2.) The same while connecting: the library reads the brain's
  // handshake reply a line per call, so one call per pass of a slow loop
  // (300-400 ms with the face's demo cycle) took ~2 s and the hello came too
  // late for the brain, over and over after a power-on (2026-10-28).
  uint32_t t0 = millis();
  ws_.loop();
  for (int i = 1; i < 32 && millis() - t0 < 12; ++i) ws_.loop();
}

const char* Link::bootReason() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "power on";
    case ESP_RST_SW: return "restart";  // OTA, or asked
    case ESP_RST_PANIC: return "crash";
    case ESP_RST_INT_WDT: return "interrupt watchdog";
    case ESP_RST_TASK_WDT: return "task watchdog";
    case ESP_RST_WDT: return "watchdog";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_DEEPSLEEP: return "deep sleep";
    case ESP_RST_EXT: return "reset pin";
    case ESP_RST_USB: return "USB";
    default: return "unknown";
  }
}

void Link::sendNote(const char* text) {
  if (!connected_) return;
  JsonDocument doc;
  doc["type"] = "note";
  doc["text"] = text;
  String out;
  serializeJson(doc, out);
  sendJson(out);
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
      doc["up_s"] = millis() / 1000;  // a small number: it has just started
      doc["boot"] = bootReason();
      String out;
      serializeJson(doc, out);
      sendJson(out);
      sendWifi();
      if (onState_) onState_(true);
      break;
    }
    case WStype_DISCONNECTED:
      if (connected_) {
        Serial.println(F("brain: disconnected, will retry"));
        if (onState_) onState_(false);
        brainSinceMs_ = millis();  // it may only be restarting: give it its chance again
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
  if (handleWifiMessage(type, doc, millis())) return;
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
  } else if (!strcmp(type, "hearing")) {  // the brain hears speech (a turn is open)
    cmd = String("hearing ") + ((doc["on"] | false) ? "on" : "off");
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
