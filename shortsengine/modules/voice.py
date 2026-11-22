"""MODULE 6 - Voice generation: per-character synthetic voices, emotion-aware delivery,
pronunciation dictionary, pause control, speed control, duration fitting to the DNA target,
and a sample-accurate dialogue track with word-level timings."""
from __future__ import annotations

import concurrent.futures as cf
import json
from pathlib import Path

import numpy as np

from .. import logs
from ..config import load_settings
from ..dna import load_dna
from ..providers.tts import get_tts
from .music import SR, read_wav, write_wav

log = logs.get("voice")

EMOTION_DELIVERY = {  # (rate %, pitch Hz)
    "neutral": (0, 0), "angry": (6, 2), "smug": (-4, -2), "shocked": (8, 4), "sad": (-8, -3),
    "happy": (2, 3), "firm": (-3, -2), "nervous": (5, 1),
}


class DurationOutOfRange(RuntimeError):
    def __init__(self, msg: str, words_delta: int):
        super().__init__(msg)
        self.words_delta = words_delta


def assign_voices(characters: list[dict]) -> dict[str, str]:
    v = load_settings().voice
    if v.provider == "kokoro":
        pools = {"male": list(v.kokoro_male_voices), "female": list(v.kokoro_female_voices)}
    else:
        pools = {"male": list(v.male_voices), "female": list(v.female_voices)}
    out = {}
    for c in characters:
        pool = pools.get(c["gender"]) or pools["male"]
        out[c["id"]] = pool.pop(0) if pool else (pools["male"] + pools["female"] or ["default"])[0]
    return out


def synth_lines(script: dict, workdir: Path, global_rate: int = 0, job_id: int | None = None) -> list[dict]:
    st = load_settings().voice
    tts = get_tts(st.provider)
    voices = assign_voices(script["characters"])
    ages = {c["id"]: c.get("age", 35) for c in script["characters"]}
    vdir = workdir / "voice"
    vdir.mkdir(parents=True, exist_ok=True)

    def one(line: dict) -> dict:
        r, p = EMOTION_DELIVERY.get(line["emotion"], (0, 0))
        if ages.get(line["speaker"], 35) >= 60:
            r, p = r - 6, p - 4
        rate = max(-40, min(40, r + global_rate))
        res = tts.synth(line["text"], voices[line["speaker"]], vdir, f"{line['id']}_r{rate:+d}", rate_pct=rate,
                        pitch_hz=p, lexicon=st.pronunciations, job_id=job_id)
        return {"id": line["id"], "speaker": line["speaker"], "voice": res.voice, "provider": res.provider,
                "wav": str(res.wav_path), "duration": res.duration, "rate": rate, "pitch": p,
                "words": [{"text": w.text, "start": w.start, "end": w.end} for w in res.words]}

    with cf.ThreadPoolExecutor(4) as ex:
        return list(ex.map(one, script["lines"]))


def layout(script: dict, synth: list[dict], gap_scale: float = 1.0) -> dict:
    st = load_settings().voice
    by_id = {s["id"]: s for s in synth}
    t = 0.15
    out, prev_beat = [], None
    for l in script["lines"]:
        s = by_id[l["id"]]
        if prev_beat is not None:
            t += st.line_gap_s * gap_scale
            if l["beat"] != prev_beat:
                t += st.beat_gap_s * gap_scale
            if l["is_reveal"]:
                t += st.pre_reveal_pause_s
        # trim leading/trailing TTS padding using word timings
        lead = max(0.0, (s["words"][0]["start"] - 0.05) if s["words"] else 0.0)
        tail_end = (s["words"][-1]["end"] + 0.12) if s["words"] else s["duration"]
        tail_end = min(s["duration"], max(tail_end, lead + 0.2))
        start = t
        out.append({"id": l["id"], "speaker": l["speaker"], "beat": l["beat"], "is_reveal": l["is_reveal"],
                    "wav": s["wav"], "clip_in": round(lead, 3), "clip_out": round(tail_end, 3),
                    "start": round(start, 3), "end": round(start + tail_end - lead, 3),
                    "words": [{"text": w["text"], "start": round(start + w["start"] - lead, 3),
                               "end": round(start + w["end"] - lead, 3)} for w in s["words"]]})
        t = start + tail_end - lead
        prev_beat = l["beat"]
    return {"lines": out, "speech_end": round(t, 3)}


def render_dialogue(tl: dict, total: float, path: Path) -> None:
    buf = np.zeros(int(total * SR) + SR, dtype=np.float32)
    for l in tl["lines"]:
        x = read_wav(Path(l["wav"]))[int(l["clip_in"] * SR): int(l["clip_out"] * SR)]
        fade = min(len(x) // 4, int(0.01 * SR))
        if fade:
            x = x.copy()
            x[:fade] *= np.linspace(0, 1, fade)
            x[-fade:] *= np.linspace(1, 0, fade)
        s = int(l["start"] * SR)
        buf[s:s + len(x)] += x[: len(buf) - s]
    write_wav(path, buf[: int(total * SR)])


def run(script: dict, workdir: Path, job_id: int | None = None) -> dict:
    dna = load_dna()
    vs = load_settings().video
    end_hold = 2.0  # final reaction shot (storyboard)
    lo, hi = (vs.duration_override_s * 0.92, vs.duration_override_s * 1.04) if vs.duration_override_s else \
        (dna.duration_min, dna.duration_max)
    rate, gap_scale = 0, 1.0
    synth = synth_lines(script, workdir, rate, job_id)
    tl = layout(script, synth)
    for _ in range(3):
        total = tl["speech_end"] + end_hold
        if total > hi and rate < 15:
            rate = min(15, rate + max(3, int((total / hi - 1) * 100) + 1))
            synth = synth_lines(script, workdir, rate, job_id)
            tl = layout(script, synth, gap_scale)
        elif total < lo and gap_scale < 1.6:
            gap_scale = min(1.6, gap_scale + (lo - total) / max(1, len(script["lines"])) / 0.3 + 0.1)
            tl = layout(script, synth, gap_scale)
        else:
            break
    total = round(tl["speech_end"] + end_hold, 3)
    wps = sum(len(l["text"].split()) for l in script["lines"]) / max(1, tl["speech_end"])
    if total > hi:
        raise DurationOutOfRange(f"dialogue runs {total:.1f}s > {hi:.0f}s even at +{rate}% speed",
                                 -int((total - hi) * wps) - 5)
    if total < lo:
        raise DurationOutOfRange(f"dialogue runs {total:.1f}s < {lo:.0f}s even with wider pauses",
                                 int((lo - total) * wps) + 5)
    tl.update({"total": total, "global_rate": rate, "gap_scale": round(gap_scale, 3),
               "voices": assign_voices(script["characters"])})
    render_dialogue(tl, total, workdir / "dialogue.wav")
    (workdir / "timeline.json").write_text(json.dumps(tl, indent=1))
    log.info("voice: %d lines, %.1fs total (rate %+d%%, gaps x%.2f)", len(tl["lines"]), total, rate, gap_scale,
             extra={"job_id": job_id, "stage": "voice"})
    return tl
