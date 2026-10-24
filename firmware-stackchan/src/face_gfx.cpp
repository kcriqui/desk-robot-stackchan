#include "face_gfx.h"

void FaceGfx::begin() {
  M5.Display.setRotation(1);  // landscape, 320x240
  M5.Display.fillScreen(M5.Display.color888(bg_ >> 16, (bg_ >> 8) & 0xFF, bg_ & 0xFF));

  // Four bits a pixel: 320x160 is 25.6 KB (in PSRAM), and the palette paints
  // it on push.
  canvas_.setPsram(true);
  canvas_.setColorDepth(4);
  canvas_.createSprite(lroundf(FACE_W * SCALE), lroundf(FACE_H * SCALE));
  canvas_.createPalette();
  resetInks();
  bandY_ = (M5.Display.height() - canvas_.height()) / 2;
}

void FaceGfx::resetInks() {
  setInk(BG, bg_);
  setInk(EYE, 0xFFFFFF);
  setInk(PUPIL, 0x0A0C12);
  setInk(SHINE, 0xFFFFFF);
  setInk(BLUSH, 0xFF7AA8);
  setInk(TEAR, 0x7AC8FF);
  setInk(HEART, 0xFF4A6A);
  setInk(STEAM, 0xFF8A3A);
}

void FaceGfx::text(const char* s, int x, int y, const lgfx::IFont* font, uint8_t ink, uint8_t datum) {
  canvas_.setFont(font);
  canvas_.setTextColor(ink);
  canvas_.setTextDatum(datum);
  canvas_.drawString(s, x, y);
}

int FaceGfx::textWidth(const char* s, const lgfx::IFont* font) {
  canvas_.setFont(font);
  return canvas_.textWidth(s);
}

void FaceGfx::screenSpan(int y, int x0, int x1, uint32_t rgb) {
  int sy = bandY_ + y;
  x0 = max(x0, 0);
  x1 = min(x1, static_cast<int>(M5.Display.width()));
  if (sy < 0 || sy >= M5.Display.height() || x1 <= x0) return;
  M5.Display.drawFastHLine(x0, sy, x1 - x0, M5.Display.color565(rgb >> 16, (rgb >> 8) & 0xFF, rgb & 0xFF));
}

void FaceGfx::span(int y, int x0, int x1, uint8_t ink) {
  if (y < 0 || y >= static_cast<int>(canvas_.height())) return;
  x0 = max(x0, 0);
  x1 = min(x1, static_cast<int>(canvas_.width()));
  if (x1 > x0) canvas_.drawFastHLine(x0, y, x1 - x0, ink);
}

void FaceGfx::fillRect(int x, int y, int w, int h, uint8_t ink) {
  if (w > 0 && h > 0) canvas_.fillRect(x, y, w, h, ink);
}

void FaceGfx::fillCircle(float cx, float cy, float r, uint8_t ink) {
  if (r <= 0) return;
  // Row by row from the exact circle, so a small one isn't lumpy.
  int y0 = static_cast<int>(floorf(cy - r)), y1 = static_cast<int>(ceilf(cy + r));
  for (int y = y0; y <= y1; ++y) {
    float dy = y + 0.5f - cy;
    float half2 = r * r - dy * dy;
    if (half2 <= 0) continue;
    float half = sqrtf(half2);
    span(y, static_cast<int>(lroundf(cx - half)), static_cast<int>(lroundf(cx + half)), ink);
  }
}

void FaceGfx::line(float ax, float ay, float bx, float by, float hw, uint8_t ink) {
  float dx = bx - ax, dy = by - ay;
  float len = sqrtf(dx * dx + dy * dy);
  if (len < 0.01f) {
    fillCircle(ax, ay, hw, ink);
    return;
  }
  float nx = -dy / len * hw, ny = dx / len * hw;  // half-width, across the line
  int p0x = lroundf(ax + nx), p0y = lroundf(ay + ny);
  int p1x = lroundf(ax - nx), p1y = lroundf(ay - ny);
  int p2x = lroundf(bx - nx), p2y = lroundf(by - ny);
  int p3x = lroundf(bx + nx), p3y = lroundf(by + ny);
  canvas_.fillTriangle(p0x, p0y, p1x, p1y, p2x, p2y, ink);
  canvas_.fillTriangle(p0x, p0y, p2x, p2y, p3x, p3y, ink);
}
