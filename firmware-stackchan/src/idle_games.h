#pragma once

#include <Arduino.h>

#include "face_gfx.h"

// Idle games: what the screen can show instead of the clock while the robot sleeps (on USB, by day). From the
// original project's V2 (cgro00/desk-robot firmware/src/idle_games.cpp, MIT), drawn in colour on the face's canvas
// (FaceGfx: 128 x 64 face pixels across the middle of the LCD) instead of a 1-bit OLED. Each one plays itself and
// never loses: the Pong paddles never miss, Tetris clears the board and starts over before it tops out, and the
// frog waits for gaps instead of getting hit. Ours adds Conway's Game of Life. Stepped and drawn about 15 times a
// second (main.cpp).
//
// The games borrow the canvas's inks for their colours (setInks); Face::restoreInks puts the face's back.

enum class IdleMode : uint8_t { Clock, Pong, Tetris, Frogger, Roam, Life };  // the `screen` message's numbers

class IdleGames {
 public:
  explicit IdleGames(FaceGfx& gfx) : gfx_(gfx) {}

  // A fresh game of `m` (Clock and Roam: nothing to do here).
  void reset(IdleMode m);
  IdleMode mode() const { return mode_; }
  void setInks();  // the games' colours, over the face's
  // One frame forward.
  void step();
  // The frame onto the canvas and the canvas onto the screen. `clock` is the time to show ("7:35 PM").
  void draw(const char* clock);

 private:
  // Drawing in face pixels (FaceGfx::SCALE screen pixels each).
  void box(float x, float y, float w, float h, uint8_t ink);
  void pixel(int x, int y, uint8_t ink) { box(x, y, 1, 1, ink); }
  void text(const char* s, float x, float baseline, bool big, uint8_t ink, bool right = false);
  float textWidth(const char* s, bool big);

  // ── Pong ──
  struct Pong {
    float bx = 64, by = 36, vx = 2.2f, vy = 1.0f;
    float pad[2] = {36, 36};   // paddle centres (left, right)
    float aim[2] = {0, 0};     // where on the paddle it means to hit (changes the bounce)
    uint16_t hits = 0;
  };
  void pongReset();
  void pongStep();
  void pongDraw(const char* clock);
  float pongPredict(int side) const;

  // ── Tetris ──
  struct Tetris {
    uint16_t rows[20] = {};    // 10 bits each, bit 0 = left column; row 0 is the top
    uint8_t color[20][10] = {};  // each settled cell's piece (its colour)
    uint8_t piece = 0, rot = 0, next = 0;
    int8_t x = 3, y = -1;
    uint8_t targetRot = 0;
    int8_t targetX = 3;
    uint8_t phase = 0;         // 0 moving into place, 1 dropping, 2 clearing lines, 3 wiping the board
    uint8_t timer = 0;
    uint32_t clearRows = 0;    // rows being cleared, bit per row
    uint16_t lines = 0;
    uint8_t bag[7] = {};
    uint8_t bagLeft = 0;
  };
  void tetrisReset();
  void tetrisStep();
  void tetrisDraw(const char* clock);
  void tetrisSpawn();
  uint8_t tetrisDraw7();
  bool tetrisCollides(const uint16_t* rows, uint8_t piece, uint8_t rot, int x, int y) const;
  void tetrisPlan();

  // ── Frogger ──
  struct Lane {
    int8_t dir = 1;
    float speed = 0.5f;
    float x[4] = {};           // object left edges, wrapping round SPAN
    uint8_t len[4] = {};
    uint8_t count = 0;
  };
  struct Frogger {
    Lane lanes[9];             // rows 1-3 river (logs), 5-7 road (cars); 0, 4, 8 are safe ground
    float fx = 60;             // frog's left edge
    uint8_t row = 8;           // 0 = home at the top, 8 = the start
    uint8_t hop = 0;           // frames left of a hop (drawn mid-jump)
    uint8_t wait = 0;          // frames before the next decision
    uint8_t party = 0;         // frames left of the celebration at the top
    uint8_t level = 1;
    uint16_t stuck = 0;        // frames without a move
  };
  void frogReset(bool keepLevel);
  void frogStep();
  void frogDraw(const char* clock);
  bool frogSafe(uint8_t row, float fx, int fromT, int toT) const;
  float laneX(const Lane& l, int i, int t) const;

  // ── Life ──
  static constexpr int LW = 64, LH = 32;   // cells of 2 x 2 face pixels, wrapping at the edges
  struct Life {
    uint8_t age[LH][LW] = {};  // 0 dead, else generations alive (255 at most)
    uint32_t seen[6] = {};     // the last few generations' hashes: a repeat means it's stuck
    uint16_t stale = 0, gens = 0;
    uint8_t seed = 0;          // which start is next
    uint8_t pause = 0;         // generations to hold the end before the next start
  };
  void lifeSeed();
  void lifeStep();
  void lifeDraw(const char* clock);

  FaceGfx& gfx_;
  IdleMode mode_ = IdleMode::Clock;
  uint32_t frame_ = 0;
  Pong pong_;
  Tetris tet_;
  Frogger frog_;
  Life life_;
};
