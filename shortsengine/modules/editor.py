"""MODULE 7 - Autonomous video editor.

Pipeline (all decisions come from the storyboard + Channel DNA, not a fixed template):
  1. re-time storyboard shots to the real voice timeline (shots cover gaps, tiny shots merged)
  2. grade each image once toward the DNA's measured look (luma, warmth, colourfulness,
     teal shadows / warm highlights, clarity, soft glow)
  3. render every shot in parallel: sub-pixel simulated camera motion (push-in / pan),
     accent transitions (flash on the reveal), DNA-styled dialogue captions synced to word
     timings, POV title bar, fact card, optional own watermark -> H.264 segment
  4. concat segments losslessly, mix dialogue + original music bed (side-chain ducked) +
     SFX, two-pass EBU R128 loudness normalisation, AAC, faststart MP4
  5. export SRT captions, a thumbnail and render metadata for QC
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import math
import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .. import logs
from ..config import ROOT, load_settings
from ..dna import load_dna
from .music import SR, impact, music_bed, read_wav, whoosh, write_wav
from .storyboard import chunk_caption_words

log = logs.get("editor")
FONT = ROOT / "assets" / "fonts" / "Anton-Regular.woff"
FALLBACK_FONT = "/usr/share/fonts/opentype/inter/Inter-Black.otf"
SAFE_X = 0.05  # caption must stay inside 5%..95% width


def font(size: int) -> ImageFont.FreeTypeFont:
    for f in (FONT, Path(FALLBACK_FONT)):
        try:
            return ImageFont.truetype(str(f), size)
        except OSError:
            continue
    return ImageFont.load_default(size)


# ------------------------------------------------------------------ timeline
def retime(storyboard: dict, tl: dict, min_shot: float = 0.6) -> list[dict]:
    lines = {l["id"]: l for l in tl["lines"]}
    shots = []
    for s in storyboard["shots"]:
        s = dict(s)
        if s.get("line_id"):
            l = lines[s["line_id"]]
            d = l["end"] - l["start"]
            s["start"] = l["start"] + d * s["frac_start"]
        else:
            s["start"] = tl["speech_end"] + 0.1
        shots.append(s)
    shots[0]["start"] = 0.0
    for a, b in zip(shots, shots[1:]):
        a["end"] = b["start"]
    shots[-1]["end"] = tl["total"]
    merged = []
    for s in shots:  # merge flicker shots into the previous one (keep reveal/final shots)
        if merged and s["end"] - s["start"] < min_shot and not s.get("is_final") and s["transition_in"] == "hard_cut":
            merged[-1]["end"] = s["end"]
        else:
            merged.append(s)
    for i, s in enumerate(merged, 1):
        s["n"] = i
        s["start"], s["end"] = round(s["start"], 4), round(s["end"], 4)
    return merged


# ------------------------------------------------------------------ grading
def colourfulness(a: np.ndarray) -> float:
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    rg, yb = r - g, 0.5 * (r + g) - b
    return float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))


def grade_image(im: Image.Image, g: dict) -> Image.Image:
    """Move an image toward the DNA's measured grade (values on 0..255 / 0..1 scales)."""
    target_luma = g.get("mean_luma", 0.6)
    target_warm = g.get("warmth_r_minus_b", 15.0)
    target_cf = g.get("colourfulness", 45.0)
    a = np.asarray(im.convert("RGB"), dtype=np.float32) / 255
    v = a.max(axis=2).mean()
    gamma = math.log(max(1e-3, target_luma)) / math.log(max(1e-3, v)) if 0.02 < v < 0.98 else 1.0
    a = np.clip(a, 0, 1) ** float(np.clip(gamma, 0.55, 1.8))
    a = np.clip(0.5 + (a - 0.5) * 1.08, 0, 1)  # gentle contrast
    luma = (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2])[..., None]
    shadows, highs = np.clip(1 - luma * 2, 0, 1), np.clip(luma * 2 - 1, 0, 1)
    a += shadows * np.array([-0.035, 0.012, 0.04]) + highs * np.array([0.035, 0.012, -0.03])  # teal/orange split
    warm_now = float((a[..., 0] - a[..., 2]).mean() * 255)
    a[..., 0] += (target_warm - warm_now) / 255 * 0.5
    a[..., 2] -= (target_warm - warm_now) / 255 * 0.5
    a = np.clip(a, 0, 1)
    cf_now = colourfulness(a * 255)
    sat = float(np.clip(target_cf / max(cf_now, 1), 0.8, 1.5))
    luma = (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2])[..., None]
    a = np.clip(luma + (a - luma) * sat, 0, 1)
    out = Image.fromarray((a * 255).astype(np.uint8))
    out = out.filter(ImageFilter.UnsharpMask(radius=4, percent=55, threshold=2))  # clarity
    hl = out.point(lambda x: max(0, x - 175) * 3).filter(ImageFilter.GaussianBlur(28))  # soft glow
    o, h = np.asarray(out, dtype=np.float32) / 255, np.asarray(hl, dtype=np.float32) / 255
    o = 1 - (1 - o) * (1 - h * 0.22)
    yy, xx = np.mgrid[0:o.shape[0], 0:o.shape[1]]
    d = np.sqrt(((xx / o.shape[1]) - 0.5) ** 2 + ((yy / o.shape[0]) - 0.45) ** 2)
    o *= (1 - np.clip(d - 0.45, 0, 1) * 0.35)[..., None]  # subtle vignette
    return Image.fromarray((np.clip(o, 0, 1) * 255).astype(np.uint8))


# ------------------------------------------------------------------ overlays
def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _lighten(rgb, f):
    return tuple(int(x + (255 - x) * f) for x in rgb)


def render_caption(line1: str, line2: str, colour: tuple, W: int) -> tuple[Image.Image, dict]:
    """Two-line glowing caption (line 2 larger = emphasis). Auto-shrinks to the safe width."""
    max_w = int(W * (1 - 2 * SAFE_X)) - 40
    s1, s2 = int(W * 0.062), int(W * 0.098)
    for _ in range(12):
        f1, f2 = font(s1), font(s2)
        w1 = f1.getbbox(line1)[2] if line1 else 0
        w2 = f2.getbbox(line2)[2]
        if max(w1, w2) <= max_w:
            break
        s1, s2 = int(s1 * 0.9), int(s2 * 0.9)
    h1 = (f1.getbbox("HG")[3] + 8) if line1 else 0
    h2 = f2.getbbox("HG")[3]
    pad = 40
    cw, ch = max(w1, w2) + 2 * pad, h1 + h2 + 2 * pad
    mask = Image.new("L", (cw, ch), 0)
    d = ImageDraw.Draw(mask)
    if line1:
        d.text(((cw - w1) // 2, pad), line1, font=f1, fill=255)
    d.text(((cw - w2) // 2, pad + h1), line2, font=f2, fill=255)
    glow = mask.filter(ImageFilter.GaussianBlur(14)).point(lambda x: min(255, x * 2.6))
    out = Image.new("RGBA", (cw, ch), colour + (0,))
    out.putalpha(glow)
    shadow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    shadow.putalpha(mask.filter(ImageFilter.MaxFilter(5)).point(lambda x: int(x * 0.55)))
    out = Image.alpha_composite(out, shadow)
    fill = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fill)
    if line1:
        fd.text(((cw - w1) // 2, pad), line1, font=f1, fill=_lighten(colour, 0.55) + (255,))
    fd.text(((cw - w2) // 2, pad + h1), line2, font=f2, fill=_lighten(colour, 0.25) + (255,))
    out = Image.alpha_composite(out, fill)
    return out, {"text_w": max(w1, w2), "w": cw, "h": ch}


def render_title_bar(text: str, W: int) -> Image.Image:
    f = font(int(W * 0.05))
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if f.getbbox(t)[2] > W * 0.9 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    lines.append(cur)
    if len(lines) > 2:
        return render_title_bar(text, int(W * 0.92)) if W > 600 else Image.new("RGBA", (W, 10))
    lh = f.getbbox("HG")[3] + 6
    h = lh * len(lines) + 34
    im = Image.new("RGBA", (W, h), (255, 255, 255, 255))
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        d.text(((W - f.getbbox(ln)[2]) // 2, 16 + i * lh), ln, font=f, fill=(10, 10, 10, 255))
    return im


def render_fact_card(text: str, source: str, W: int) -> Image.Image:
    f1, f2, f3 = font(int(W * 0.034)), font(int(W * 0.04)), ImageFont.truetype(FALLBACK_FONT, int(W * 0.024)) \
        if Path(FALLBACK_FONT).exists() else font(int(W * 0.024))
    maxw = int(W * 0.8)
    words, lines, cur = text.upper().split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if f2.getbbox(t)[2] > maxw and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    lines.append(cur)
    lh = f2.getbbox("HG")[3] + 8
    h = 30 + f1.getbbox("HG")[3] + 14 + lh * len(lines) + 16 + f3.getbbox("Hg")[3] + 30
    im = Image.new("RGBA", (int(W * 0.88), h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, im.width - 1, h - 1), 28, fill=(12, 16, 22, 200))
    d.text((34, 30), "REAL FACT", font=f1, fill=(255, 225, 77, 255))
    y = 30 + f1.getbbox("HG")[3] + 14
    for ln in lines:
        d.text((34, y), ln, font=f2, fill=(255, 255, 255, 255))
        y += lh
    d.text((34, y + 10), source, font=f3, fill=(200, 205, 215, 255))
    return im


def render_small_label(text: str, W: int, size: float = 0.026) -> Image.Image:
    f = ImageFont.truetype(FALLBACK_FONT, int(W * size)) if Path(FALLBACK_FONT).exists() else font(int(W * size))
    w, h = f.getbbox(text)[2] + 24, f.getbbox("Hg")[3] + 16
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.text((13, 9), text, font=f, fill=(0, 0, 0, 120))
    d.text((12, 8), text, font=f, fill=(255, 255, 255, 190))
    return im


# ------------------------------------------------------------------ shot rendering
def _ease(x: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * min(1, max(0, x)))


def camera_transform(kind: str, p: float, strength: float, W: int, H: int, src: tuple[int, int]):
    sw, sh = src
    base = max(W / sw, H / sh)
    z0, z1, dx0, dx1 = 1.0, 1.05, 0.0, 0.0
    if kind == "push_in_fast":
        z0, z1 = 1.0, 1.11
    elif kind == "pan_left":
        z0 = z1 = 1.08
        dx0, dx1 = 0.025, -0.025
    elif kind == "pan_right":
        z0 = z1 = 1.08
        dx0, dx1 = -0.025, 0.025
    e = _ease(p)
    z = 1 + ((z0 + (z1 - z0) * e) - 1) * strength
    dx = (dx0 + (dx1 - dx0) * e) * strength
    scale = base * z
    cw, ch = W / scale, H / scale
    cx = sw * (0.5 + dx)
    cy = sh * 0.45
    x0 = min(max(cx - cw / 2, 0), sw - cw)
    y0 = min(max(cy - ch / 2, 0), sh - ch)
    return (1 / scale, 0, x0, 0, 1 / scale, y0)


def render_shot(job: dict) -> dict:
    W, H, fps = job["W"], job["H"], job["fps"]
    src = Image.open(job["image"]).convert("RGB")
    f0, f1 = job["frame0"], job["frame1"]
    n = f1 - f0
    caps = [(c, Image.open(c["png"]).convert("RGBA")) for c in job["captions"]]
    overlays = [(o, Image.open(o["png"]).convert("RGBA")) for o in job["overlays"]]
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-", "-c:v", "libx264", "-preset", job["preset"], "-crf", str(job["crf"]), "-pix_fmt", "yuv420p",
           "-profile:v", "high", "-g", str(fps * 2), "-bf", "2", "-movflags", "+faststart", job["out"]]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(n):
            t = (f0 + i) / fps
            p = i / max(1, n - 1)
            frame = src.transform((W, H), Image.AFFINE,
                                  camera_transform(job["camera"], p, job["motion"], W, H, src.size),
                                  resample=Image.BICUBIC).convert("RGBA")
            if job["transition_in"] in ("flash_white", "cross_dissolve") and t - job["start"] < 0.3:
                a = 1 - (t - job["start"]) / 0.3
                frame = Image.blend(frame, Image.new("RGBA", (W, H), (255, 255, 255, 255)), 0.85 * a)
            for o, im in overlays:
                if o["start"] <= t < o["end"]:
                    frame.alpha_composite(im, (o["x"], o["y"]))
            for c, im in caps:
                if c["start"] <= t < c["end"]:
                    age = t - c["start"]
                    if age < 0.1:  # pop-in
                        k = 0.86 + 1.4 * age
                        im2 = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.BILINEAR)
                        frame.alpha_composite(im2, (c["cx"] - im2.width // 2, c["cy"] - im2.height // 2))
                    else:
                        frame.alpha_composite(im, (c["cx"] - im.width // 2, c["cy"] - im.height // 2))
            proc.stdin.write(frame.convert("RGB").tobytes())
        proc.stdin.close()
        rc = proc.wait()
    except BrokenPipeError:
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed for shot {job['n']}")
    return {"n": job["n"], "out": job["out"], "frames": n}


# ------------------------------------------------------------------ audio
def mix_audio(workdir: Path, tl: dict, shots: list[dict], seed: int) -> Path:
    vs = load_settings().video
    total = tl["total"]
    n = int(total * SR)
    dia = read_wav(workdir / "dialogue.wav")[:n]
    dia = np.pad(dia, (0, n - len(dia)))
    mix = dia.copy()
    if vs.music_enabled:
        bed = music_bed(total, seed)[:n]
        bed = np.pad(bed, (0, n - len(bed)))
        win = int(0.05 * SR)
        env = np.sqrt(np.convolve(dia ** 2, np.ones(win) / win, mode="same"))
        speech = (env > 0.01).astype(np.float32)
        att, rel = 1 - math.exp(-1 / (0.03 * SR)), 1 - math.exp(-1 / (0.45 * SR))
        sm = np.zeros(n, dtype=np.float32)
        # vectorised-ish smoothing in blocks for speed
        blk = 240
        val = 0.0
        for i in range(0, n, blk):
            tgt = speech[i:i + blk].max()
            k = att if tgt > val else rel
            val += (tgt - val) * (1 - (1 - k) ** blk)
            sm[i:i + blk] = val
        duck = 10 ** (vs.music_duck_db / 20)
        base = 10 ** (-12 / 20)
        mix += bed * base * (1 - sm * (1 - duck))
    if vs.sfx_enabled:
        for s in shots:
            fx = None
            if s.get("sfx") == "impact":
                fx = impact(seed) * 0.6
            elif s.get("sfx") == "whoosh":
                fx = whoosh(seed) * 0.35
            if fx is not None:
                st = max(0, int(s["start"] * SR) - (int(0.2 * SR) if s["sfx"] == "whoosh" else 0))
                mix[st:st + len(fx)] += fx[: n - st]
    peak = np.max(np.abs(mix)) or 1
    if peak > 0.98:
        mix = mix / peak * 0.98
    p = workdir / "mix.wav"
    write_wav(p, mix)
    return p


def loudnorm_filter(src: Path, I: float, TP: float) -> str:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-af",
                        f"loudnorm=I={I}:TP={TP}:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        return f"loudnorm=I={I}:TP={TP}:LRA=11"
    d = json.loads(m.group(0))
    return (f"loudnorm=I={I}:TP={TP}:LRA=11:measured_I={d['input_i']}:measured_TP={d['input_tp']}:"
            f"measured_LRA={d['input_lra']}:measured_thresh={d['input_thresh']}:offset={d['target_offset']}:linear=true")


# ------------------------------------------------------------------ main
def srt_time(t: float) -> str:
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


def render(job_id: int, script: dict, storyboard: dict, tl: dict, assets: dict[str, str], workdir: Path,
           research: dict | None = None) -> dict:
    st = load_settings()
    vs, dna = st.video, load_dna()
    W, H, fps = vs.width, vs.height, vs.fps
    rdir = workdir / "render"
    (rdir / "graded").mkdir(parents=True, exist_ok=True)
    (rdir / "overlays").mkdir(exist_ok=True)
    (rdir / "segments").mkdir(exist_ok=True)
    shots = retime(storyboard, tl)

    # 1. grade every used asset once (parallel)
    used = sorted({s["asset_key"] for s in shots})

    def grade_one(k):
        dst = rdir / "graded" / f"{k}.png"
        if not dst.exists():
            im = Image.open(assets[k]).convert("RGB")
            big = im.resize((int(W * 1.12), int(H * 1.12)), Image.LANCZOS)
            grade_image(big, storyboard.get("grade") or dna.grade).save(dst)
        return k, str(dst)

    with cf.ThreadPoolExecutor(vs.render_workers) as ex:
        graded = dict(ex.map(grade_one, used))

    # 2. captions from real word timings
    cap_style = storyboard.get("caption_style") or {}
    palette = [_hex(c) for c in cap_style.get("palette", ["#FFE14D", "#4CFF6A", "#FF4DF0", "#4DB8FF", "#FFA64D"])]
    max_words = (cap_style.get("words_per_chunk") or [1, 4])[-1]
    y_c = int(H * cap_style.get("y_center_pct", 0.5))
    captions, ci = [], 0
    for line in tl["lines"]:
        for ch in chunk_caption_words(line["words"], max_words):
            col = palette[ci % len(palette)]
            png = rdir / "overlays" / f"cap_{ci:04d}.png"
            im, meta = render_caption(ch["line1"], ch["line2"], col, W)
            im.save(png)
            captions.append({"start": ch["start"], "end": ch["end"], "line1": ch["line1"], "line2": ch["line2"],
                             "png": str(png), "cx": W // 2, "cy": y_c, "w": meta["w"], "h": meta["h"],
                             "text_w": meta["text_w"], "colour": "#%02x%02x%02x" % col, "line_id": line["id"]})
            ci += 1
    # extend each caption until the next one (max +0.5 s) so text does not flicker between words
    for a, b in zip(captions, captions[1:]):
        a["end"] = round(min(b["start"], a["end"] + 0.5), 3)
    if captions:
        captions[-1]["end"] = round(captions[-1]["end"] + 0.5, 3)

    # 3. other overlays
    overlays = []
    tb = storyboard.get("title_bar")
    if tb:
        im = render_title_bar(tb["text"], W)
        p = rdir / "overlays" / "title_bar.png"
        im.save(p)
        overlays.append({"kind": "title_bar", "start": tb["start"], "end": tb["end"], "png": str(p), "x": 0, "y": 0,
                         "w": im.width, "h": im.height})
    lab = render_small_label("Fictional story · AI-generated visuals", W)
    p = rdir / "overlays" / "disclosure.png"
    lab.save(p)
    overlays.append({"kind": "disclosure", "start": 0.0, "end": 3.0, "png": str(p), "x": (W - lab.width) // 2,
                     "y": int(H * 0.955) - lab.height, "w": lab.width, "h": lab.height})
    fc = storyboard.get("fact_card") or {}
    claim = next((c for c in (research or {}).get("verified_claims", []) if c["id"] == fc.get("claim_id")), None)
    if claim and fc.get("text"):
        im = render_fact_card(fc["text"], f"Source: Wikipedia – {claim['page_title']}", W)
        p = rdir / "overlays" / "fact_card.png"
        im.save(p)
        start = max(0.0, tl["total"] - 5.5)
        overlays.append({"kind": "fact_card", "start": start, "end": tl["total"] + 1, "png": str(p),
                         "x": (W - im.width) // 2, "y": int(H * 0.66), "w": im.width, "h": im.height})
        # keep captions clear of the fact card
        for c in captions:
            if c["end"] > start:
                c["cy"] = int(H * 0.47)
    if vs.brand_handle:
        lab = render_small_label(vs.brand_handle, W, 0.022)
        p = rdir / "overlays" / "brand.png"
        lab.save(p)
        overlays.append({"kind": "brand", "start": 0.0, "end": tl["total"] + 1, "png": str(p),
                         "x": (W - lab.width) // 2, "y": int(H * 0.92), "w": lab.width, "h": lab.height})

    # 4. render segments in parallel
    jobs = []
    for s in shots:
        f0, f1 = round(s["start"] * fps), round(s["end"] * fps)
        if f1 <= f0:
            continue
        jobs.append({"n": s["n"], "W": W, "H": H, "fps": fps, "image": graded[s["asset_key"]], "frame0": f0, "frame1": f1,
                     "start": s["start"], "camera": s["camera"], "motion": vs.motion_strength,
                     "transition_in": s["transition_in"], "preset": vs.preset, "crf": vs.crf,
                     "captions": [c for c in captions if c["start"] < s["end"] and c["end"] > s["start"]],
                     "overlays": [o for o in overlays if o["start"] < s["end"] and o["end"] > s["start"]],
                     "out": str(rdir / "segments" / f"shot_{s['n']:04d}.mp4")})
    with cf.ProcessPoolExecutor(vs.render_workers) as ex:
        results = list(ex.map(render_shot, jobs))
    concat = rdir / "concat.txt"
    concat.write_text("".join(f"file '{Path(j['out']).resolve()}'\n" for j in jobs))
    video = rdir / "video.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy",
                    str(video)], check=True)

    # 5. audio
    mix = mix_audio(workdir, tl, shots, seed=job_id * 7919 + 17)
    af = loudnorm_filter(mix, st.voice.loudness_lufs, st.voice.true_peak_db)
    final = workdir / "final.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(mix), "-map", "0:v:0", "-map", "1:a:0",
                    "-af", af + ",aresample=48000", "-c:v", "copy", "-c:a", "aac", "-b:a", vs.audio_bitrate,
                    "-ar", "48000", "-ac", "2", "-t", f"{tl['total']:.3f}", "-movflags", "+faststart", str(final)],
                   check=True)

    # 6. sidecars
    srt = workdir / "captions.srt"
    srt.write_text("".join(f"{i}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n{(c['line1'] + ' ' + c['line2']).strip()}\n\n"
                           for i, c in enumerate(captions, 1)))
    thumb = workdir / "thumbnail.jpg"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "0.6", "-i", str(final), "-frames:v", "1", "-q:v", "3", str(thumb)],
                   check=True)
    meta = {"final": str(final), "srt": str(srt), "thumbnail": str(thumb), "duration": tl["total"],
            "shots": [{k: s[k] for k in ("n", "start", "end", "framing", "asset_key", "camera", "transition_in", "beat",
                                         "line_id", "characters", "sfx")} | {"is_final": bool(s.get("is_final"))}
                      for s in shots],
            "captions": [{k: c[k] for k in ("start", "end", "line1", "line2", "cx", "cy", "w", "h", "text_w", "colour",
                                            "line_id")} for c in captions],
            "overlays": [{k: o[k] for k in ("kind", "start", "end", "x", "y", "w", "h")} for o in overlays],
            "segments": len(results), "frame_size": [W, H], "fps": fps,
            "mean_shot_s": round(tl["total"] / max(1, len(shots)), 3)}
    (workdir / "render.json").write_text(json.dumps(meta, indent=1))
    log.info("rendered %s (%d shots, %.1fs)", final, len(shots), tl["total"], extra={"job_id": job_id, "stage": "edit"})
    return meta

