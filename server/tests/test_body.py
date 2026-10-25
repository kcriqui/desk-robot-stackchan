"""Body language and dancing (brain/body.py, brain/dance.py): the moves they send, that he always comes back, that
they stay inside a StackChan's neck, and when he must keep still. Adapted from the original project's V2 tests."""

import asyncio
import unittest
from unittest.mock import patch

from brain import body as body_mod, config, dance
from brain.body import Body
from brain.dance import DANCES, Dancer


class FakeTracker:
    def __init__(self, pan=0.0, tilt=20.0):
        self.pan, self.tilt = pan, tilt
        self.noted = None

    def note_pose(self, pan=None, tilt=None):
        self.noted = (pan, tilt)


class Robot:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, msg):
        self.sent.append(msg)

    def moves(self):
        return [(m["type"], m["deg"]) for m in self.sent if m["type"] in ("pan", "tilt")]

    def last(self, axis):
        return next((d for a, d in reversed(self.moves()) if a == axis), None)


def run(coro):
    return asyncio.run(coro)


# A StackChan's neck (BODY=stackchan in server/.env), whatever the .env says.
STACKCHAN_NECK = {"TRACK_TILT_MIN": 5.0, "TRACK_TILT_MAX": 85.0, "TILT_REST": 20.0, "TRACK_PAN_LIMIT": 120.0}


def stackchan_neck(cls):
    for name, value in STACKCHAN_NECK.items():
        cls = patch.object(config, name, value)(cls)
    return cls


@stackchan_neck
@patch.object(body_mod, "SWAY_EVERY", (0.05, 0.1))
@patch.object(body_mod, "SWAY_REST", 0.05)
@patch.object(config, "BODY_LANGUAGE", True)
class BodyTests(unittest.TestCase):
    def make(self, pan=0.0, tilt=20.0):
        robot, tracker = Robot(), FakeTracker(pan, tilt)
        return robot, tracker, Body(robot.send, tracker)

    def test_talks_then_comes_back_to_rest(self):
        robot, _, b = self.make(pan=20.0, tilt=20.0)

        async def go():
            b.speaking("happy")
            await asyncio.sleep(1.2)
            b.quiet()
            await asyncio.sleep(0.2)
        run(go())
        self.assertGreater(len(robot.moves()), 3, "the bounce and some sway")
        self.assertIn(robot.last("pan"), (None, 20.0))
        self.assertEqual(robot.last("tilt"), 20.0)
        self.assertIsNone(b._sent)
        for axis, deg in robot.moves():   # small moves only, around where he was
            rest = 20.0
            self.assertLessEqual(abs(deg - rest), 10.0, (axis, deg))

    def test_never_below_level(self):
        robot, _, b = self.make(tilt=0.0)   # resting level: a sad droop has nowhere to go

        async def go():
            b.speaking("sad")
            await asyncio.sleep(0.6)
            b.quiet()
            await asyncio.sleep(0.1)
        run(go())
        self.assertTrue(all(d >= config.TRACK_TILT_MIN for a, d in robot.moves() if a == "tilt"))

    def test_still_when_not_allowed(self):
        robot, _, b = self.make()
        b.allowed = lambda: False   # asleep, tracking, dancing or no robot

        async def go():
            b.ponder()
            await asyncio.sleep(0.4)
            b.speaking("happy")
            await asyncio.sleep(0.6)
            b.quiet()
            await asyncio.sleep(0.1)
        run(go())
        self.assertEqual(robot.sent, [])

    def test_switched_off_sends_nothing(self):
        robot, _, b = self.make()

        async def go():
            with patch.object(config, "BODY_LANGUAGE", False):
                b.speaking("happy")
                await asyncio.sleep(0.5)
                b.quiet()
                await asyncio.sleep(0.1)
        run(go())
        self.assertEqual(robot.sent, [])

    def test_a_look_holds_him_still_for_the_rest_of_the_answer(self):
        robot, _, b = self.make()

        async def go():
            b.ponder()
            await asyncio.sleep(0.4)            # glanced up and aside
            async with b.hold():
                held = len(robot.moves())
                await asyncio.sleep(0.3)
                self.assertEqual(len(robot.moves()), held, "nothing while the look runs")
            b.speaking("happy")                 # talking about what he saw
            await asyncio.sleep(0.5)
            self.assertEqual(len(robot.moves()), held, "and nothing while he talks about it")
            b.quiet()
        run(go())
        self.assertEqual(robot.moves()[-2:], [("pan", 0.0), ("tilt", 20.0)], "the glance was taken back first")

    def test_idles_while_waiting_but_not_while_you_talk(self):
        robot, _, b = self.make()
        talking = [False]
        b.hush = lambda: talking[0]

        async def go():
            with patch.object(body_mod, "IDLE_EVERY", (0.02, 0.04)):
                loop = asyncio.ensure_future(b.idle_loop())
                await asyncio.sleep(0.6)
                moved = len(robot.moves())
                talking[0] = True                 # you start talking
                await asyncio.sleep(0.6)
                during = len(robot.moves()) - moved
                loop.cancel()
                return moved, during
        moved, during = run(go())
        self.assertGreater(moved, 2, "small moves while he waits")
        self.assertLessEqual(during, 2, "at most the move already under way")
        for axis, deg in robot.moves():   # drifts and the odd big look, around where he rests
            if axis == "pan":
                self.assertLessEqual(abs(deg), body_mod.IDLE_LOOK[1], deg)
            else:
                self.assertTrue(20.0 - 6.0 <= deg <= 20.0 + 10.0, deg)

    def test_stop_forgets_the_offset(self):
        robot, _, b = self.make()

        async def go():
            b.speaking("happy")
            await asyncio.sleep(0.3)
            b.stop()                            # tracking / sleep / the console took the head
            sent = len(robot.sent)
            b.quiet()
            await asyncio.sleep(0.2)
            self.assertEqual(len(robot.sent), sent)
        run(go())


def play(name=None, tempo=40.0):
    robot = Robot()
    d = Dancer(robot.send, FakeTracker())
    d.tempo = tempo                           # a 15 s routine in well under a second

    async def go():
        with patch.object(asyncio, "sleep", _fast_sleep):
            picked = d.start(name)
            while d.dancing:
                await _REAL_SLEEP(0.01)
        return picked, d
    picked, d = asyncio.run(go())
    return picked, robot.sent, d.tracker


_REAL_SLEEP = asyncio.sleep


async def _fast_sleep(t):
    await _REAL_SLEEP(min(t, 0.01))   # the 0.5 s lead-in wait too


@stackchan_neck
class DanceTests(unittest.TestCase):
    def test_four_routines_of_about_fifteen_seconds(self):
        self.assertEqual(sorted(DANCES), ["disco", "groove", "robot", "sprinkler"])
        for name in DANCES:
            self.assertTrue(8 <= dance.length(name) <= 20, (name, dance.length(name)))

    def test_each_stays_in_range_and_ends_at_rest_and_happy(self):
        lo, hi = dance.tilt_range()
        self.assertGreaterEqual(lo, config.TRACK_TILT_MIN)
        for name in DANCES:
            picked, sent, tracker = play(name)
            self.assertEqual(picked, name)
            moves = [m for m in sent if m["type"] in ("pan", "tilt")]
            self.assertGreater(len(moves), 10, name)
            for m in moves:
                a, b = (-dance.PAN_MAX, dance.PAN_MAX) if m["type"] == "pan" else (lo, hi)
                self.assertTrue(a <= m["deg"] <= b, (name, m))
            last = {m["type"]: m["deg"] for m in moves}
            self.assertEqual((last["pan"], last["tilt"]), (0.0, config.TILT_REST), name)
            self.assertEqual(sent[-2:], [{"type": "emotion", "name": "happy"},
                                         {"type": "leds", "dance": False}], name)   # pleased, then the lights calm down
            self.assertIn({"type": "leds", "dance": True}, sent[:3], name)       # the light show starts with it
            self.assertEqual(tracker.noted, (0.0, config.TILT_REST))
            # only faces this robot has
            faces = {m["name"] for m in sent if m["type"] == "emotion"}
            self.assertLessEqual(faces, set(config.EMOTIONS), name)


if __name__ == "__main__":
    unittest.main()
