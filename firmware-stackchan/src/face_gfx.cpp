#include "face_gfx.h"

void FaceGfx::begin() {
  M5.Display.setRotation(1);  // landscape, 320x240
  M5.Display.fillScreen(M5.Display.color888(bg_ >> 16, (bg_ >> 8) & 0xFF, bg_ & 0xFF));

  // One bit per pixel: 320x160 is 6.4 KB, and the palette paints it on push.
  canvas_.setColorDepth(1);
  canvas_.createSprite(edge(FACE_W), edge(FACE_H));
  canvas_.createPalette();  // colours below are palette indices: 0 = bg, 1 = face
  canvas_.setPaletteColor(0, bg_);
  canvas_.setPaletteColor(1, fg_);
  canvas_.setTextDatum(textdatum_t::baseline_left);  // U8g2 draws strings from the baseline
  canvas_.setFont(&fonts::Font2);
  bandY_ = (M5.Display.height() - canvas_.height()) / 2;
}

void FaceGfx::clearBuffer() { canvas_.fillSprite(0); }

void FaceGfx::sendBuffer() { canvas_.pushSprite(0, bandY_); }

void FaceGfx::drawBox(int x, int y, int w, int h) {
  if (w <= 0 || h <= 0) return;
  canvas_.fillRect(edge(x), edge(y), edge(x + w) - edge(x), edge(y + h) - edge(y), color_);
}

void FaceGfx::drawRBox(int x, int y, int w, int h, int r) {
  if (w <= 0 || h <= 0) return;
  canvas_.fillRoundRect(edge(x), edge(y), edge(x + w) - edge(x), edge(y + h) - edge(y),
                        edge(r), color_);
}

void FaceGfx::drawDisc(int x, int y, int r) {
  // A U8g2 disc of radius r covers 2r+1 pixels across.
  canvas_.fillCircle(lroundf(mid(x)), lroundf(mid(y)), lroundf((r + 0.5f) * SCALE), color_);
}

void FaceGfx::drawTriangle(int x0, int y0, int x1, int y1, int x2, int y2) {
  canvas_.fillTriangle(lroundf(mid(x0)), lroundf(mid(y0)), lroundf(mid(x1)), lroundf(mid(y1)),
                       lroundf(mid(x2)), lroundf(mid(y2)), color_);
}

void FaceGfx::drawLine(int x0, int y0, int x1, int y1) {
  // A one-face-pixel line is SCALE screen pixels wide: a filled quad (two
  // triangles). Not drawWideLine: that one anti-aliases by reading pixels
  // back, which crashes on a 1-bit palette canvas.
  float ax = mid(x0), ay = mid(y0), bx = mid(x1), by = mid(y1);
  float dx = bx - ax, dy = by - ay;
  float len = sqrtf(dx * dx + dy * dy);
  float hw = SCALE / 2;
  if (len < 0.01f) {
    canvas_.fillCircle(lroundf(ax), lroundf(ay), lroundf(hw), color_);
    return;
  }
  float nx = -dy / len * hw, ny = dx / len * hw;  // half-width, across the line
  int p0x = lroundf(ax + nx), p0y = lroundf(ay + ny);
  int p1x = lroundf(ax - nx), p1y = lroundf(ay - ny);
  int p2x = lroundf(bx - nx), p2y = lroundf(by - ny);
  int p3x = lroundf(bx + nx), p3y = lroundf(by + ny);
  canvas_.fillTriangle(p0x, p0y, p1x, p1y, p2x, p2y, color_);
  canvas_.fillTriangle(p0x, p0y, p2x, p2y, p3x, p3y, color_);
}

void FaceGfx::drawStr(int x, int y, const char* s) {
  // Font2 is 16 px tall; size it to the requested face-pixel height.
  canvas_.setTextSize(fontH_ * SCALE / 16.0f);
  canvas_.setTextColor(color_);
  canvas_.drawString(s, edge(x), edge(y));
}
