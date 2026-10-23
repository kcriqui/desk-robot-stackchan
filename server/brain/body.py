"""Body language: small, quick head moves that go with what Rocky feels while he talks.

Adapted from the original project's V2 (cgro00/desk-robot, brain/body.py, MIT).

When his voice starts, the reply's emotion plays a short gesture (a happy bounce, a surprised pull-back, a sad
droop), then he settles into that emotion's posture and sways a little around it, a small nod or drift every few
seconds, until he stops talking and goes back to where he was. While a reply is being worked out he glances up and to
the side, as if thinking. Awake and waiting for you, he idles attentively: every few seconds a drift, a nod, back to
the middle, or now and then a proper look around (idle_loop), but never while you're talking (the servos' hum
would reach the microphone).

Everything is an offset from the head's resting pose (the tracker's pan / tilt: where he was last told to look), so
he always comes back to it, and it's clamped to the neck's range (a StackChan can't tilt below level; it rests a
little up, config.TILT_REST). The servos let go once the head is still (SERVO_AUTO_RELAX), so the sway leaves them a
while between moves.

He holds still:
  - asleep, or with the sleepy face (allowed() says no, or the emotion has no sway);
  - while face tracking is on or he's dancing: they own the head then;
  - while a `look` is turning the head and taking its picture (hold()), and for the rest of that answer: he turned to
    look at something, so he keeps looking at it while he talks about it;
  - when someone moves the head from the console, or he falls asleep (stop()).
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Awaitable, Callable

from . import config

# Offsets in head degrees (pan + = Rocky's right, tilt + = up) and how long to hold each before the next. The original
# body's face lacks the last five emotions (curious ... confused); a StackChan's has them.
GESTURES: dict[str, list[tuple[float, float, float]]] = {
    "happy":     [(0, 5, 0.18), (0, 0, 0.14), (0, 4, 0.16)],                      # a little bounce
    "excited":   [(-5, 6, 0.16), (5, 1, 0.16), (-4, 6, 0.16), (4, 1, 0.16)],     # bouncy wiggle
    "surprised": [(0, 8, 0.6)],                                                    # pulls back
    "sad":       [(0, -8, 0.8)],                                                   # droops
    "curious":   [(7, 4, 0.7)],                                                    # leans in to one side
    "skeptical": [(-6, -3, 0.8)],                                                  # side-eye
    "confused":  [(6, 2, 0.35), (-6, 2, 0.35)],                                    # looks one way, the other
    "angry":     [(-4, -3, 0.12), (4, -3, 0.12), (-4, -3, 0.12), (4, -3, 0.12)],  # quick shake
    "love":      [(-5, 3, 0.45), (5, 3, 0.45)],                                    # slow sway
}
# Where he settles while talking, and sways around (neutral: straight).
POSTURE: dict[str, tuple[float, float]] = {
    "sad": (0, -6), "curious": (5, 0), "skeptical": (-4, -2), "angry": (0, -2), "thinking": (4, 3),
}
# Sway while talking: (size, pause between moves) relative to neutral. None = no sway.
STYLE: dict[str, tuple[float, float] | None] = {
    "excited": (1.4, 0.6), "happy": (1.2, 0.8), "love": (0.9, 1.3), "sad": (0.5, 1.6), "angry": (1.1, 0.8),
    "thinking": (0.7, 1.3), "sleepy": None,
}
PONDER = (6.0, 5.0)        # glance up and to the side while working out a reply
IDLE_EVERY = (4.0, 9.0)    # s between idle moves while he waits for you
IDLE_DRIFT = (9.0, 4.5)    # deg at most, pan / tilt, around where he rests
IDLE_LOOK = (18.0, 30.0)   # deg of pan for the occasional big look around (with -6 .. +10 of tilt)
# How often each idle move comes up: back to the middle, a nod, a big look; the rest are drifts.
IDLE_CHANCES = (0.2, 0.2, 0.12)
SWAY_NOD = 2.5             # deg down and back
SWAY_DRIFT = 3.0           # deg either side of the posture
SWAY_EVERY = (2.2, 4.0)    # s between sway moves (times the emotion's pace)
SWAY_REST = 1.9            # s at least between them, so the servos get to let go


class Body:
    def __init__(self, send: Callable[[dict], Awaitable[object]], tracker=None) -> None:
        self.send = send                     # main.send_to_robot
        self.tracker = tracker               # its pan / tilt is the resting pose (None: straight ahead, at rest)
        self.allowed: Callable[[], bool] = lambda: True   # main.py: awake, robot connected, not tracking or dancing
        self.hush: Callable[[], bool] = lambda: False     # main.py: someone is talking (idle moves wait)
        self.talking = False
        self._emotion = "neutral"
        self._task: asyncio.Task | None = None
        self._holds = 0
        self._sent: tuple[float, float] | None = None   # the last pose we sent (None: the head is at rest)
        self._looked = False                 # a look turned the head this turn: keep still until he's done talking
        self._idle_off = (0.0, 0.0)          # where an idle drift left him, as an offset from rest

    # ── what main.py calls ──────────────────────────────────────────────────

    def ponder(self) -> None:
        """A reply is being worked out (the question's picture is already in hand). A new turn."""
        if self.talking:
            return
        self._looked = False
        self._start(self._ponder())

    def speaking(self, emotion: str) -> None:
        """His voice just started: this emotion's gesture, then sway until quiet()."""
        self.talking, self._emotion = True, emotion
        if not self._looked:
            self._start(self._perform(emotion, gesture=True))

    def feel(self, emotion: str) -> None:
        """His face changed mid-reply: play the new emotion's gesture and sway its way from now on."""
        if not self.talking or emotion == self._emotion:
            return
        self._emotion = emotion
        if not self._holds and not self._looked:
            self._start(self._perform(emotion, gesture=True))

    def quiet(self) -> None:
        """Done talking (or the reply was dropped): back to rest."""
        self.talking = False
        self._looked = False
        if self._sent is not None and not self._holds:
            self._start(self._rest())
        else:
            self._cancel()

    def stop(self) -> None:
        """Someone else is moving the head (the console, tracking, a dance, sleep): stop at once, send nothing more."""
        self.talking = False
        self._cancel()
        self._sent = None
        self._idle_off = (0.0, 0.0)

    @contextlib.asynccontextmanager
    async def hold(self):
        """Keep still while a look turns the head and takes its picture, and until he has finished talking about it.
        The look sets the new rest pose, but only on the axes it turns, so first take our offset off both."""
        self._cancel()
        if self._sent is not None:
            await self._go(0.0, 0.0, home=True)
        self._holds += 1
        self._looked = True
        self._sent = None
        try:
            yield
        finally:
            self._holds -= 1

    async def idle_loop(self) -> None:
        """Forever: while he's awake and waiting (not talking, thinking, looking or listening to you), a small move
        every few seconds. main.py runs it."""
        while True:
            await asyncio.sleep(random.uniform(*IDLE_EVERY))
            busy = self.talking or self._holds or (self._task is not None and not self._task.done())
            if busy or self.hush() or not self._ok():
                continue
            self._start(self._idle_move())

    def test(self, emotion: str) -> None:
        """Play one emotion's gesture and a few seconds of its sway (the console's `gesture`), then rest."""
        async def run():
            self.speaking(emotion)
            await asyncio.sleep(3.0)
            if self._emotion == emotion:
                self.quiet()
        asyncio.ensure_future(run())

    # ── moves ───────────────────────────────────────────────────────────────

    def _start(self, coro) -> None:
        self._cancel()
        self._task = asyncio.ensure_future(coro)

    def _cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    def _ok(self) -> bool:
        return config.BODY_LANGUAGE and not self._holds and self.allowed()

    def _rest_pose(self) -> tuple[float, float]:
        t = self.tracker
        return (t.pan, t.tilt) if t is not None else (0.0, config.TILT_REST)

    async def _go(self, dpan: float, dtilt: float, home: bool = False) -> bool:
        """Head to rest + offset. False (nothing sent) when he should hold still. home: the way back to rest, which
        only a look's hold stops (asleep or tracking, stop() has already forgotten the offset; switched off mid-move,
        he should still come back)."""
        if self._holds or not (home or self._ok()):
            return False
        rp, rt = self._rest_pose()
        pan = round(max(-config.TRACK_PAN_LIMIT, min(config.TRACK_PAN_LIMIT, rp + dpan)), 1)
        tilt = round(max(config.TRACK_TILT_MIN, min(config.TRACK_TILT_MAX, rt + dtilt)), 1)
        last = self._sent or (round(rp, 1), round(rt, 1))
        if pan != last[0]:
            await self.send({"type": "pan", "deg": pan})
        if tilt != last[1]:
            await self.send({"type": "tilt", "deg": tilt})
        self._sent = (pan, tilt) if (dpan or dtilt) else None
        return True

    async def _rest(self) -> None:
        if self._sent is not None:
            await self._go(0.0, 0.0, home=True)
        self._sent = None
        self._idle_off = (0.0, 0.0)

    async def _ponder(self) -> None:
        side = random.choice((-1, 1))
        await asyncio.sleep(0.25)
        await self._go(side * PONDER[0], PONDER[1])

    async def _idle_move(self) -> None:
        middle, nod, look = IDLE_CHANCES
        r = random.random()
        if r < middle:                            # back to the middle
            await self._rest()
        elif r < middle + nod:                    # a small nod, then back up
            dp, dt = self._idle_off
            if await self._go(dp, dt - 3.0):
                await asyncio.sleep(0.3)
                await self._go(dp, dt)
        elif r < middle + nod + look:             # a proper look around: something caught his eye
            self._idle_off = (random.choice((-1, 1)) * random.uniform(*IDLE_LOOK), random.uniform(-6.0, 10.0))
            await self._go(*self._idle_off)
        else:                                     # drift a little: look at something nearby
            self._idle_off = (random.uniform(-1, 1) * IDLE_DRIFT[0], random.uniform(-0.5, 1) * IDLE_DRIFT[1])
            await self._go(*self._idle_off)

    async def _perform(self, emotion: str, gesture: bool) -> None:
        if gesture:
            for dpan, dtilt, wait in GESTURES.get(emotion, []):
                if not await self._go(dpan, dtilt):
                    return
                await asyncio.sleep(wait)
        posture = POSTURE.get(emotion, (0.0, 0.0))
        style = STYLE.get(emotion, (1.0, 1.0))
        if not await self._go(*posture) or style is None:
            return
        size, pace = style
        drift = 0.0
        while self.talking:
            await asyncio.sleep(max(SWAY_REST, random.uniform(*SWAY_EVERY) * pace))
            if not self.talking:
                break
            if random.random() < 0.45:   # a small nod, then back up
                if not await self._go(posture[0] + drift, posture[1] - SWAY_NOD * size):
                    return
                await asyncio.sleep(0.22)
                if not await self._go(posture[0] + drift, posture[1]):
                    return
            else:                        # drift a little to one side or the other
                drift = random.choice((-1, 1)) * SWAY_DRIFT * size * random.uniform(0.5, 1.0)
                if not await self._go(posture[0] + drift, posture[1]):
                    return
        await self._rest()
