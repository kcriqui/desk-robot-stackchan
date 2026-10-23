"""Dancing: four short routines for Rocky's neck, one picked at random when you ask him to dance.

Adapted from the original project's V2 (cgro00/desk-robot, brain/dance.py, MIT).

Each routine is a list of moves on a beat: where the head goes (pan, and tilt as an offset from his resting tilt,
config.TILT_REST: a StackChan can't go below level, so it dances around a little up), for how many beats, and a face
to switch to on the way. The player keeps time on the clock rather than adding up sleeps, so a slow WiFi message
doesn't drag the rhythm. The neck's own speed limits round the corners: a move it can't finish within its beat turns
into a smooth sweep.

    groove     head bobs on the beat while it sways side to side
    robot      stiff poses held for a beat, one axis at a time, deadpan until the end
    sprinkler  ticks across in little steps, then one fast sweep back; three times
    disco      points up one way and down the other, a shimmy, a big finish looking up

He waits until he has finished saying his line, keeps still otherwise (body language and face tracking stand aside),
and ends straight ahead at his resting tilt with a happy face.
"""

from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Awaitable, Callable

from . import config

# (pan, tilt offset, beats, face or None). Kept well inside the neck's range: the dance is a lot of quick moves.
Move = tuple[float, float, float, str | None]
PAN_MAX, TILT_DOWN, TILT_UP = 60.0, -20.0, 28.0
# Faces the routines use that this robot's face doesn't have yet, and what to show instead.
FACE_STAND_IN = {"excited": "happy", "love": "happy"}


def _groove() -> list[Move]:
    moves: list[Move] = []
    for i in range(16):                       # a nod down on the beat, back up on the "and"; a slow sway under it
        side = 18 * math.sin(i * math.pi / 4)
        face = "happy" if i == 0 else "excited" if i == 8 else None
        moves += [(side, -9, 0.5, face), (side, 3, 0.5, None)]
    return moves


def _robot() -> list[Move]:
    poses = [(-40, 0, 1), (-40, 15, 1), (0, 15, 0.5), (40, 15, 1), (40, -10, 1), (0, -10, 0.5), (0, 0, 1)]
    moves: list[Move] = [(0, 0, 1, "neutral")]
    for rep in range(2):
        moves += [(p, t, b, None) for p, t, b in poses]
        moves.append((0, 0, 1, None))         # freeze
    moves.append((0, 22, 1, "excited"))       # and the robot cracks a smile
    return moves


def _sprinkler() -> list[Move]:
    moves: list[Move] = [(45, 5, 1, "happy")]
    for rep in range(3):
        for i in range(1, 7):                 # tick, tick, tick across...
            moves.append((45 - i * 15, 5, 0.75, None))
        moves.append((45, 8, 2, "excited" if rep == 2 else None))   # ...and whoosh back
    return moves


def _disco() -> list[Move]:
    moves: list[Move] = [(0, 0, 0.5, "love")]
    for i in range(4):                        # point up to one side, down to the other
        moves += [(35, 22, 1.5, None), (-30, -12, 1.5, "excited" if i == 2 else None)]
    for i in range(8):                        # shimmy
        moves.append((7 if i % 2 else -7, 10, 0.25, None))
    moves.append((0, 26, 1.5, "excited"))     # big finish
    return moves


# name: (beats per minute, moves)
DANCES: dict[str, tuple[float, list[Move]]] = {
    "groove": (112, _groove()),
    "robot": (100, _robot()),
    "sprinkler": (110, _sprinkler()),
    "disco": (110, _disco()),
}


def length(name: str) -> float:
    """Seconds the routine takes."""
    bpm, moves = DANCES[name]
    return sum(b for _, _, b, _ in moves) * 60.0 / bpm


def tilt_range() -> tuple[float, float]:
    """The absolute tilt a dance may use: around his resting tilt, inside the neck's limits."""
    lo = max(config.TRACK_TILT_MIN, config.TILT_REST + TILT_DOWN)
    hi = min(config.TRACK_TILT_MAX, config.TILT_REST + TILT_UP)
    return lo, hi


def face(name: str) -> str:
    return name if name in config.EMOTIONS else FACE_STAND_IN.get(name, "happy")


class Dancer:
    def __init__(self, send: Callable[[dict], Awaitable[object]], tracker=None) -> None:
        self.send = send                      # main.send_to_robot
        self.tracker = tracker                # told where the head ends up
        self.ready: Callable[[], bool] = lambda: True             # main.py: his line has been said
        self.before: Callable[[], Awaitable[None]] | None = None  # main.py: body language / tracking stand aside
        self.tempo = 1.0                      # tests: > 1 plays faster
        self._task: asyncio.Task | None = None

    @property
    def dancing(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, name: str | None = None) -> str:
        """Start a routine (that one, or one at random). Returns its name."""
        if name not in DANCES:
            name = random.choice(list(DANCES))
        self.stop()
        self._task = asyncio.ensure_future(self._run(name))
        return name

    def stop(self) -> None:
        """Stop where he is (asleep, a look, his head moved from the console): nothing more is sent."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _go(self, pan: float, dtilt: float, last: list[float | None]) -> None:
        lo, hi = tilt_range()
        pan = round(max(-PAN_MAX, min(PAN_MAX, pan)), 1)
        tilt = round(max(lo, min(hi, config.TILT_REST + dtilt)), 1)
        if pan != last[0]:
            await self.send({"type": "pan", "deg": pan})
        if tilt != last[1]:
            await self.send({"type": "tilt", "deg": tilt})
        last[0], last[1] = pan, tilt

    async def _run(self, name: str) -> None:
        loop = asyncio.get_running_loop()
        await asyncio.sleep(0.5)                           # his voice starts a moment after the tool runs
        give_up = loop.time() + 10
        while not self.ready() and loop.time() < give_up:  # his "Dance time!" first
            await asyncio.sleep(0.1)
        if self.before is not None:
            await self.before()
        bpm, moves = DANCES[name]
        beat = 60.0 / bpm / self.tempo
        last: list[float | None] = [None, None]
        print(f"(dancing: {name}, {length(name) / self.tempo:.0f} s)")
        at = loop.time()
        for pan, dtilt, beats, f in moves:
            if f:
                await self.send({"type": "emotion", "name": face(f)})
            await self._go(pan, dtilt, last)
            at += beats * beat
            await asyncio.sleep(max(0.0, at - loop.time()))
        await self._go(0.0, 0.0, last)                     # straight ahead at rest, pleased with himself
        if self.tracker is not None:
            self.tracker.note_pose(pan=0.0, tilt=config.TILT_REST)
        await self.send({"type": "emotion", "name": "happy"})
