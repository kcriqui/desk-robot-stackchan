#pragma once

#include <M5Unified.h>

// FaceGfx: the slice of the U8g2 API that face.cpp uses, drawn on the
// StackChan's 320x240 LCD instead of a 128x64 OLED.
//
// face.cpp is the upstream file, unchanged: it still thinks in 128x64
// "face pixels". Every call here scales those by SCALE onto a 1-bit canvas
// (a full-width band across the middle of the screen), and sendBuffer()
// pushes the band in the face colour. Keeping the API identical means face
// improvements from the original repo drop straight in.

// U8g2 font stand-ins. face.cpp names these fonts; here each is just its
// glyph height in face pixels, which setFont() turns into a text size.
inline constexpr uint8_t u8g2_font_6x10_tr[] = {10};
inline constexpr uint8_t u8g2_font_9x15B_tr[] = {15};
inline constexpr uint8_t u8g2_font_10x20_tr[] = {20};

class FaceGfx {
 public:
  static constexpr int FACE_W = 128;  // what face.cpp draws in
  static constexpr int FACE_H = 64;
  static constexpr float SCALE = 2.5f;  // 128 x 2.5 = 320, the LCD width

  // fg/bg: RGB888 face and background colours.
  void configure(uint32_t fg, uint32_t bg) { fg_ = fg; bg_ = bg; }

  // U8g2 API used by face.cpp.
  void begin();
  void setBusClock(uint32_t) {}  // the LCD bus is M5GFX's business
  void clearBuffer();
  void sendBuffer();
  void setDrawColor(uint8_t c) { color_ = c ? 1 : 0; }
  void drawBox(int x, int y, int w, int h);
  void drawRBox(int x, int y, int w, int h, int r);
  void drawDisc(int x, int y, int r);
  void drawTriangle(int x0, int y0, int x1, int y1, int x2, int y2);
  void drawLine(int x0, int y0, int x1, int y1);
  void setFont(const uint8_t* font) { fontH_ = font ? font[0] : 10; }
  void drawStr(int x, int y, const char* s);

 private:
  // Face pixel edge → screen pixel, and face pixel centre → screen pixel.
  static int edge(int v) { return static_cast<int>(lroundf(v * SCALE)); }
  static float mid(int v) { return (v + 0.5f) * SCALE; }

  M5Canvas canvas_{&M5.Display};
  int bandY_ = 0;
  uint8_t color_ = 1;
  uint8_t fontH_ = 10;
  uint32_t fg_ = 0xFFFFFF;
  uint32_t bg_ = 0x000000;
};
