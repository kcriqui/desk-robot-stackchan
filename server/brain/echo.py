"""Echo: take the robot's own voice out of what its mic hears, so it can listen while it talks.

The robot (firmware-stackchan 0.6.9.41+, duplex) sends 0x04 frames while its speaker plays: the mic and the echo
reference, the speaker amp's output wired back into a spare ADC channel, recorded in the same instant. So there's no
delay to find and no clock drift; what's left is the room: the path from speaker to mic, ~100 ms long.

Two stages, as in Speex's echo canceller (mdf.c) and WebRTC's AEC:
1. A linear filter learns that path and subtracts the echo it predicts: a partitioned-block frequency-domain NLMS
   (overlap-save, 15 ms blocks, 8 partitions = 120 ms). Two copies of it: the background one always adapts, the
   foreground one makes the output and only takes the background's weights when they cancel better. When you talk
   over the robot the background filter goes astray (your voice looks like echo it failed to remove) and is put back
   from the foreground, so the output never falls apart.
2. What the linear filter misses (the little speaker's distortion, a path still changing) is pushed down per
   frequency: a residual-echo suppressor that estimates how much echo is left from the predicted echo and how much
   of it has been leaking through, and attenuates the bins where it dominates.

`near` says how much of the output isn't echo, per block: while the speaker plays, how far the output stands above
the mic level it usually brings the echo down to (dB). Echo alone sits near 0; you talking over it rise well above.
With the speaker quiet it's high (nothing to take out). The ears use it to keep what's left of the robot's voice
from opening a turn of its own, and to hear you talk over it.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from . import config

BLOCK = 240        # 15 ms at 16 kHz
PARTITIONS = 8     # x BLOCK = 1920 taps: 120 ms of echo path
FLOOR = 1e-10


class EchoCanceller:
    def __init__(self, block: int = BLOCK, partitions: int = PARTITIONS) -> None:
        self.N, self.P = block, partitions
        self.K = block + 1  # rfft bins of a 2N transform
        self.window = np.sqrt(np.hanning(2 * block + 1)[:-1])  # periodic sqrt-Hann: overlap-adds to 1
        self.reset(full=True)

    def reset(self, full: bool = False) -> None:
        """Forget the reference's history (after a gap in the audio). full: the learned echo path too."""
        N, P, K = self.N, self.P, self.K
        if full:
            self.Wf = np.zeros((P, K), dtype=complex)  # foreground filter: makes the output
            self.Wb = np.zeros((P, K), dtype=complex)  # background filter: always adapting
            self.leak = 0.1                            # how much of the predicted echo gets through (power)
            self.Pey = 0.0
            self.Pyy = 1.0
            self.Eh = np.zeros(K)                      # smoothed spectra for the leak estimate
            self.Yh = np.zeros(K)
            self._davg1 = self._davg2 = self._dvar1 = self._dvar2 = 0.0
            self.down = -30.0                          # output vs echo level (dB) with only echo: learnt
        self.X = np.zeros((P, K), dtype=complex)       # the reference's last P block spectra
        self.xprev = np.zeros(N)
        self.px = np.full(K, 1e-6)                     # the reference's power per bin, smoothed
        self.eprev = np.zeros(N)                       # the suppressor's previous half-windows
        self.yprev = np.zeros(N)
        self.ola = np.zeros(N)
        self.gain = np.ones(K)
        self._in_d = np.zeros(0)
        self._in_x = np.zeros(0)
        self._out = np.zeros(0)  # finished output not yet handed back
        self.near = 99.0
        self.erle = 0.0
        self._recent: deque[float] = deque(maxlen=3)
        self.noise = -60.0        # dB: the output's quietest lately (the room)
        self.echo_level = -120.0  # dB: how loud the echo has lately been at the mic
        self._echo_blocks = 0  # blocks since the reference last had sound in it (its echo dies away after)

    def process(self, mic: np.ndarray, ref: np.ndarray) -> np.ndarray:
        """mic, ref: float arrays of the same length (16 kHz, -1..1). Returns the mic with the echo taken out, the
        same length, one BLOCK late (the suppressor's overlap-add)."""
        self._in_d = np.concatenate([self._in_d, mic.astype(np.float64)])
        self._in_x = np.concatenate([self._in_x, ref.astype(np.float64)])
        out = [self._out]
        while len(self._in_d) >= self.N:
            d, self._in_d = self._in_d[:self.N], self._in_d[self.N:]
            x, self._in_x = self._in_x[:self.N], self._in_x[self.N:]
            out.append(self._block(d, x))
        out = np.concatenate(out)
        if len(out) < len(mic):  # only with a length that isn't whole blocks: pad at the front
            out = np.concatenate([np.zeros(len(mic) - len(out)), out])
        self._out = out[len(mic):]
        return out[:len(mic)].astype(np.float32)

    def _filter(self, W: np.ndarray) -> np.ndarray:
        return np.fft.irfft(np.sum(W * self.X, axis=0), 2 * self.N)[self.N:]

    def _block(self, d: np.ndarray, x: np.ndarray) -> np.ndarray:
        N, P = self.N, self.P
        X = np.fft.rfft(np.concatenate([self.xprev, x]))
        self.xprev = x
        self.X = np.roll(self.X, 1, axis=0)
        self.X[0] = X

        yf, yb = self._filter(self.Wf), self._filter(self.Wb)
        ef, eb = d - yf, d - yb

        # The background filter learns (NLMS, normalised per bin by the reference's power).
        self.px = 0.9 * self.px + 0.1 * (X.real ** 2 + X.imag ** 2)
        Eb = np.fft.rfft(np.concatenate([np.zeros(N), eb]))
        self.Wb += config.ECHO_STEP * np.conj(self.X) * Eb / (P * self.px + 1e-6)
        w = np.fft.irfft(self.Wb, 2 * N, axis=1)
        w[:, N:] = 0  # each partition N taps long (the gradient constraint)
        self.Wb = np.fft.rfft(w, axis=1)

        # Which filter makes the output (Speex mdf.c): take the background's weights when they've been doing better,
        # put them back from the foreground when they've clearly gone wrong (someone talking over the echo).
        Sff, See = float(ef @ ef), float(eb @ eb)
        Dbf = float((yb - yf) @ (yb - yf)) + 1e-9
        diff = Sff - See
        self._davg1 = 0.6 * self._davg1 + 0.4 * diff
        self._davg2 = 0.85 * self._davg2 + 0.15 * diff
        self._dvar1 = 0.36 * self._dvar1 + 0.16 * Sff * Dbf
        self._dvar2 = 0.7225 * self._dvar2 + 0.0225 * Sff * Dbf
        if (diff * abs(diff) > Sff * Dbf or self._davg1 * abs(self._davg1) > 0.5 * self._dvar1
                or self._davg2 * abs(self._davg2) > 0.25 * self._dvar2):
            self.Wf = self.Wb.copy()
            self._davg1 = self._davg2 = self._dvar1 = self._dvar2 = 0.0
            ef, yf = eb, yb
        elif -diff * abs(diff) > 4.0 * Sff * Dbf:
            self.Wb = self.Wf.copy()
            self._davg1 = self._davg2 = self._dvar1 = self._dvar2 = 0.0
        Sdd = float(d @ d)
        if Sdd > 1e-7:
            self.erle = 0.9 * self.erle + 0.1 * 10 * np.log10(Sdd / max(float(ef @ ef), FLOOR))
        out = self._suppress(ef, d - ef)
        self._judge(float(x @ x) / self.N, Sdd / self.N, out)
        return out

    def _judge(self, ref_power: float, mic_power: float, out: np.ndarray) -> None:
        """near for this block: with echo about, the output's level above what's expected of the echo alone, averaged
        over three blocks (45 ms). Expected: how loud the echo has lately been (the mic's level, held and falling
        10 dB a second) less what the canceller usually takes off it (a running average over blocks of echo alone),
        but never under the room's noise (the output's quietest, tracked: it creeps up 0.7 dB a second)."""
        out_db = 10 * np.log10(max(float(out @ out) / self.N, FLOOR))
        self.noise = 0.7 * self.noise + 0.3 * out_db if out_db < self.noise else self.noise + 0.01
        self._echo_blocks = 0 if ref_power > 1e-6 else self._echo_blocks + 1  # -60 dBFS
        if self._echo_blocks > 10:  # 150 ms quiet: no echo left to speak of
            self.near = 99.0
            self.echo_level = -120.0
            self._recent.clear()
            return
        mic_db = 10 * np.log10(max(mic_power, FLOOR))
        self.echo_level = max(mic_db, self.echo_level - 0.15)
        r = out_db - self.echo_level
        self._recent.append(out_db - max(self.echo_level + self.down, self.noise + 3.0))
        self.near = float(np.mean(self._recent))
        if r - self.down < config.ECHO_NEAR_DB and out_db > self.noise + 3.0:  # echo alone, above the noise: learn
            self.down = 0.95 * self.down + 0.05 * r

    def _suppress(self, e: np.ndarray, y: np.ndarray) -> np.ndarray:
        """The residual-echo suppressor on one block: e the linear filter's output, y the echo it predicted."""
        win = self.window
        E = np.fft.rfft(win * np.concatenate([self.eprev, e]))
        Y = np.fft.rfft(win * np.concatenate([self.yprev, y]))
        self.eprev, self.yprev = e, y
        Pe = E.real ** 2 + E.imag ** 2
        Py = Y.real ** 2 + Y.imag ** 2

        # How much of the predicted echo leaks through (Speex): the correlation over time of the output's and the
        # prediction's power spectra, learnt faster while the echo is loud.
        self.Eh = 0.65 * self.Eh + 0.35 * Pe
        self.Yh = 0.65 * self.Yh + 0.35 * Py
        de, dy = Pe - self.Eh, Py - self.Yh
        rate = min(0.5, float(np.sum(Py)) / (float(np.sum(Pe)) + 1e-9) * 0.05)
        self.Pey = (1 - rate) * self.Pey + rate * float(np.sum(de * dy))
        self.Pyy = (1 - rate) * self.Pyy + rate * float(np.sum(dy * dy))
        self.Pyy = max(self.Pyy, 1e-9)
        self.leak = float(np.clip(self.Pey / self.Pyy, 0.005, 1.0))

        residual = config.ECHO_SUPPRESS * self.leak * Py
        g = np.clip(1.0 - residual / (Pe + 1e-12), config.ECHO_FLOOR, 1.0)
        self.gain = np.maximum(g, 0.6 * self.gain)  # opens at once, closes over a few blocks: no musical noise
        frame = np.fft.irfft(self.gain * E, 2 * self.N) * win
        out = self.ola + frame[:self.N]
        self.ola = frame[self.N:]
        return out
