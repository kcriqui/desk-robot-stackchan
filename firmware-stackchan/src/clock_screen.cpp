#include "clock_screen.h"

#include <M5Unified.h>
#include <Preferences.h>
#include <esp_sntp.h>
#include <sys/time.h>
#include <time.h>

namespace {

volatile bool synced = false;  // SNTP set the system clock (from its task)

void onSync(struct timeval*) { synced = true; }

// Days since 1970-01-01 for a civil date (Howard Hinnant's algorithm): the
// clock chip keeps UTC, and newlib has no timegm().
long daysFromCivil(int y, unsigned m, unsigned d) {
  y -= m <= 2;
  const long era = (y >= 0 ? y : y - 399) / 400;
  const unsigned yoe = static_cast<unsigned>(y - era * 400);
  const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return era * 146097 + static_cast<long>(doe) - 719468;
}

const uint32_t WEATHER_STALE_MS = 3UL * 60 * 60 * 1000;  // older than this: not shown

uint16_t amber() { return M5.Display.color565(0xF2, 0xB4, 0x5C); }
uint16_t grey() { return M5.Display.color565(0x8A, 0x94, 0xA6); }
uint16_t ink() { return M5.Display.color565(0xEC, 0xE6, 0xD9); }

// Screen regions (320 x 240).
const int DATE_Y = 30, TIME_Y = 108, WX_TOP = 168;

}  // namespace

void ClockScreen::begin() {
  Preferences p;
  if (p.begin("clock", true)) {
    tz_ = p.getString("tz", tz_);
    h24_ = p.getBool("h24", h24_);
    p.end();
  }
  setenv("TZ", tz_.c_str(), 1);
  tzset();
  if (!timeKnown() && M5.Rtc.isEnabled()) {
    m5::rtc_datetime_t dt;
    if (M5.Rtc.getDateTime(&dt) && dt.date.year >= 2025) {
      long days = daysFromCivil(dt.date.year, dt.date.month, dt.date.date);
      timeval tv{static_cast<time_t>(days * 86400L + dt.time.hours * 3600L + dt.time.minutes * 60L +
                                     dt.time.seconds), 0};
      settimeofday(&tv, nullptr);
      Serial.println(F("clock: time from the clock chip until internet time arrives"));
    }
  }
  sntp_set_time_sync_notification_cb(onSync);
  configTzTime(tz_.c_str(), "pool.ntp.org", "time.google.com");
}

void ClockScreen::update(uint32_t nowMs) {
  // Internet time arrived (and every 6 h after): keep the clock chip in step,
  // in UTC.
  if (synced || (rtcSet_ && static_cast<int32_t>(nowMs - nextRtcMs_) >= 0 && timeKnown())) {
    synced = false;
    if (M5.Rtc.isEnabled()) {
      time_t t = time(nullptr);
      tm utc;
      gmtime_r(&t, &utc);
      M5.Rtc.setDateTime(&utc);
    }
    rtcSet_ = true;
    nextRtcMs_ = nowMs + 6UL * 60 * 60 * 1000;
  }
}

bool ClockScreen::timeKnown() const {
  return time(nullptr) > 1735689600;  // after 2025-01-01: set, not the 1970 the chip starts at
}

void ClockScreen::setClock(const String& tz, bool h24) {
  if (tz.isEmpty() || tz.length() > 60) return;
  if (tz == tz_ && h24 == h24_) return;
  tz_ = tz;
  h24_ = h24;
  setenv("TZ", tz_.c_str(), 1);
  tzset();
  Preferences p;
  if (p.begin("clock", false)) {
    p.putString("tz", tz_);
    p.putBool("h24", h24_);
    p.end();
  }
  shownMinute_ = -1;  // redraw in the new zone
  Serial.printf("clock: time zone %s, %s clock\n", tz_.c_str(), h24_ ? "24-hour" : "12-hour");
}

bool ClockScreen::setWeather(const String& args) {
  int temp, hi, lo, code, day;
  char unit;
  int used = 0;
  if (sscanf(args.c_str(), "%d %d %d %d %d %c %n", &temp, &hi, &lo, &code, &day, &unit, &used) < 6) return false;
  wx_.valid = true;
  wx_.temp = temp;
  wx_.hi = hi;
  wx_.lo = lo;
  wx_.code = code;
  wx_.day = day != 0;
  wx_.unit = unit == 'C' || unit == 'c' ? 'C' : 'F';
  strlcpy(wx_.sky, args.c_str() + used, sizeof(wx_.sky));
  wx_.atMs = millis();
  weatherSeq_++;
  return true;
}

void ClockScreen::printStatus() const {
  time_t t = time(nullptr);
  tm local;
  localtime_r(&t, &local);
  char buf[32];
  strftime(buf, sizeof buf, "%Y-%m-%d %H:%M:%S", &local);
  Serial.printf("clock: %s (%s), tz %s, %s; weather %s\n", timeKnown() ? buf : "not set", rtcSet_ ? "chip synced" : "chip not synced",
                tz_.c_str(), h24_ ? "24h" : "12h",
                wx_.valid ? (String(wx_.temp) + wx_.unit + " " + wx_.sky + ", " +
                             String((millis() - wx_.atMs) / 60000) + " min old").c_str()
                          : "none yet");
}

void ClockScreen::draw(uint32_t nowMs, bool force) {
  auto& d = M5.Display;
  time_t t = time(nullptr);
  tm local;
  localtime_r(&t, &local);
  int minute = timeKnown() ? local.tm_hour * 60 + local.tm_min : -2;
  bool wxFresh = wx_.valid && nowMs - wx_.atMs < WEATHER_STALE_MS;
  uint32_t wxKey = wxFresh ? weatherSeq_ : 0;
  if (!force && minute == shownMinute_ && wxKey == shownWeather_) return;
  if (force) d.fillScreen(TFT_BLACK);
  d.startWrite();

  if (force || minute != shownMinute_) {
    shownMinute_ = minute;
    // Date.
    d.fillRect(0, 0, d.width(), WX_TOP - 4, TFT_BLACK);
    d.setTextDatum(middle_center);
    d.setFont(&fonts::FreeSans12pt7b);
    d.setTextColor(grey(), TFT_BLACK);
    char line[40];
    if (timeKnown()) {
      strftime(line, sizeof line, "%A, %b %e", &local);
    } else {
      strlcpy(line, "waiting for the time", sizeof line);
    }
    d.drawString(line, d.width() / 2, DATE_Y);
    // Time: big seven-segment digits, with AM/PM beside them.
    char hm[8] = "--:--";
    const char* ampm = "";
    if (timeKnown()) {
      int h = local.tm_hour;
      if (!h24_) {
        ampm = h < 12 ? "AM" : "PM";
        h = h % 12 == 0 ? 12 : h % 12;
      }
      snprintf(hm, sizeof hm, h24_ ? "%02d:%02d" : "%d:%02d", h, local.tm_min);
    }
    d.setFont(&fonts::Font7);
    d.setTextSize(1.7f);
    d.setTextColor(amber(), TFT_BLACK);
    int w = d.textWidth(hm);
    int x = d.width() / 2 - (*ampm ? 18 : 0);
    d.drawString(hm, x, TIME_Y);
    d.setTextSize(1);
    if (*ampm) {
      d.setFont(&fonts::FreeSansBold12pt7b);
      d.setTextDatum(top_left);
      d.drawString(ampm, x + w / 2 + 6, TIME_Y - 38);
    }
  }

  if (force || wxKey != shownWeather_) {
    shownWeather_ = wxKey;
    d.fillRect(0, WX_TOP, d.width(), d.height() - WX_TOP, TFT_BLACK);
    if (wxFresh) {
      drawIcon(wx_.code, wx_.day, 52, WX_TOP + 34);
      d.setTextDatum(middle_left);
      d.setFont(&fonts::FreeSansBold18pt7b);
      d.setTextColor(ink(), TFT_BLACK);
      String temp = String(wx_.temp);
      d.drawString(temp, 96, WX_TOP + 24);
      int tw = d.textWidth(temp);
      drawDegree(96 + tw + 6, WX_TOP + 12, 4, ink());
      d.setFont(&fonts::FreeSans9pt7b);
      d.setTextColor(grey(), TFT_BLACK);
      d.drawString(wx_.sky, 96, WX_TOP + 54);
      d.setTextDatum(middle_right);
      d.setFont(&fonts::FreeSans12pt7b);
      d.drawString(String("H ") + wx_.hi + "   L " + wx_.lo, d.width() - 14, WX_TOP + 24);
    }
  }
  d.endWrite();
}

void ClockScreen::drawDegree(int x, int y, int r, uint16_t color) {
  M5.Display.drawCircle(x, y, r, color);
  M5.Display.drawCircle(x, y, r - 1, color);
}

// A small picture for the WMO weather code: sun, moon, cloud, rain, snow,
// fog, storm. Drawn from shapes, so it needs no image files.
void ClockScreen::drawIcon(int code, bool day, int cx, int cy) {
  auto& d = M5.Display;
  uint16_t sun = d.color565(0xF2, 0xB4, 0x5C), moon = d.color565(0xC8, 0xCC, 0xD8);
  uint16_t cloud = d.color565(0xA8, 0xB0, 0xBE), drop = d.color565(0x6A, 0xA8, 0xE8), bolt = d.color565(0xFF, 0xD8, 0x40);
  bool clear = code <= 1, partly = code == 2;
  bool fog = code == 45 || code == 48;
  bool snow = (code >= 71 && code <= 77) || code == 85 || code == 86;
  bool rain = (code >= 51 && code <= 67) || (code >= 80 && code <= 82);
  bool storm = code >= 95;
  if (clear || partly) {  // the sun or moon, behind the cloud if partly cloudy
    int sx = partly ? cx - 8 : cx, sy = partly ? cy - 8 : cy;
    if (day) {
      d.fillCircle(sx, sy, 13, sun);
      for (int i = 0; i < 8; ++i) {
        float a = i * PI / 4;
        d.drawLine(sx + cosf(a) * 17, sy + sinf(a) * 17, sx + cosf(a) * 23, sy + sinf(a) * 23, sun);
      }
    } else {
      d.fillCircle(sx, sy, 13, moon);
      d.fillCircle(sx + 7, sy - 5, 11, TFT_BLACK);
    }
    if (clear) return;
  }
  // A cloud: three overlapping circles on a flat base.
  int cy0 = rain || snow || storm ? cy - 6 : cy + 2;
  d.fillCircle(cx - 10, cy0 + 2, 10, cloud);
  d.fillCircle(cx + 2, cy0 - 5, 13, cloud);
  d.fillCircle(cx + 14, cy0 + 3, 9, cloud);
  d.fillRect(cx - 10, cy0 + 2, 24, 11, cloud);
  if (fog) {
    for (int i = 0; i < 3; ++i) d.fillRect(cx - 22, cy0 + 18 + i * 7, 44, 3, cloud);
  } else if (rain) {
    for (int i = 0; i < 4; ++i) d.drawLine(cx - 12 + i * 8, cy0 + 18, cx - 16 + i * 8, cy0 + 28, drop);
  } else if (snow) {
    for (int i = 0; i < 4; ++i) d.fillCircle(cx - 12 + i * 8, cy0 + 22 + (i % 2) * 5, 2, TFT_WHITE);
  } else if (storm) {
    d.fillTriangle(cx + 2, cy0 + 14, cx - 6, cy0 + 28, cx + 2, cy0 + 26, bolt);
    d.fillTriangle(cx + 2, cy0 + 26, cx - 2, cy0 + 26, cx + 8, cy0 + 16, bolt);
  }
}
