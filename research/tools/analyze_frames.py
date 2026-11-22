"""Quantitative visual analysis of YouTube storyboard frames.

Storyboards are YouTube's own low-resolution frame samples (here: 180x180 cells, ~0.51 fps,
i.e. one frame every ~1.96 s). They are real frames from the published video, but the low
sampling rate means:
  * cut counts are a LOWER bound (several cuts inside one 2 s gap count as one change);
  * motion/zoom can only be inferred between samples, not measured inside shots.

Per video we compute colour statistics, an estimate of visual change frequency, caption
presence/position, title-bar presence, black frames and end-frame properties.

Usage: python research/tools/analyze_frames.py --raw research/raw --out research/frame_metrics.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def load_frames(d: Path) -> tuple[list[np.ndarray], dict]:
    spec = json.loads((d / "spec.json").read_text())
    w, h, cols, rows = spec["width"], spec["height"], spec["columns"], spec["rows"]
    fps = spec["fps"] or 0.5
    n_expected = int((spec.get("duration") or 0) * fps) or 10**6
    frames: list[np.ndarray] = []
    for sheet in sorted(d.glob("sb*.jpg")):
        im = np.asarray(Image.open(sheet).convert("RGB"), dtype=np.float32)
        for r in range(rows):
            for c in range(cols):
                cell = im[r * h:(r + 1) * h, c * w:(c + 1) * w]
                if cell.shape[0] != h or cell.shape[1] != w:
                    continue
                frames.append(cell)
    return frames[:n_expected], spec


def hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r, g, b = rgb[..., 0] / 255, rgb[..., 1] / 255, rgb[..., 2] / 255
    mx, mn = np.maximum(np.maximum(r, g), b), np.minimum(np.minimum(r, g), b)
    v = mx
    s = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    return r, s, v


def colourfulness(rgb: np.ndarray) -> float:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    rg, yb = r - g, 0.5 * (r + g) - b
    return float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))


def hist_diff(a: np.ndarray, b: np.ndarray) -> float:
    """Chi-like distance between coarse RGB histograms of the central region (ignores captions)."""
    def h(x):
        x = x[: int(x.shape[0] * 0.5)]  # upper half: subject faces, unaffected by subtitle band
        q = (x // 32).astype(int)
        idx = q[..., 0] * 64 + q[..., 1] * 8 + q[..., 2]
        hh = np.bincount(idx.ravel(), minlength=512).astype(float)
        return hh / hh.sum()
    ha, hb = h(a), h(b)
    return float(0.5 * np.abs(ha - hb).sum())


def text_rows(rgb: np.ndarray) -> np.ndarray:
    """Per-row density of 'caption-like' pixels: bright, high local horizontal contrast."""
    gray = rgb.mean(axis=2)
    gx = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1]))
    _, s, v = hsv(rgb)
    mask = (v > 0.80) & (gx > 40)
    return mask.mean(axis=1)


def analyse(frames: list[np.ndarray], spec: dict) -> dict:
    if not frames:
        return {"frames": 0}
    interval = 1.0 / (spec["fps"] or 0.5)
    lum, sat, warm, cf, contrast = [], [], [], [], []
    caption_y, caption_hits, black = [], 0, 0
    title_bar = []
    for f in frames:
        _, s, v = hsv(f)
        lum.append(float(v.mean()))
        sat.append(float(s.mean()))
        warm.append(float((f[..., 0] - f[..., 2]).mean()))
        cf.append(colourfulness(f))
        contrast.append(float(f.mean(axis=2).std()))
        if f.mean() < 15:
            black += 1
        tr = text_rows(f)
        h = len(tr)
        band = tr[int(h * 0.35): int(h * 0.92)]
        if band.max() > 0.06:
            caption_hits += 1
            caption_y.append((int(h * 0.35) + int(band.argmax())) / h)
        # Title bar = white strip with dark text in the top ~7% of the frame (calibrated on
        # known frames: present >=0.5 white ratio; absent <0.35).
        top = f[: max(1, int(h * 0.07))]
        title_bar.append(bool(((top > 200).all(axis=2)).mean() > 0.45))
    diffs = [hist_diff(frames[i], frames[i + 1]) for i in range(len(frames) - 1)]
    changes = [d > 0.35 for d in diffs]
    change_rate = sum(changes) / max(1, len(changes))
    # title-bar persistence (seconds from start while present)
    tb_secs = 0.0
    for t in title_bar:
        if not t:
            break
        tb_secs += interval
    return {
        "frames": len(frames),
        "sample_interval_s": round(interval, 3),
        "mean_luma": round(float(np.mean(lum)), 3),
        "mean_saturation": round(float(np.mean(sat)), 3),
        "mean_warmth_r_minus_b": round(float(np.mean(warm)), 2),
        "mean_colourfulness": round(float(np.mean(cf)), 2),
        "mean_contrast_std": round(float(np.mean(contrast)), 2),
        "visual_change_ratio": round(change_rate, 3),
        "est_changes_per_min_lower_bound": round(change_rate * 60 / interval, 2),
        "est_mean_shot_s_upper_bound": round(interval / change_rate, 2) if change_rate else None,
        "caption_frame_ratio": round(caption_hits / len(frames), 3),
        "caption_y_median": round(float(np.median(caption_y)), 3) if caption_y else None,
        "title_bar_first_frame": title_bar[0],
        "title_bar_seconds": round(tb_secs, 2),
        "title_bar_frame_ratio": round(sum(title_bar) / len(title_bar), 3),
        "black_frames": black,
        "first_frame_luma": round(lum[0], 3),
        "last_frame_luma": round(lum[-1], 3),
        "first_change_s": next((round((i + 1) * interval, 2) for i, c in enumerate(changes) if c), None),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="research/raw")
    ap.add_argument("--out", default="research/frame_metrics.json")
    a = ap.parse_args()
    res = {}
    for d in sorted((Path(a.raw) / "storyboards").iterdir()):
        if (d / "spec.json").exists():
            frames, spec = load_frames(d)
            res[d.name] = analyse(frames, spec)
    Path(a.out).write_text(json.dumps(res, indent=1))
    print(f"analysed {len(res)} videos -> {a.out}")


if __name__ == "__main__":
    main()
