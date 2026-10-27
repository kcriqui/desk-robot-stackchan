"""The echo canceller (brain/echo.py) on synthetic audio: a speech-like reference through a made-up room."""

import unittest

import numpy as np

from brain.echo import EchoCanceller

RATE = 16000


def speechy(seconds: float, seed: int) -> np.ndarray:
    """Noise shaped a little like speech: bursts of low-passed noise, 0.2-0.4 s on, 0.1 s off."""
    rng = np.random.default_rng(seed)
    x = np.convolve(rng.standard_normal(int(seconds * RATE)), np.ones(4) / 4, mode="same") * 0.2
    t, on = 0, True
    while t < len(x):
        n = int(rng.uniform(0.2, 0.4) * RATE) if on else int(0.1 * RATE)
        if not on:
            x[t:t + n] = 0
        t, on = t + n, not on
    return x


def room(x: np.ndarray, seed: int = 1) -> np.ndarray:
    """The speaker-to-mic path: a direct sound, a few reflections, a decaying tail (80 ms)."""
    rng = np.random.default_rng(seed)
    h = rng.standard_normal(1280) * np.exp(-np.arange(1280) / 250) * 0.05
    h[6] += 0.8
    h[40] += 0.3
    return np.convolve(x, h)[:len(x)]


def run(mic: np.ndarray, ref: np.ndarray) -> tuple[np.ndarray, list[float]]:
    aec = EchoCanceller()
    out, near = [], []
    for k in range(0, len(mic) - 479, 480):
        out.append(aec.process(mic[k:k + 480], ref[k:k + 480]))
        near.append(aec.near)
    return np.concatenate(out), near


def db(x: np.ndarray) -> float:
    return 10 * np.log10(np.mean(x * x) + 1e-15)


class EchoTests(unittest.TestCase):
    def test_takes_the_echo_out(self):
        ref = speechy(8, 2)
        mic = room(ref) + np.random.default_rng(3).standard_normal(len(ref)) * 1e-4
        out, near = run(mic, ref)
        settled = slice(5 * RATE, 8 * RATE - 480)
        self.assertGreater(db(mic[settled]) - db(out[settled]), 25)
        # Echo alone, once settled, mostly isn't taken for someone talking (ECHO_NEAR_DB 6). Single blocks after a
        # gap still reach ~10 dB here, where the canceller gets the echo below the noise: to tune on the robot.
        self.assertLess(np.median([n for n in near[-60:] if n < 90]), 3)

    def test_keeps_you_when_you_talk_over_it(self):
        ref = speechy(10, 4)
        you = np.zeros_like(ref)
        you[7 * RATE:9 * RATE] = speechy(2, 5) * 0.15  # 2 s of you, ~16 dB under the echo, once it has settled
        mic = room(ref) + you
        out, near = run(mic, ref)
        late = slice(7 * RATE + 240, 9 * RATE + 240)  # the output is a block (240) late
        kept = db(out[late]) - db(you[7 * RATE:9 * RATE])
        self.assertGreater(kept, -6)  # most of you is still there
        talking = [n for n in near[7 * RATE // 480 + 2:9 * RATE // 480] if n < 90]
        self.assertGreater(np.median(talking), 6)  # and it's heard as someone talking

    def test_passes_audio_through_without_a_reference(self):
        x = speechy(1, 6)
        out, _ = run(x, np.zeros_like(x))
        np.testing.assert_allclose(out[240:], x[:len(out) - 240], atol=1e-4)


if __name__ == "__main__":
    unittest.main()
