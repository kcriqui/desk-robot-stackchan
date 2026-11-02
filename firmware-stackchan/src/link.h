#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>
#include <WebSocketsClient.h>
#include <WiFiUdp.h>
#include <functional>
#include <vector>

// Link: WiFi + a WebSocket to the brain server.
//
// Scans for the networks it knows (the one in secrets.h, and any added from the
// live view, kept in flash), joins the best, finds the brain there (the address
// in secrets.h at home, elsewhere by asking on the network), connects to
// ws://<host>:<port>, reconnects on its own, and
// turns the server's JSON commands (docs/protocol.md) into the same text
// commands the serial console uses — so "emotion happy" from the brain and
// "emo happy" typed over USB take the exact same path.

class Link {
 public:
  using CommandHandler = std::function<void(const String&)>;
  using StateHandler = std::function<void(bool connected)>;
  using AudioHandler = std::function<void(const uint8_t* pcm, size_t len)>;

  void begin(const char* ssid, const char* pass, const char* host,
             uint16_t port, const char* token, CommandHandler onCommand,
             StateHandler onState = nullptr);

  // Binary frames from the brain: type byte 0x01 = TTS audio (16 kHz s16le).
  void onAudio(AudioHandler h) { onAudio_ = std::move(h); }

  // Announced in the hello: the robot listens for this wake word itself.
  void setWakeWord(const char* word) { wakeWord_ = word; }
  // Also in the hello: whether his eyes are shut, so a restarted brain
  // carries on where the robot is instead of putting him to sleep.
  void setAsleepSource(std::function<bool()> f) { asleep_ = std::move(f); }

  // Call every loop().
  void update(uint32_t nowMs);

  bool connected() const { return connected_; }
  void printWifi() const;  // the serial console's `wifi`: names, never passwords
  static const char* bootReason();  // why the chip last started, for the hello

  // Robot → server. Text is sent as-is (should be JSON).
  void sendJson(const String& json);
  void sendNote(const char* text);  // a line for the brain's log
  void sendState(float panDeg, const char* emotion);
  // Binary frame: one type byte (0x01 = mic audio, 0x02 = JPEG) + payload.
  void sendBinary(uint8_t type, const uint8_t* data, size_t len);

 private:
  void onEvent(WStype_t type, uint8_t* payload, size_t length);
  void handleMessage(const char* json);
  bool handleWifiMessage(const char* type, JsonDocument& doc, uint32_t nowMs);
  void loadNetworks(const char* homeSsid, const char* homePass);
  void saveNetworks() const;
  bool knows(const String& ssid) const;
  void startScan(uint32_t nowMs);
  void onScanDone(int found, uint32_t nowMs, bool wifiUp);
  void joinNext(uint32_t nowMs);
  void onJoined(uint32_t nowMs);
  void startBrain(const String& host, uint16_t port, uint32_t nowMs);
  void findBrain(uint32_t nowMs);
  void sendWifi();

  struct Network {
    String ssid, pass;
  };
  struct Seen {
    String ssid;
    int rssi;
    bool open;
  };

  WebSocketsClient ws_;
  CommandHandler onCommand_;
  StateHandler onState_;
  AudioHandler onAudio_;
  std::vector<Network> networks_;  // known: the one in secrets.h first
  std::vector<Seen> seen_;         // what the last scan asked for from the live view found
  std::vector<String> toTry_;      // this scan's known networks, best first
  String home_;                    // secrets.h's network: its brain address is secrets.h's too
  String homeBrain_;
  String last_;                    // the network last joined or asked for: tried first when it's there
  String here_;                    // the network the brain link belongs to
  String brainHost_;               // "" until one is found on this network
  uint16_t brainPort_ = 0;
  bool brainStarted_ = false;
  uint32_t brainSinceMs_ = 0;      // since when this address has been tried
  WiFiUDP finder_;
  uint32_t nextAskMs_ = 0;
  bool scanning_ = false;
  bool scanWanted_ = false;        // asked for from the live view: report what it finds
  bool joining_ = false;
  bool tryLastBlind_ = false;      // nothing known in sight: every other round try the last one (hidden networks)
  bool switchWanted_ = false;      // the live view chose a network: leave this one
  bool switching_ = false;         // ...and this drop is that, so look around at once
  uint32_t joinAtMs_ = 0;
  uint32_t nextScanMs_ = 0;
  const char* token_ = "";
  bool connected_ = false;
  bool wifiReported_ = false;
  uint32_t lastTxCheckMs_ = 0;    // transmit power: last look at the signal
  bool lowTxPower_ = false;       // turned down because the router is close
  uint32_t nextStateMs_ = 0;
  const char* wakeWord_ = nullptr;
  std::function<bool()> asleep_;
};
