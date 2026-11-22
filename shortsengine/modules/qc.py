"""MODULE 8 - Automatic quality control.

Technical checks run on the actual exported MP4 (ffprobe + ffmpeg analysis filters);
creative checks run on the render metadata, voice timeline and script against the DNA;
originality/rights checks use the asset registry and previous scripts. Every failure
carries an explanation and the production stage it is sent back to."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .. import logs
from ..config import load_settings
from ..dna import load_dna, reference_titles
from ..textutil import jaccard, shingle_sim
from . import assets as assets_mod
from .script import previous_scripts, script_text, structure_sig, validate_script

log = logs.get("qc")
STAGE_ORDER = ["topic", "research", "script", "storyboard", "assets", "voice", "edit"]


def ffprobe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return {}
    return json.loads(r.stdout)


def analyse_media(path: Path) -> dict:
    """One ffmpeg decode pass: errors, black segments, audio peak and loudness."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-i", str(path),
                        "-vf", "blackdetect=d=0.25:pix_th=0.08", "-af", "volumedetect,ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True)
    err = r.stderr
    blacks = [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)]
    maxv = re.search(r"max_volume: (-?[\d.]+) dB", err)
    integ = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    peak = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", err)
    decode_errors = [l for l in err.splitlines() if re.search(r"\b(error|corrupt|invalid|non-existing)\b", l, re.I)
                     and "Conversion failed" not in l]
    return {"black_segments": blacks, "max_volume_db": float(maxv.group(1)) if maxv else None,
            "integrated_lufs": float(integ[-1]) if integ else None, "true_peak_dbfs": float(peak[-1]) if peak else None,
            "decode_errors": decode_errors[:10], "returncode": r.returncode}


def run(job_id: int, script: dict, storyboard: dict, tl: dict, render_meta: dict, research: dict,
        workdir: Path) -> dict:
    st = load_settings()
    dna = load_dna()
    vs = st.video
    checks: list[dict] = []

    def add(name, cat, ok, detail, back, severity="major", weight=1.0):
        checks.append({"name": name, "category": cat, "passed": bool(ok), "detail": detail, "send_back": back,
                       "severity": severity, "weight": weight})

    final = Path(render_meta["final"])
    probe = ffprobe(final) if final.exists() else {}
    vstreams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
    astreams = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
    v = vstreams[0] if vstreams else {}
    a = astreams[0] if astreams else {}
    # ---------------- technical
    add("file_integrity", "technical", bool(probe) and final.stat().st_size > 100_000 if final.exists() else False,
        f"ffprobe {'ok' if probe else 'failed'}", "edit", "critical", 2)
    add("resolution", "technical", (v.get("width"), v.get("height")) == (vs.width, vs.height),
        f"{v.get('width')}x{v.get('height')} (expected {vs.width}x{vs.height})", "edit", "critical")
    add("aspect_9_16", "technical", v.get("width") and abs(v["width"] / v["height"] - 9 / 16) < 0.01,
        f"ratio {v.get('width', 0) / max(1, v.get('height', 1)):.4f}", "edit", "critical")
    fr = v.get("r_frame_rate", "0/1").split("/")
    fps = float(fr[0]) / float(fr[1] or 1) if len(fr) == 2 and float(fr[1] or 0) else 0
    add("frame_rate", "technical", abs(fps - vs.fps) < 0.01, f"{fps:.3f} fps (expected {vs.fps})", "edit", "critical")
    add("codecs", "technical", v.get("codec_name") == "h264" and a.get("codec_name") == "aac" and v.get("pix_fmt") == "yuv420p",
        f"video {v.get('codec_name')}/{v.get('pix_fmt')}, audio {a.get('codec_name')}", "edit", "critical")
    add("audio_present", "technical", bool(a), f"{len(astreams)} audio stream(s)", "edit", "critical")
    dur = float(probe.get("format", {}).get("duration", 0) or 0)
    lo, hi = (vs.duration_override_s * 0.92, vs.duration_override_s * 1.04) if vs.duration_override_s else \
        (dna.duration_min, dna.duration_max)
    add("duration_dna", "technical", lo <= dur <= hi + 0.5, f"{dur:.2f}s (DNA range {lo:.0f}-{hi:.0f}s)", "voice", "major", 1.5)
    add("shorts_limit", "technical", 0 < dur <= 180, f"{dur:.2f}s (YouTube Shorts max 180s)", "voice", "critical")
    m = analyse_media(final) if final.exists() else {"black_segments": [], "decode_errors": ["missing file"]}
    add("no_black_frames", "technical", not m["black_segments"], f"black segments: {m['black_segments'][:5]}", "edit", "critical")
    add("no_corrupt_frames", "technical", not m["decode_errors"], "; ".join(m["decode_errors"][:3]) or "clean decode",
        "edit", "critical")
    add("no_clipping", "technical", m.get("max_volume_db") is not None and m["max_volume_db"] <= -0.3 and
        (m.get("true_peak_dbfs") is None or m["true_peak_dbfs"] <= -0.3),
        f"max {m.get('max_volume_db')} dB, true peak {m.get('true_peak_dbfs')} dBFS", "edit", "major")
    li = m.get("integrated_lufs")
    add("loudness", "technical", li is not None and abs(li - st.voice.loudness_lufs) <= 2.0,
        f"{li} LUFS (target {st.voice.loudness_lufs})", "edit", "minor", 0.5)
    shots = render_meta["shots"]
    missing = [s["asset_key"] for s in storyboard["shots"]
               if not Path(workdir / "assets" / f"{s['asset_key']}.png").exists()]
    add("assets_present", "technical", not missing, f"missing: {sorted(set(missing))[:5]}", "assets", "critical")
    W, H = render_meta["frame_size"]
    bad_caps = []
    for c in render_meta["captions"]:
        x0, x1 = c["cx"] - c["text_w"] / 2, c["cx"] + c["text_w"] / 2
        y0, y1 = c["cy"] - c["h"] / 2, c["cy"] + c["h"] / 2
        if x0 < W * 0.05 or x1 > W * 0.95 or y0 < H * 0.1 or y1 > H * 0.85:
            bad_caps.append(f"{c['line1']} {c['line2']}".strip())
    add("subtitle_safe_area", "technical", not bad_caps, f"{len(bad_caps)} caption(s) outside safe area {bad_caps[:3]}",
        "edit", "critical")
    # ---------------- creative
    first = shots[0]
    framing_of = {"medium_two_shot": "two", "medium_group": "wide", "wide_establishing": "wide",
                  "closeup_single": "cu", "medium_single": "medium", "insert_detail": "insert"}
    add("opening_shot", "creative", first["framing"] == framing_of.get(dna.opening_shot, first["framing"])
        and first["framing"] != framing_of.get(dna.avoid_opening),
        f"first shot framing '{first['framing']}' (DNA prefers {dna.opening_shot}, avoid {dna.avoid_opening})",
        "storyboard", "major")
    tb = [o for o in render_meta["overlays"] if o["kind"] == "title_bar"]
    need_tb = dna.title_bar.get("present_ratio", 0) >= 0.5
    add("title_bar_hook", "creative", (not need_tb) or (tb and tb[0]["start"] == 0 and tb[0]["end"] >= 3.5),
        f"title bar {'0-%.1fs' % tb[0]['end'] if tb else 'missing'}", "storyboard", "major")
    first_speech = tl["lines"][0]["start"] if tl["lines"] else 99
    add("fast_start", "creative", first_speech <= 0.6, f"first words at {first_speech:.2f}s", "voice", "major")
    # sync: the speaker should be on screen when their line starts
    by_line = {}
    for s in shots:
        if s.get("line_id") and s["line_id"] not in by_line:
            by_line[s["line_id"]] = s
    speaker_of = {l["id"]: l["speaker"] for l in script["lines"]}
    synced = sum(1 for lid, s in by_line.items() if speaker_of.get(lid) in s["characters"] or s["framing"] in ("two", "wide"))
    ratio = synced / max(1, len(by_line))
    add("speaker_visual_sync", "creative", ratio >= 0.9, f"{ratio:.0%} of lines open on the speaker", "storyboard", "major")
    cap_ok = all(any(l["start"] - 0.05 <= c["start"] <= l["end"] + 0.05 for l in tl["lines"] if l["id"] == c["line_id"])
                 for c in render_meta["captions"])
    add("caption_audio_sync", "creative", cap_ok, "captions start inside their spoken line", "edit", "major")
    beats = [b.name for b in dna.beats]
    seq = [beats.index(s["beat"]) for s in shots if s["beat"] in beats]
    add("scene_order", "creative", seq == sorted(seq), "beats in DNA order" if seq == sorted(seq) else "beats out of order",
        "script", "major")
    gaps = [b["start"] - a["end"] for a, b in zip(tl["lines"], tl["lines"][1:])]
    maxgap = max(gaps or [0])
    add("no_dead_time", "creative", maxgap <= 1.8, f"longest silence between lines {maxgap:.2f}s", "voice", "major")
    mean_shot = render_meta["mean_shot_s"]
    add("pacing", "creative", 0.65 * dna.mean_shot_s <= mean_shot <= 1.45 * dna.mean_shot_s,
        f"mean shot {mean_shot:.2f}s (DNA target {dna.mean_shot_s}s)", "storyboard", "major")
    longest = max(s["end"] - s["start"] for s in shots)
    add("no_static_stretch", "creative", longest <= 7.5, f"longest shot {longest:.1f}s", "storyboard", "minor", 0.5)
    rev = next((l for l in tl["lines"] if l["is_reveal"]), None)
    rp = rev["start"] / tl["total"] if rev else 0
    add("reveal_position", "creative", rev and abs(rp - dna.reveal_target_pct) <= 0.14,
        f"reveal at {rp:.0%} (DNA {dna.reveal_target_pct:.0%})", "script", "major")
    last = shots[-1]
    add("clear_ending", "creative", last.get("is_final") and last["framing"] == "cu",
        f"ends on {last['framing']} reaction", "storyboard", "major")
    fc = storyboard.get("fact_card") or {}
    vids = {c["id"] for c in research.get("verified_claims", [])}
    has_value = fc.get("claim_id") in vids and any(o["kind"] == "fact_card" for o in render_meta["overlays"])
    add("original_value", "creative", has_value or not st.automation.require_fact_anchor,
        "verified real-world fact shown with source" if has_value else "no sourced fact card", "script", "major")
    from .voice import effective_wpm
    errs = [e for e in validate_script(script, dna, research, effective_wpm()) if "claim" in e or "number" in e]
    add("facts_supported", "creative", not errs, "; ".join(errs[:3]) or "all factual lines cite verified claims",
        "script", "critical")
    # ---------------- originality & rights
    rights = assets_mod.rights_report(job_id)
    bad = [r["asset_key"] for r in rights if not r["allowed"]]
    add("asset_rights", "originality", rights and not bad, f"{len(rights)} assets registered; disallowed: {bad[:5]}",
        "assets", "critical")
    third = [r for r in rights if r["source"].startswith(("youtube", "download", "unknown"))]
    add("no_third_party_media", "originality", not third, f"{len(third)} third-party items", "assets", "critical")
    txt = script_text(script)
    prev = [p for p in previous_scripts(200) if p["job_id"] != job_id]
    sim = max([shingle_sim(txt, p["text"]) for p in prev] or [0])
    add("no_duplicate_script", "originality", sim < 0.25, f"max dialogue similarity {sim:.2f}", "script", "critical")
    tsim_ref = max([jaccard(script["moral"], t) for t in reference_titles()] or [0])
    tsim_own = max([jaccard(script["moral"], p["moral"] or "") for p in prev] or [0])
    add("no_copied_title", "originality", tsim_ref < 0.6 and tsim_own < 0.7,
        f"title similarity: reference {tsim_ref:.2f}, own {tsim_own:.2f}", "script", "critical")
    sig = structure_sig(script)
    same = [p["job_id"] for p in prev if p["structure_sig"] == sig]
    add("structure_variety", "originality", not same, f"identical structure to jobs {same[:3]}" if same else "unique",
        "script", "minor", 0.5)

    total_w = sum(c["weight"] for c in checks)
    score = round(100 * sum(c["weight"] for c in checks if c["passed"]) / total_w, 1)
    failed = [c for c in checks if not c["passed"]]
    critical = [c for c in failed if c["severity"] == "critical"]
    major = [c for c in failed if c["severity"] == "major"]
    passed = not critical and len(major) <= 1 and score >= 80
    back = min((c["send_back"] for c in failed if c["severity"] != "minor"), key=STAGE_ORDER.index, default=None)
    rep = {"score": score, "passed": passed, "send_back_to": None if passed else back,
           "rejections": [f"[{c['severity']}] {c['name']}: {c['detail']} -> {c['send_back']}" for c in failed],
           "checks": checks, "media": m}
    (workdir / "qc.json").write_text(json.dumps(rep, indent=1))
    log.info("QC score %.1f passed=%s failures=%d", score, passed, len(failed), extra={"job_id": job_id, "stage": "qc"})
    return rep
