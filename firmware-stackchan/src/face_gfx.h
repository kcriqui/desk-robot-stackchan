#pragma once

#include <M5Unified.h>

// FaceGfx: the canvas face.cpp draws on, a band across the middle of the
// StackChan's 320x240 LCD.
//
// face.cpp thinks in 128x64 "face pixels" (the original OLED's, so face packs
// from the original project fit as they are) and rasterizes its shapes at the
// LCD's own resolution, SCALE screen pixels to the face pixel, so edges come
// out smooth instead of blocky. The canvas is 4 bits a pixel: each colour is
// an ink (below) whose colour can change without redrawing, which is how the
// eyes take on each emotion's colour.

class FaceGfx {
 public:
  static constexpr int FACE_W = 128;  // what face.cpp draws in
  static constexpr int FACE_H = 64;
  static constexpr float SCALE = 2.5f;  // 128 x 2.5 = 320, the LCD width

  enum Ink : uint8_t {
    BG = 0,     // background
    EYE = 1,    // the eyes and most marks: the emotion's colour
    PUPIL = 2,  // the dark of the pupil and an open mouth
    SHINE = 3,  // the glint in each pupil, sparkles
    BLUSH = 4,
    TEAR = 5,
    HEART = 6,
    STEAM = 7,
  };

  // bg: RGB888 background (the rest of the screen too).
  void configure(uint32_t bg) { bg_ = bg; }
  void begin();
  void clear() { canvas_.fillSprite(BG); }
  void push() { canvas_.pushSprite(0, bandY_); }
  void setInk(Ink ink, uint32_t rgb888) { canvas_.setPaletteColor(ink, rgb888); }

  int width() const { return canvas_.width(); }    // screen pixels
  int height() const { return canvas_.height(); }

  // Screen-pixel drawing.
  void span(int y, int x0, int x1, uint8_t ink);  // pixels x0 .. x1-1 of row y
  void fillRect(int x, int y, int w, int h, uint8_t ink);
  void fillCircle(float cx, float cy, float r, uint8_t ink);
  void fillTriangle(float x0, float y0, float x1, float y1, float x2, float y2, uint8_t ink) {
    canvas_.fillTriangle(lroundf(x0), lroundf(y0), lroundf(x1), lroundf(y1), lroundf(x2), lroundf(y2), ink);
  }
  // A line hw either side of its centre line. Filled triangles, not
  // drawWideLine: that one anti-aliases by reading pixels back, which
  // crashes on a palette canvas.
  void line(float ax, float ay, float bx, float by, float hw, uint8_t ink);

 private:
  M5Canvas canvas_{&M5.Display};
  int bandY_ = 0;
  uint32_t bg_ = 0x000000;
};
