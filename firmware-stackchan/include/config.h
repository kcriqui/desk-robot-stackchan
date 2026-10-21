#pragma once

#include <stdint.h>

// desk-robot firmware for the M5Stack StackChan (CoreS3 + serial-bus servo
// body). Everything here is the StackChan counterpart of firmware/include/
// config.h. The pins are fixed by the board, so there's no pin map: the
// StackChan BSP and M5Unified know where everything is.

// ─── Head ────────────────────────────────────────────────────────────────────
// Angles are "head degrees", the same units the brain sends (docs/protocol.md):
// pan 0 = straight ahead, negative = robot's left; tilt 0 = level, positive = up.
//
// The StackChan's neck is the opposite of the original build's: it can't tilt
// below level, only UP (pitch 0-90 deg), where the original only nods down.
// It sits on a desk below its human's eyes, so it rests looking a little up
// and can still drop to level to see the desk. Keep the server's
// BODY=stackchan limits (server/brain/config.py) in step with these.
constexpr float PAN_MIN_DEG = -120.0f;  // the BSP stops yaw at +/-128 (position
constexpr float PAN_MAX_DEG = 120.0f;   // control ends there); keep a margin
constexpr float TILT_MIN_DEG = 0.0f;    // level: the pitch servo's lower stop
constexpr float TILT_MAX_DEG = 90.0f;   // straight up at the ceiling
constexpr float TILT_REST_DEG = 20.0f;  // boot pose and `center`

// Flip if `pan -30` turns the head to ITS right instead of its left, or if
// `tilt 20` looks down. Both defaults checked on a StackChan (2026-10-17).
constexpr bool PAN_INVERT = false;
constexpr bool TILT_INVERT = false;

// Motion speed, 0-1000 (the BSP's spring speed). 500 is its default; higher
// snaps, lower glides.
constexpr int PAN_SPEED = 450;
constexpr int TILT_SPEED = 350;

// Release servo torque once the head is at rest, like the original build's
// detach: quiet, cool and low-power. If the head sags when looking up, set
// this to false.
constexpr bool SERVO_AUTO_RELAX = true;

// Idle glance range, degrees either side of where the head was put.
constexpr float PAN_GLANCE_DEG = 25.0f;
constexpr float TILT_GLANCE_DEG = 8.0f;

// ─── Microphone ──────────────────────────────────────────────────────────────
// Software gain on top of M5Unified's own (mic_config_t::magnification).
// Raise if the brain says "too quiet", lower if loud speech clips.
constexpr float MIC_GAIN = 1.0f;

// ─── Camera ──────────────────────────────────────────────────────────────────
// The CoreS3's GC0308 has no hardware JPEG, so frames are compressed in
// software. That costs CPU, so the stream is capped below what the brain asks.
constexpr float CAMERA_MAX_FPS = 8.0f;
constexpr int CAMERA_JPEG_QUALITY = 60;  // 1-100, higher = better (~8-15 KB QVGA)
constexpr bool CAMERA_VFLIP = false;
constexpr bool CAMERA_HMIRROR = false;

// ─── Speaker ─────────────────────────────────────────────────────────────────
constexpr float SPEAKER_VOLUME = 0.7f;  // 0.0-1.0

// ─── Face ────────────────────────────────────────────────────────────────────
constexpr uint32_t FACE_COLOR = 0xFFFFFF;        // RGB888
constexpr uint32_t BACKGROUND_COLOR = 0x000000;

// ─── Behavior ────────────────────────────────────────────────────────────────
constexpr uint32_t FRAME_INTERVAL_MS = 33;  // ~30 FPS face animation
