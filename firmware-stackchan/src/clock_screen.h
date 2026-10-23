#pragma once

#include <Arduino.h>

// ClockScreen: what the LCD shows while Rocky is asleep (after a few seconds
// of the sleeping face): the time, the date, and the weather at home. An idea
// from the original project's V2 home screen, drawn for the CoreS3's colour
// LCD.
//
// The time comes from the internet (SNTP) in the time zone the brain sends
// (`clock` message, kept in flash), and is written to the CoreS3's clock chip,
// so after a power cut it shows the right time before WiFi is back. The
// weather comes from the brain (`weather` message, about every 30 min): it
// already asks Open-Meteo, and fetching over HTTPS here would cost ~40 KB of
// internal RAM the wake word needs. On battery, deep sleep turns the screen off
// instead.

class ClockScreen {
 public:
  // Loads the saved time zone, sets the system clock from the clock chip if
  // the internet hasn't yet, and starts SNTP (it waits for WiFi itself).
  void begin();
  // Call every loop: keeps the clock chip in step with internet time.
  void update(uint32_t nowMs);

  // "clock <posix tz> [12|24]": the time zone (e.g. PST8PDT,M3.2.0,M11.1.0)
  // and the clock style. Saved when it changes.
  void setClock(const String& tz, bool h24);
  // "weather <temp> <hi> <lo> <wmo code> <day 0|1> <F|C> <words...>"
  bool setWeather(const String& args);

  // Draws the whole screen (force) or just what changed: call while shown.
  void draw(uint32_t nowMs, bool force);
  bool timeKnown() const;
  void printStatus() const;

 private:
  void drawIcon(int code, bool day, int cx, int cy);
  void drawDegree(int x, int y, int r, uint16_t color);

  String tz_ = "UTC0";
  bool h24_ = false;
  bool rtcSet_ = false;
  uint32_t nextRtcMs_ = 0;
  int shownMinute_ = -1;
  uint32_t shownWeather_ = 0;  // weatherSeq_ when last drawn

  struct Weather {
    bool valid = false;
    int temp = 0, hi = 0, lo = 0, code = -1;
    bool day = true;
    char unit = 'F';
    char sky[32] = "";
    uint32_t atMs = 0;
  } wx_;
  uint32_t weatherSeq_ = 0;
};
