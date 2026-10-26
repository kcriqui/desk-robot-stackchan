"""Tracker: find a face in each camera frame and steer the head toward it.

Runs in its own thread (OpenCV is blocking). For every new frame from Eyes
it detects faces (YuNet, ~3 ms at 320x240), picks the biggest, works out how
far off-center it is, and nudges the pan/tilt estimate toward it. The
proposed head pose goes to the on_pose callback; main.py sends it to the robot.

The camera rides on the head, so this is a simple servo loop: an error of
x% of the half-width means the face is x% of half the field of view off
axis. We move a fraction (TRACK_GAIN) of that per frame so the head glides
rather than jerks, and ignore tiny errors (TRACK_DEADBAND).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from . import config
from .eyes import Eyes

MODEL = Path(__file__).resolve().parent.parent / "models" / "face_detection_yunet_2023mar.onnx"


class Tracker:
    def __init__(self, eyes: Eyes, on_pose: Callable[[float | None, float | None, bool], None]) -> None:
        """on_pose(pan, tilt, tracking) is called from the tracker thread
        whenever the head should move (None = leave that axis alone)."""
        self.eyes = eyes
        self.on_pose = on_pose
        self.pan = 0.0     # our estimate of where the head is (deg)
        self.tilt = config.TILT_REST
        self.tracking = False
        self.last_seen = 0.0
        self.enabled = config.TRACKING
        self._det = None
        self._size = (0, 0)
        self._last_sent = 0.0
        self._lost_reported = True

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    # Called from main.py when it moves the head itself, so we stay in sync.
    def note_pose(self, pan: float | None = None, tilt: float | None = None) -> None:
        if pan is not None:
            self.pan = pan
        if tilt is not None:
            self.tilt = tilt

    def _detector(self, w: int, h: int):
        if self._det is None or self._size != (w, h):
            self._det = cv2.FaceDetectorYN.create(str(MODEL), "", (w, h), score_threshold=0.6)
            self._size = (w, h)
        return self._det

    def _run(self) -> None:
        seq = -1
        while True:
            seq = self.eyes.wait_for_new(seq, timeout=1.0)
            jpeg, pose = self.eyes.frame_and_pose()
            if not jpeg:
                continue
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            h, w = img.shape[:2]
            face = None
            if self.enabled:
                _, faces = self._detector(w, h).detect(img)
                if faces is not None and len(faces):
                    best = max(faces, key=lambda f: f[2] * f[3])
                    face = tuple(int(v) for v in best[:4])
            now = time.time()

            if face is not None:
                self.last_seen = now
                self._lost_reported = False
                self._steer(face, w, h, now, pose)
            elif self.tracking and now - self.last_seen > config.TRACK_LOST_SECONDS:
                # Lost them: hand the head back to its idle glances.
                self.tracking = False
                if not self._lost_reported:
                    self._lost_reported = True
                    self.on_pose(None, None, False)

            self.eyes.publish(self._annotate(img, face, w, h))

    def _steer(self, face: tuple[int, int, int, int], w: int, h: int, now: float,
               pose: tuple[float, float] | None = None) -> None:
        """pose: where the head pointed when this frame was taken, if the robot
        says (0x03 frames). Then aim from there: the target can't pile up while
        the head is still moving. Without it, nudge our estimate by TRACK_GAIN."""
        x, y, fw, fh = face
        ex = ((x + fw / 2) - w / 2) / (w / 2)   # -1 (left edge) .. +1 (right edge)
        ey = ((y + fh / 2) - h / 2) / (h / 2)   # -1 (top) .. +1 (bottom)
        moved = False
        if abs(ex) > config.TRACK_DEADBAND:
            step = config.TRACK_PAN_SIGN * ex * (config.TRACK_HFOV / 2)
            if pose is not None:
                self.pan = pose[0] + step * config.TRACK_POSE_GAIN
            else:
                self.pan += step * config.TRACK_GAIN
            self.pan = max(-config.TRACK_PAN_LIMIT, min(config.TRACK_PAN_LIMIT, self.pan))
            moved = True
        if abs(ey) > config.TRACK_DEADBAND:
            # Face above center → look up (tilt toward its max); below → look down.
            step = config.TRACK_TILT_SIGN * (-ey) * (config.TRACK_VFOV / 2)
            if pose is not None:
                self.tilt = pose[1] + step * config.TRACK_POSE_GAIN
            else:
                self.tilt += step * config.TRACK_GAIN
            self.tilt = max(config.TRACK_TILT_MIN, min(config.TRACK_TILT_MAX, self.tilt))
            moved = True
        became_tracking = not self.tracking
        self.tracking = True
        if (moved or became_tracking) and now - self._last_sent > 0.12:
            self._last_sent = now
            self.on_pose(round(self.pan, 1), round(self.tilt, 1), True)

    def _annotate(self, img, face, w: int, h: int) -> bytes:
        cv2.drawMarker(img, (w // 2, h // 2), (90, 90, 90), cv2.MARKER_CROSS, 12, 1)
        if face is not None:
            x, y, fw, fh = face
            cv2.rectangle(img, (x, y), (x + fw, y + fh), (80, 220, 120), 2)
        elif self.enabled:
            cv2.putText(img, "no face", (6, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (120, 120, 200), 1)
        ok, out = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return out.tobytes() if ok else b""
