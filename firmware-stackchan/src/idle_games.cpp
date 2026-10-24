#include "idle_games.h"

// From the original project's V2 (cgro00/desk-robot firmware/src/idle_games.cpp, MIT): the three games and how
// they play themselves are V2's, coordinate for coordinate in its 128 x 64 pixels. What changed for the StackChan:
// colour (Tetris pieces, the river and logs, cars, the frog), drawn on the face's canvas at the LCD's resolution,
// and no burn-in shift (an LCD doesn't need one).

namespace {

constexpr float S = FaceGfx::SCALE;

// The games' inks (setInks): they take the face's palette while they play.
enum : uint8_t {
  G_TEXT = 1, G_DIM = 2, G_AMBER = 3,
  G_PIECE = 4,  // 4..10: the seven Tetris pieces
  G_WATER = 11, G_LOG = 12, G_CAR = 13, G_FROG = 14, G_GRASS = 15,
};
const uint32_t PIECE_COLORS[7] = {0x5AD2F2, 0x5A7CF2, 0xF2A03A, 0xF2E05A, 0x6AD26A, 0xB46AF2, 0xF25A5A};

float frand(float lo, float hi) { return lo + (hi - lo) * (random(0, 10001) / 10000.0f); }

// ── Tetris pieces: 4 rotations each, 4 x 4 cells, bit 15 = row 0 column 0 ───────────────────────────────────────
const uint16_t SHAPES[7][4] = {
    {0x0F00, 0x2222, 0x00F0, 0x4444},   // I
    {0x8E00, 0x6440, 0x0E20, 0x44C0},   // J
    {0x2E00, 0x4460, 0x0E80, 0xC440},   // L
    {0x6600, 0x6600, 0x6600, 0x6600},   // O
    {0x6C00, 0x4620, 0x06C0, 0x8C40},   // S
    {0x4E00, 0x4640, 0x0E40, 0x4C40},   // T
    {0xC600, 0x2640, 0x0C60, 0x4C80},   // Z
};
inline bool cell(uint16_t mask, int r, int c) { return (mask >> (15 - (r * 4 + c))) & 1; }
constexpr int COLS = 10, ROWS = 20;
constexpr uint16_t FULL = (1 << COLS) - 1;
constexpr int WELL_X = 49, WELL_Y = 2, CELL = 3;   // the well: 30 x 60 px in the middle of the screen

// ── Frogger: 9 rows of 6 px under a 9 px bar; objects wrap round a strip wider than the screen ─────────────────────
constexpr int FROG_ROW0 = 9, FROG_ROW_H = 6;
constexpr float SPAN = 160.0f;          // -32 .. 128: objects leave one side and come back on the other
constexpr int HOP = 3;                  // frames a hop takes
inline int rowY(int r) { return FROG_ROW0 + r * FROG_ROW_H; }
inline bool river(int r) { return r >= 1 && r <= 3; }
inline bool road(int r) { return r >= 5 && r <= 7; }
float wrapX(float v) {
  v = fmodf(v + 32.0f, SPAN);
  if (v < 0) v += SPAN;
  return v - 32.0f;
}

}  // namespace

void IdleGames::setInks() {
  gfx_.setInk(FaceGfx::BG, 0x000000);
  gfx_.setInk(static_cast<FaceGfx::Ink>(G_TEXT), 0xECE6D9);
  gfx_.setInk(static_cast<FaceGfx::Ink>(G_DIM), 0x4A5262);
  gfx_.setInk(static_cast<FaceGfx::Ink>(G_AMBER), 0xF2B45C);
  for (int i = 0; i < 7; ++i) gfx_.setInk(static_cast<FaceGfx::Ink>(G_PIECE + i), PIECE_COLORS[i]);
  gfx_.setInk(static_cast<FaceGfx::Ink>(G_WATER), 0x123A5A);
  gfx_.setInk(static_cast<FaceGfx::Ink>(G_LOG), 0x9A6A3A);
  gfx_.setInk(static_cast<FaceGfx::Ink>(G_CAR), 0xE0605A);
  gfx_.setInk(static_cast<FaceGfx::Ink>(G_FROG), 0x7AE05A);
  gfx_.setInk(static_cast<FaceGfx::Ink>(G_GRASS), 0x2E6A34);
}

void IdleGames::box(float x, float y, float w, float h, uint8_t ink) {
  int x0 = static_cast<int>(floorf(x * S)), y0 = static_cast<int>(floorf(y * S));
  int x1 = static_cast<int>(floorf((x + w) * S)), y1 = static_cast<int>(floorf((y + h) * S));
  if (x1 > x0 && y1 > y0) gfx_.fillRect(x0, y0, x1 - x0, y1 - y0, ink);
}

void IdleGames::text(const char* s, float x, float baseline, bool big, uint8_t ink, bool right) {
  gfx_.text(s, lroundf(x * S), lroundf(baseline * S), big ? &fonts::FreeSansBold12pt7b : &fonts::FreeSans9pt7b, ink,
            right ? baseline_right : baseline_left);
}

float IdleGames::textWidth(const char* s, bool big) {
  return gfx_.textWidth(s, big ? &fonts::FreeSansBold12pt7b : &fonts::FreeSans9pt7b) / S;
}

void IdleGames::reset(IdleMode m) {
  mode_ = m;
  frame_ = 0;
  if (m == IdleMode::Pong) pongReset();
  else if (m == IdleMode::Tetris) tetrisReset();
  else if (m == IdleMode::Frogger) frogReset(false);
  else if (m == IdleMode::Life) lifeSeed();
}

void IdleGames::step() {
  ++frame_;
  if (mode_ == IdleMode::Pong) pongStep();
  else if (mode_ == IdleMode::Tetris) tetrisStep();
  else if (mode_ == IdleMode::Frogger) frogStep();
  else if (mode_ == IdleMode::Life) lifeStep();
}

void IdleGames::draw(const char* clock) {
  gfx_.clear();
  if (mode_ == IdleMode::Pong) pongDraw(clock);
  else if (mode_ == IdleMode::Tetris) tetrisDraw(clock);
  else if (mode_ == IdleMode::Frogger) frogDraw(clock);
  else if (mode_ == IdleMode::Life) lifeDraw(clock);
  gfx_.push();
}

// ── Pong ─────────────────────────────────────────────────────────────────────────────────────────────────────────
// The field is y 10..63 under a bar with the time. Paddles 2 x 12 at x 2 and 124; the ball is 2 x 2. Each paddle
// works out where the ball will reach it (bounces included) and heads there, aiming a little off-centre so the
// rally changes angle; the hit itself always happens, so it never misses.

namespace {
constexpr float PONG_TOP = 10, PONG_BOT = 62;   // the ball's top edge stays in here (it's 2 px tall)
constexpr float PAD_HALF = 6, LEFT_FACE = 4, RIGHT_FACE = 122;
}

void IdleGames::pongReset() {
  Pong p;
  p.vx = random(2) ? 2.2f : -2.2f;
  p.vy = frand(0.5f, 1.3f) * (random(2) ? 1 : -1);
  pong_ = p;
}

float IdleGames::pongPredict(int side) const {
  float x = pong_.bx, y = pong_.by, vx = pong_.vx, vy = pong_.vy;
  float face = side == 0 ? LEFT_FACE : RIGHT_FACE;
  for (int i = 0; i < 400; ++i) {
    if ((vx < 0 && x <= face) || (vx > 0 && x >= face)) break;
    x += vx;
    y += vy;
    if (y < PONG_TOP) { y = 2 * PONG_TOP - y; vy = -vy; }
    if (y > PONG_BOT) { y = 2 * PONG_BOT - y; vy = -vy; }
  }
  return y + 1;
}

void IdleGames::pongStep() {
  Pong& p = pong_;
  for (int s = 0; s < 2; ++s) {
    bool coming = s == 0 ? p.vx < 0 : p.vx > 0;
    float want = coming ? pongPredict(s) - p.aim[s] : 36.0f;
    float maxStep = coming ? 2.8f : 0.8f;
    float d = constrain(want - p.pad[s], -maxStep, maxStep);
    p.pad[s] = constrain(p.pad[s] + d, PONG_TOP + PAD_HALF, PONG_BOT + 2 - PAD_HALF);
  }
  p.bx += p.vx;
  p.by += p.vy;
  if (p.by < PONG_TOP) { p.by = 2 * PONG_TOP - p.by; p.vy = -p.vy; }
  if (p.by > PONG_BOT) { p.by = 2 * PONG_BOT - p.by; p.vy = -p.vy; }
  int side = p.vx < 0 && p.bx <= LEFT_FACE ? 0 : p.vx > 0 && p.bx >= RIGHT_FACE ? 1 : -1;
  if (side >= 0) {
    // it never misses: if the paddle is somehow off, it's already there
    float ballC = p.by + 1;
    if (fabsf(ballC - p.pad[side]) > PAD_HALF) p.pad[side] = ballC + (ballC > p.pad[side] ? -PAD_HALF + 1 : PAD_HALF - 1);
    float rel = constrain((ballC - p.pad[side]) / PAD_HALF, -1.0f, 1.0f);
    p.bx = side == 0 ? LEFT_FACE : RIGHT_FACE;
    p.vx = -p.vx * 1.04f;
    if (fabsf(p.vx) > 3.6f) p.vx = p.vx > 0 ? 3.6f : -3.6f;
    p.vy = constrain(p.vy * 0.6f + rel * 1.7f, -2.4f, 2.4f);
    if (fabsf(p.vy) < 0.35f) p.vy = p.vy < 0 ? -0.35f : 0.35f;
    p.aim[side] = frand(-4.5f, 4.5f);          // next time, a different spot on the paddle
    if (++p.hits % 40 == 0) p.vx = p.vx > 0 ? 2.2f : -2.2f;   // a long rally: calm down, build up again
  }
}

void IdleGames::pongDraw(const char* clock) {
  text(clock, 64 - textWidth(clock, false) / 2, 7.5f, false, G_TEXT);
  box(0, 9, 128, 0.6f, G_DIM);
  for (int y = 11; y < 64; y += 4) box(63.5f, y, 1, 2, G_DIM);
  for (int s = 0; s < 2; ++s) box(s == 0 ? 2 : 124, pong_.pad[s] - PAD_HALF, 2, PAD_HALF * 2, G_AMBER);
  box(pong_.bx, pong_.by, 2, 2, G_TEXT);
}

// ── Tetris ───────────────────────────────────────────────────────────────────────────────────────────────────────
// A 7-bag of pieces; for each new piece a small player tries every rotation and column and picks the placement with
// the lowest, flattest stack and the fewest holes (the usual four-number score). The piece slides and turns into
// place near the top, then drops a row a frame. Full rows flash and go. If the stack ever gets near the top, the
// board fills and empties with a wipe and the game goes on: it never ends.

void IdleGames::tetrisReset() {
  uint16_t lines = tet_.lines;
  tet_ = Tetris();
  tet_.lines = lines;
  tet_.next = tetrisDraw7();
  tetrisSpawn();
}

uint8_t IdleGames::tetrisDraw7() {
  if (tet_.bagLeft == 0) {
    for (int i = 0; i < 7; ++i) tet_.bag[i] = i;
    for (int i = 6; i > 0; --i) {
      int j = random(0, i + 1);
      uint8_t t = tet_.bag[i]; tet_.bag[i] = tet_.bag[j]; tet_.bag[j] = t;
    }
    tet_.bagLeft = 7;
  }
  return tet_.bag[--tet_.bagLeft];
}

bool IdleGames::tetrisCollides(const uint16_t* rows, uint8_t piece, uint8_t rot, int x, int y) const {
  uint16_t m = SHAPES[piece][rot & 3];
  for (int r = 0; r < 4; ++r)
    for (int c = 0; c < 4; ++c) {
      if (!cell(m, r, c)) continue;
      int bx = x + c, by = y + r;
      if (bx < 0 || bx >= COLS || by >= ROWS) return true;
      if (by >= 0 && (rows[by] >> bx & 1)) return true;
    }
  return false;
}

void IdleGames::tetrisPlan() {
  Tetris& t = tet_;
  float best = -1e9f;
  t.targetRot = t.rot;
  t.targetX = t.x;
  for (int rot = 0; rot < 4; ++rot) {
    for (int x = -2; x < COLS; ++x) {
      if (tetrisCollides(t.rows, t.piece, rot, x, -2)) continue;
      int y = -2;
      while (!tetrisCollides(t.rows, t.piece, rot, x, y + 1)) ++y;
      uint16_t b[ROWS];
      memcpy(b, t.rows, sizeof(b));
      bool over = false;
      uint16_t m = SHAPES[t.piece][rot];
      for (int r = 0; r < 4; ++r)
        for (int c = 0; c < 4; ++c)
          if (cell(m, r, c)) {
            if (y + r < 0) over = true;
            else b[y + r] |= 1 << (x + c);
          }
      if (over) continue;
      int cleared = 0;
      for (int r = 0; r < ROWS; ++r) if (b[r] == FULL) ++cleared;
      // heights and holes, as if the full rows were gone
      int h[COLS], holes = 0, agg = 0, bump = 0;
      for (int c = 0; c < COLS; ++c) {
        h[c] = 0;
        bool seen = false;
        for (int r = 0; r < ROWS; ++r) {
          if (b[r] == FULL) continue;
          bool filled = b[r] >> c & 1;
          if (filled && !seen) { seen = true; h[c] = ROWS - r; }
          if (!filled && seen) ++holes;
        }
        h[c] = h[c] > cleared ? h[c] - cleared : 0;
        agg += h[c];
      }
      for (int c = 0; c + 1 < COLS; ++c) bump += abs(h[c] - h[c + 1]);
      float score = -0.510066f * agg + 0.760666f * cleared - 0.35663f * holes - 0.184483f * bump;
      score += frand(0, 0.01f);                // ties go either way: a little variety
      if (score > best) { best = score; t.targetRot = rot; t.targetX = x; }
    }
  }
}

void IdleGames::tetrisSpawn() {
  Tetris& t = tet_;
  t.piece = t.next;
  t.next = tetrisDraw7();
  t.rot = 0;
  t.x = 3;
  t.y = -1;
  t.phase = 0;
  t.timer = 0;
  int top = ROWS;
  for (int r = 0; r < ROWS; ++r) if (t.rows[r]) { top = r; break; }
  if (top < 4 || tetrisCollides(t.rows, t.piece, t.rot, t.x, t.y)) {   // nearly full: wipe and carry on
    t.phase = 3;
    return;
  }
  tetrisPlan();
}

void IdleGames::tetrisStep() {
  Tetris& t = tet_;
  if (t.phase == 0) {          // turning and sliding into place, drifting down a little
    if (t.rot != t.targetRot) {
      uint8_t r = (t.rot + 1) & 3;
      if (!tetrisCollides(t.rows, t.piece, r, t.x, t.y)) t.rot = r;
      else t.phase = 1;
    } else if (t.x != t.targetX) {
      int nx = t.x + (t.targetX > t.x ? 1 : -1);
      if (!tetrisCollides(t.rows, t.piece, t.rot, nx, t.y)) t.x = nx;
      else t.phase = 1;
    } else {
      t.phase = 1;
    }
    if (t.phase == 0 && ++t.timer % 3 == 0 && !tetrisCollides(t.rows, t.piece, t.rot, t.x, t.y + 1)) ++t.y;
    return;
  }
  if (t.phase == 1) {          // dropping
    if (!tetrisCollides(t.rows, t.piece, t.rot, t.x, t.y + 1)) {
      ++t.y;
      return;
    }
    uint16_t m = SHAPES[t.piece][t.rot];
    for (int r = 0; r < 4; ++r)
      for (int c = 0; c < 4; ++c)
        if (cell(m, r, c) && t.y + r >= 0) {
          t.rows[t.y + r] |= 1 << (t.x + c);
          t.color[t.y + r][t.x + c] = t.piece;
        }
    t.clearRows = 0;
    for (int r = 0; r < ROWS; ++r) if (t.rows[r] == FULL) t.clearRows |= 1UL << r;
    if (t.clearRows) {
      t.phase = 2;
      t.timer = 0;
    } else {
      tetrisSpawn();
    }
    return;
  }
  if (t.phase == 2) {          // full rows flash, then go
    if (++t.timer < 6) return;
    int w = ROWS - 1;
    for (int r = ROWS - 1; r >= 0; --r) {
      if (t.clearRows >> r & 1) { ++t.lines; continue; }
      t.rows[w] = t.rows[r];
      memcpy(t.color[w], t.color[r], COLS);
      --w;
    }
    while (w >= 0) t.rows[w--] = 0;
    t.clearRows = 0;
    tetrisSpawn();
    return;
  }
  // phase 3: wipe. Fill from the bottom up, then empty from the top down, then a new game.
  ++t.timer;
  if (t.timer <= ROWS) {
    t.rows[ROWS - t.timer] = FULL;
    memset(t.color[ROWS - t.timer], 0xFF, COLS);   // the wipe is grey
  } else if (t.timer <= 2 * ROWS) {
    t.rows[t.timer - ROWS - 1] = 0;
  } else {
    tetrisReset();
  }
}

void IdleGames::tetrisDraw(const char* clock) {
  const Tetris& t = tet_;
  // the well's walls
  box(WELL_X - 2, 0, 1, 64, G_DIM);
  box(WELL_X + COLS * CELL + 1, 0, 1, 64, G_DIM);
  box(WELL_X - 2, 63, COLS * CELL + 4, 1, G_DIM);
  bool flashOn = (t.timer / 2) % 2 == 0;
  for (int r = 0; r < ROWS; ++r) {
    if ((t.clearRows >> r & 1) && t.phase == 2) {
      if (flashOn) box(WELL_X, WELL_Y + r * CELL, COLS * CELL - 1, CELL - 1, G_TEXT);
      continue;
    }
    for (int c = 0; c < COLS; ++c)
      if (t.rows[r] >> c & 1) {
        uint8_t k = t.color[r][c];
        box(WELL_X + c * CELL, WELL_Y + r * CELL, CELL - 0.6f, CELL - 0.6f, k < 7 ? G_PIECE + k : G_DIM);
      }
  }
  if (t.phase <= 1) {
    uint16_t m = SHAPES[t.piece][t.rot];
    for (int r = 0; r < 4; ++r)
      for (int c = 0; c < 4; ++c)
        if (cell(m, r, c) && t.y + r >= 0)
          box(WELL_X + (t.x + c) * CELL, WELL_Y + (t.y + r) * CELL, CELL - 0.6f, CELL - 0.6f, G_PIECE + t.piece);
  }
  // left: the time (number big, AM / PM small under it) and the lines cleared
  char num[12] = "", ampm[4] = "";
  sscanf(clock, "%11s %3s", num, ampm);
  text(num, 23 - textWidth(num, true) / 2, 17, true, G_AMBER);
  if (ampm[0]) text(ampm, 23 - textWidth(ampm, false) / 2, 26, false, G_TEXT);
  text("LINES", 4, 50, false, G_DIM);
  char buf[8];
  snprintf(buf, sizeof(buf), "%u", t.lines);
  text(buf, 4, 60, false, G_TEXT);
  // right: the next piece
  text("NEXT", 92, 10, false, G_DIM);
  uint16_t nm = SHAPES[t.next][0];
  for (int r = 0; r < 4; ++r)
    for (int c = 0; c < 4; ++c)
      if (cell(nm, r, c)) box(94 + c * 4, 16 + r * 4, 3.4f, 3.4f, G_PIECE + t.next);
}

// ── Frogger ──────────────────────────────────────────────────────────────────────────────────────────────────────
// Rows from the top: 0 home (grass), 1-3 a river with logs, 4 the bank in the middle, 5-7 a road with cars, 8 the
// start. The frog only hops when the row ahead will be safe when it lands (a gap in the traffic, a log under it)
// and for a moment after; on the road it steps back or sideways if a car would reach it. At the top it celebrates,
// the traffic gets a little faster, and it starts again from the bottom.

float IdleGames::laneX(const Lane& l, int i, int t) const { return wrapX(l.x[i] + l.dir * l.speed * t); }

void IdleGames::frogReset(bool keepLevel) {
  uint8_t level = keepLevel ? frog_.level : 1;
  frog_ = Frogger();
  frog_.level = level;
  float k = 1.0f + 0.08f * (level - 1);
  const float riverSpeed[3] = {0.6f, 0.9f, 0.7f}, roadSpeed[3] = {0.9f, 1.3f, 0.7f};
  for (int r = 1; r <= 7; ++r) {
    if (!river(r) && !road(r)) continue;
    Lane& l = frog_.lanes[r];
    l.dir = (r % 2) ? 1 : -1;
    l.speed = (river(r) ? riverSpeed[r - 1] : roadSpeed[r - 5]) * k;
    l.count = river(r) ? 3 : (r == 6 ? 2 : 3);
    float gap = SPAN / l.count;
    for (int i = 0; i < l.count; ++i) {
      l.len[i] = river(r) ? random(24, 35) : random(10, 15);
      l.x[i] = wrapX(i * gap + random(0, 10));
    }
  }
  frog_.fx = random(2, 13) * 8 + 2;
  frog_.row = 8;
  frog_.wait = 8;
}

bool IdleGames::frogSafe(uint8_t row, float fx, int fromT, int toT) const {
  if (fx < 0 || fx > 123) return false;               // stay on the screen
  if (!river(row) && !road(row)) return true;
  const Lane& l = frog_.lanes[row];
  if (road(row)) {
    for (int t = fromT; t <= toT; ++t)
      for (int i = 0; i < l.count; ++i) {
        float cx = laneX(l, i, t);
        if (fx + 6 > cx && fx - 1 < cx + l.len[i]) return false;
      }
    return true;
  }
  // river: there has to be a log under it when it lands, with a pixel to spare each side
  for (int i = 0; i < l.count; ++i) {
    float lx = laneX(l, i, fromT);
    if (fx >= lx + 1 && fx + 5 <= lx + l.len[i] - 1) return true;
  }
  return false;
}

void IdleGames::frogStep() {
  Frogger& f = frog_;
  // traffic and logs move; a frog on a log goes with it
  for (int r = 1; r <= 7; ++r) {
    Lane& l = f.lanes[r];
    for (int i = 0; i < l.count; ++i) l.x[i] = wrapX(l.x[i] + l.dir * l.speed);
  }
  if (river(f.row) && f.hop == 0) f.fx = wrapX(f.fx + f.lanes[f.row].dir * f.lanes[f.row].speed);

  if (f.party) {
    if (--f.party == 0) {
      if (f.level < 8) ++f.level;
      frogReset(true);
    }
    return;
  }
  if (f.hop) {
    --f.hop;
    if (f.hop == 0 && f.row == 0) f.party = 24;      // made it home
    return;
  }
  bool visible = f.fx >= 0 && f.fx <= 123;
  // on the road: is a car about to reach it where it stands?
  bool danger = road(f.row) && !frogSafe(f.row, f.fx, 0, 4);
  if (f.wait && !danger) {
    --f.wait;
    return;
  }
  auto hopTo = [&](uint8_t row, float fx) {
    f.row = row;
    f.fx = fx;
    f.hop = HOP;
    f.wait = random(1, 5);
    f.stuck = 0;
  };
  if (visible && f.row > 0 && frogSafe(f.row - 1, f.fx, HOP, HOP + 6)) {   // forward
    hopTo(f.row - 1, f.fx);
    return;
  }
  if (danger) {                                                         // back, or a step aside
    if (f.row < 8 && frogSafe(f.row + 1, f.fx, HOP, HOP + 6)) { hopTo(f.row + 1, f.fx); return; }
    for (float side : {-6.0f, 6.0f})
      if (frogSafe(f.row, f.fx + side, HOP, HOP + 6)) { hopTo(f.row, f.fx + side); return; }
  }
  // waiting a long while on safe ground or the road: try a step sideways, to line up with another gap
  if (!river(f.row) && ++f.stuck > 45) {
    float side = random(2) ? 6.0f : -6.0f;
    if (frogSafe(f.row, f.fx + side, HOP, HOP + 6)) hopTo(f.row, f.fx + side);
    f.stuck = 0;
  }
}

void IdleGames::frogDraw(const char* clock) {
  const Frogger& f = frog_;
  char buf[16];
  snprintf(buf, sizeof(buf), "FROG LV%u", f.level);
  text(buf, 1, 7.5f, false, G_DIM);
  text(clock, 127, 7.5f, false, G_TEXT, true);
  // the ground: grass at home, the river, the bank in the middle, the start
  box(0, rowY(0), 128, FROG_ROW_H, G_GRASS);
  box(0, rowY(1), 128, 3 * FROG_ROW_H, G_WATER);
  box(0, rowY(4), 128, FROG_ROW_H, G_GRASS);
  box(0, rowY(8), 128, FROG_ROW_H, G_GRASS);
  // road lane marks, and ripples on the river
  for (int r = 6; r <= 7; ++r)
    for (int x = static_cast<int>(frame_ % 8); x < 128; x += 8) box(x, rowY(r) - 0.3f, 3, 0.6f, G_DIM);
  for (int r = 1; r <= 3; ++r)
    for (int x = (r * 5 + static_cast<int>(frame_ / 3)) % 8; x < 128; x += 8) box(x, rowY(r) + 3, 1.4f, 0.6f, G_DIM);
  for (int r = 1; r <= 7; ++r) {
    const Lane& l = f.lanes[r];
    for (int i = 0; i < l.count; ++i) {
      float x = l.x[i];
      int y = rowY(r);
      if (river(r)) {                                 // a log, with a groove
        box(x, y + 1, l.len[i], 4, G_LOG);
        box(x + 3, y + 2.6f, l.len[i] - 6, 0.8f, G_WATER);
      } else {                                        // a car, with a window toward its front
        box(x, y + 1, l.len[i], 4, G_CAR);
        box(l.dir > 0 ? x + l.len[i] - 4 : x + 2, y + 2, 2, 1.2f, G_TEXT);
      }
    }
  }
  // the frog; mid-hop it's a pixel up; at home it blinks
  if (f.party && (f.party / 3) % 2) return;
  static const uint8_t FROG[5] = {0b01010, 0b11111, 0b01110, 0b11111, 0b10001};
  int fx = static_cast<int>(lroundf(f.fx)), fy = rowY(f.row) + (f.hop ? -1 : 0);
  for (int r = 0; r < 5; ++r)
    for (int c = 0; c < 5; ++c)
      if (FROG[r] >> (4 - c) & 1) pixel(fx + c, fy + r, G_FROG);
}


// ── Life ─────────────────────────────────────────────────────────────────────────────────────────────────────────
// Conway's Game of Life on a 64 x 32 board that wraps at the edges, about 7 generations a second. Cells take on
// colour as they age (white newborns, then yellow, amber, orange, and blue for the long-lived: still lifes and
// blinkers), so what's moving stands out. When the board dies out, freezes or only repeats, it holds a moment and
// starts again, taking turns: a random soup, the Gosper glider gun, the R-pentomino and the acorn.

namespace {
const char* const GUN[] = {
    "........................O...........",
    "......................O.O...........",
    "............OO......OO............OO",
    "...........O...O....OO............OO",
    "OO........O.....O...OO..............",
    "OO........O...O.OO....O.O...........",
    "..........O.....O.......O...........",
    "...........O...O....................",
    "............OO......................",
};
const char* const R_PENTOMINO[] = {".OO", "OO.", ".O."};
const char* const ACORN[] = {".O.....", "...O...", "OO..OOO"};
}  // namespace

void IdleGames::lifeSeed() {
  Life& L = life_;
  memset(L.age, 0, sizeof L.age);
  memset(L.seen, 0, sizeof L.seen);
  L.stale = L.gens = L.pause = 0;
  auto place = [&](const char* const* rows, int n, int x0, int y0) {
    for (int r = 0; r < n; ++r)
      for (int c = 0; rows[r][c]; ++c)
        if (rows[r][c] == 'O') L.age[(y0 + r + LH) % LH][(x0 + c + LW) % LW] = 1;
  };
  switch (L.seed++ % 5) {
    case 1: place(GUN, 9, 14, 11); break;
    case 2: place(R_PENTOMINO, 3, 31, 15); break;
    case 3: place(ACORN, 3, 29, 15); break;
    default:  // a soup: about a third of the cells, leaving a margin so it grows into the space
      for (int y = 4; y < LH - 4; ++y)
        for (int x = 8; x < LW - 8; ++x)
          if (random(100) < 34) L.age[y][x] = 1;
  }
}

void IdleGames::lifeStep() {
  Life& L = life_;
  if (frame_ % 2) return;  // a generation every other frame
  if (L.pause) {
    if (--L.pause == 0) lifeSeed();
    return;
  }
  static uint8_t next[LH][LW];
  uint32_t hash = 2166136261u;  // FNV-1a over the live cells
  int alive = 0;
  for (int y = 0; y < LH; ++y) {
    const uint8_t* up = L.age[(y + LH - 1) % LH];
    const uint8_t* mid = L.age[y];
    const uint8_t* dn = L.age[(y + 1) % LH];
    for (int x = 0; x < LW; ++x) {
      int l = (x + LW - 1) % LW, r = (x + 1) % LW;
      int n = (up[l] > 0) + (up[x] > 0) + (up[r] > 0) + (mid[l] > 0) + (mid[r] > 0) + (dn[l] > 0) + (dn[x] > 0) +
              (dn[r] > 0);
      bool live = mid[x] ? (n == 2 || n == 3) : n == 3;
      next[y][x] = live ? (mid[x] ? (mid[x] < 255 ? mid[x] + 1 : 255) : 1) : 0;
      if (live) {
        ++alive;
        hash = (hash ^ static_cast<uint32_t>(y * LW + x)) * 16777619u;
      }
    }
  }
  memcpy(L.age, next, sizeof L.age);
  ++L.gens;
  bool repeat = false;
  for (uint32_t h : L.seen) repeat |= h == hash;
  memmove(L.seen + 1, L.seen, sizeof L.seen - sizeof L.seen[0]);
  L.seen[0] = hash;
  L.stale = repeat ? L.stale + 1 : 0;
  // Dead, stuck in a short loop for ~4 s, or simply long enough: hold it ~2 s, then start again.
  if (alive == 0 || L.stale > 30 || L.gens > 1800) L.pause = 15;
}

void IdleGames::lifeDraw(const char* clock) {
  const Life& L = life_;
  const int px = static_cast<int>(2 * S);  // a cell: 5 screen pixels, drawn 4 with a gap
  for (int y = 0; y < LH; ++y)
    for (int x = 0; x < LW; ++x) {
      uint8_t a = L.age[y][x];
      if (!a) continue;
      uint8_t ink = a == 1 ? G_TEXT : a < 5 ? G_PIECE + 3 : a < 13 ? G_AMBER : a < 41 ? G_PIECE + 2 : G_PIECE + 1;
      gfx_.fillRect(x * px, y * px, px - 1, px - 1, ink);
    }
  // the time in a dark box in the corner
  float w = textWidth(clock, false);
  box(126 - w - 3, 0, w + 5, 9.5f, FaceGfx::BG);
  text(clock, 126, 7.5f, false, G_TEXT, true);
}
