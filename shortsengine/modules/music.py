"""Original procedural music bed and sound effects (numpy synthesis).

The reference channel uses one recurring dark music bed (inferred from description credits).
We do not reuse that track: this module synthesises an original, royalty-free dark ambient bed
(minor-key pads, sub pulse, sparse ticks) and simple SFX (whoosh, impact). Seeded per video
so each video gets a variation."""
from __future__ import annotations

import random
import wave
from pathlib import Path

import numpy as np

SR = 48000
NOTE = {"A2": 110.0, "C3": 130.81, "D3": 146.83, "E3": 164.81, "F2": 87.31, "F3": 174.61, "G2": 98.0, "G3": 196.0,
        "A3": 220.0, "B2": 123.47, "E2": 82.41, "D2": 73.42, "C4": 261.63, "E4": 329.63, "A4": 440.0}
PROGRESSIONS = [
    [("A2", ["A3", "C4", "E4"]), ("F2", ["F3", "A3", "C4"]), ("D2", ["D3", "F3", "A3"]), ("E2", ["E3", "G3", "B2"])],
    [("A2", ["A3", "C4", "E4"]), ("G2", ["G3", "B2", "D3"]), ("F2", ["F3", "A3", "C4"]), ("E2", ["E3", "G3", "B2"])],
    [("D2", ["D3", "F3", "A3"]), ("A2", ["A3", "C4", "E4"]), ("F2", ["F3", "A3", "C4"]), ("G2", ["G3", "B2", "D3"])],
]


def _env(n: int, a: float, r: float) -> np.ndarray:
    e = np.ones(n)
    na, nr = int(a * SR), int(r * SR)
    if na:
        e[:na] = np.linspace(0, 1, na)
    if nr:
        e[-nr:] *= np.linspace(1, 0, nr)
    return e


def _reverb(x: np.ndarray, seconds: float = 1.6, mix: float = 0.25, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    ir = rng.standard_normal(n) * np.exp(-np.linspace(0, 6, n))
    ir /= np.abs(ir).sum() / 3
    L = len(x) + n - 1  # FFT convolution
    nfft = 1 << (L - 1).bit_length()
    wet = np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(ir, nfft), nfft)[: len(x)]
    return (1 - mix) * x + mix * wet / (np.max(np.abs(wet)) + 1e-9) * np.max(np.abs(x))


def music_bed(duration: float, seed: int, bpm: float = 72) -> np.ndarray:
    rnd = random.Random(seed)
    prog = rnd.choice(PROGRESSIONS)
    n = int(duration * SR)
    out = np.zeros(n)
    bar = 4 * 60 / bpm
    t_all = np.arange(n) / SR
    i = 0
    while i * bar < duration:
        root, chord = prog[i % len(prog)]
        s, e = int(i * bar * SR), min(n, int((i + 1) * bar * SR))
        t = t_all[s:e] - i * bar
        seg = np.zeros(e - s)
        for note in chord:  # soft detuned pad
            f = NOTE[note]
            for det in (-0.6, 0.6):
                seg += 0.05 * np.sin(2 * np.pi * (f + det) * t + rnd.random() * 6)
                seg += 0.012 * np.sin(2 * np.pi * 2 * (f + det) * t)
        seg *= _env(e - s, 0.8, 0.8)
        fr = NOTE[root]  # sub pulse on beats 1 and 3
        for beat in (0, 2):
            bs = int(beat * 60 / bpm * SR)
            if bs < len(seg):
                k = min(len(seg) - bs, int(0.7 * SR))
                tt = np.arange(k) / SR
                seg[bs:bs + k] += 0.22 * np.sin(2 * np.pi * fr * tt) * np.exp(-tt * 4)
        for eighth in range(8):  # sparse ticks
            if rnd.random() < 0.45:
                bs = int(eighth * 30 / bpm * SR)
                k = min(len(seg) - bs, int(0.03 * SR))
                if k > 0:
                    seg[bs:bs + k] += 0.02 * np.random.default_rng(seed + i * 8 + eighth).standard_normal(k) * np.linspace(1, 0, k)
        out[s:e] += seg
        i += 1
    out = _reverb(out, seed=seed)
    fade = min(n, int(2.0 * SR))
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    return (out / (np.max(np.abs(out)) + 1e-9) * 0.5).astype(np.float32)


def whoosh(seed: int = 1, dur: float = 0.45) -> np.ndarray:
    n = int(dur * SR)
    noise = np.random.default_rng(seed).standard_normal(n)
    # sweep a one-pole low-pass cutoff up then down
    cut = np.concatenate([np.linspace(0.02, 0.35, n // 2), np.linspace(0.35, 0.02, n - n // 2)])
    y = np.zeros(n)
    acc = 0.0
    for i in range(n):
        acc += cut[i] * (noise[i] - acc)
        y[i] = acc
    y *= np.sin(np.linspace(0, np.pi, n)) ** 2
    return (y / (np.max(np.abs(y)) + 1e-9) * 0.5).astype(np.float32)


def impact(seed: int = 2, dur: float = 1.2) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 55 * np.exp(-t * 3) + 38
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 3.5)
    click = np.random.default_rng(seed).standard_normal(n) * np.exp(-t * 60) * 0.4
    y = body + click
    return (y / (np.max(np.abs(y)) + 1e-9) * 0.7).astype(np.float32)


def write_wav(path: Path, x: np.ndarray) -> None:
    x16 = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(x16.tobytes())


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SR and w.getnchannels() == 1, "expected 48 kHz mono"
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
