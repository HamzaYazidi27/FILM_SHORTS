"""MODULE 4 - Storyboard generator.

Turns a script into shots, driven by the DNA's measured editing grammar:
  * mean shot length and range (DNA visual.mean_shot_s) - shorter in escalation/reveal
  * opening shot type (DNA: two-shot of protagonist + antagonist, never an insert)
  * shot/reverse-shot coverage: speaker close-ups with listener reaction cutaways
  * wide group shot when the authority/turn beat starts
  * accent transition (flash) only on the reveal, hard cuts elsewhere
  * ending on a reaction close-up (DNA ending_shot)
  * POV title bar for the first ~5 s (DNA title_bar)
Shots are anchored to script line ids + fractions so that real TTS timing (known later)
can re-time them exactly. Each shot references an asset key with a generation prompt;
assets are deduplicated so ~15-20 images cover a ~3 minute story."""
from __future__ import annotations

import hashlib
import random
import re

from ..dna import FormatDNA, load_dna

EXPRESSION_OF = {
    "neutral": "calm neutral expression", "angry": "angry, raised voice, furrowed brows",
    "smug": "smug condescending smirk", "shocked": "shocked, wide eyes, mouth slightly open",
    "sad": "hurt, humiliated, eyes glistening", "happy": "relieved warm smile",
    "firm": "calm, confident, firm gaze", "nervous": "nervous, uneasy, avoiding eye contact",
}
LISTENER_REACTION = {  # (beat, role) -> expression
    ("setup", "protagonist"): "nervous", ("setup", "antagonist"): "smug",
    ("escalation", "protagonist"): "sad", ("escalation", "antagonist"): "smug",
    ("authority_or_turn", "antagonist"): "smug", ("authority_or_turn", "protagonist"): "firm",
    ("reveal", "antagonist"): "shocked", ("reveal", "protagonist"): "firm", ("reveal", "authority"): "firm",
    ("comeuppance_and_moral", "antagonist"): "shocked", ("comeuppance_and_moral", "protagonist"): "happy",
}
BEAT_PACE = {"escalation": 0.85, "reveal": 0.8, "authority_or_turn": 0.95}
STYLE = ("cinematic film still, photorealistic, natural skin texture, shallow depth of field, 35mm lens, "
         "soft key light, vertical 9:16 composition, subject centred, no text, no watermark, no logo")
MAX_ASSETS = 20


def chunk_caption_words(words: list[dict], max_words: int = 4) -> list[dict]:
    """Group timed words into 1..max_words chunks, breaking after punctuation or long pauses.
    Returns chunks with line1/line2 split (line2 = emphasised last 1-2 words, as observed)."""
    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        brk = (len(cur) >= max_words or re.search(r"[,.!?;:]$", w["text"]) or
               (nxt is not None and nxt["start"] - w["end"] > 0.35) or nxt is None)
        if brk:
            chunks.append(cur)
            cur = []
    out = []
    for c in chunks:
        texts = [re.sub(r"[^\w'’?!-]", "", w["text"]).upper() for w in c]
        texts = [t for t in texts if t]
        if not texts:
            continue
        k = 1 if len(texts) <= 2 else 2
        out.append({"start": c[0]["start"], "end": c[-1]["end"], "line1": " ".join(texts[:-k]),
                    "line2": " ".join(texts[-k:]), "words": c})
    return out


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def build(script: dict, dna: FormatDNA | None = None) -> dict:
    dna = dna or load_dna()
    rnd = random.Random(_seed(script.get("title"), len(script["lines"])))
    chars = {c["id"]: c for c in script["characters"]}
    role_of = {c["id"]: c["role"] for c in script["characters"]}
    prot = next(c["id"] for c in script["characters"] if c["role"] == "protagonist")
    ant = next(c["id"] for c in script["characters"] if c["role"] == "antagonist")
    setting = script["setting"]["visual"]
    assets: dict[str, dict] = {}

    def asset(kind: str, who: list[str], expr: str, beat: str) -> str:
        if kind == "cu":
            key = f"{who[0]}_cu_{expr}"
            desc = (f"close-up portrait of {chars[who[0]]['look']}, {EXPRESSION_OF.get(expr, expr)}, "
                    f"head and shoulders, in {setting}")
            subject_seed = _seed(script.get("title"), who[0])
        elif kind == "medium":
            key = f"{who[0]}_med_{expr}"
            desc = (f"medium shot, waist up, of {chars[who[0]]['look']}, {EXPRESSION_OF.get(expr, expr)}, "
                    f"in {setting}")
            subject_seed = _seed(script.get("title"), who[0])
        elif kind == "two":
            key = f"two_{who[0]}_{who[1]}_{expr}"
            desc = (f"medium two-shot of two people facing each other in a tense confrontation: "
                    f"{chars[who[0]]['look']} and {chars[who[1]]['look']}, in {setting}")
            subject_seed = _seed(script.get("title"), "two")
        else:  # wide group
            key = f"wide_{expr}"
            people = "; ".join(chars[c]["look"] for c in who[:3])
            desc = f"wide shot of a tense scene with several people: {people}, in {setting}, onlookers in background"
            subject_seed = _seed(script.get("title"), "wide")
        if key not in assets:
            if len(assets) >= MAX_ASSETS:
                # reuse the closest existing asset of the same subject instead of generating more
                alt = next((k for k in assets if k.startswith(key.rsplit("_", 1)[0])), None) or \
                      next((k for k in assets if k.startswith(f"{who[0]}_cu_")), None)
                if alt:
                    return alt
            assets[key] = {"key": key, "type": "image", "framing": kind, "characters": who, "expression": expr,
                           "beat": beat, "prompt": f"{desc}. {STYLE}", "seed": subject_seed % 10_000_000}
        return key

    shots = []
    lines = script["lines"]
    reveal_idx = next(i for i, l in enumerate(lines) if l["is_reveal"])
    prev_beat = None
    for i, l in enumerate(lines):
        dur = l["est_end"] - l["est_start"]
        beat = l["beat"]
        pace = BEAT_PACE.get(beat, 1.0)
        target = dna.mean_shot_s * pace
        k = max(1, round(dur / target))
        k = min(k, max(1, int(dur / dna.shot_s_range[0])))
        listener = l.get("addressee") if l.get("addressee") in chars and l.get("addressee") != l["speaker"] else \
            (ant if l["speaker"] != ant else prot)
        bounds = [j / k for j in range(k + 1)]
        for j in range(k):
            framing, who, expr = "cu", [l["speaker"]], l["emotion"]
            transition = dna.transitions.get("default", "hard_cut")
            if i == 0 and j == 0:
                framing, who, expr = "two", [prot, ant], "tense"
            elif beat != prev_beat and j == 0 and beat == "authority_or_turn":
                framing, who, expr = "wide", list(chars), "tense"
            elif j % 2 == 1:  # reaction cutaway
                who = [listener]
                expr = LISTENER_REACTION.get((beat, role_of.get(listener, "")), "neutral")
            elif rnd.random() < 0.22 and beat in ("setup", "comeuppance_and_moral"):
                framing = "medium"
            if i == reveal_idx and j == 0:
                transition = (dna.transitions.get("accent") or ["flash_white"])[0]
            motion = "push_in" if framing in ("cu", "medium") else rnd.choice(["pan_left", "pan_right", "push_in"])
            if i == reveal_idx:
                motion = "push_in_fast"
            shots.append({
                "line_id": l["id"], "beat": beat, "frac_start": bounds[j], "frac_end": bounds[j + 1],
                "est_start": round(l["est_start"] + dur * bounds[j], 2), "est_end": round(l["est_start"] + dur * bounds[j + 1], 2),
                "framing": framing, "characters": who, "expression": expr,
                "asset_key": asset(framing, who, expr, beat),
                "camera": motion, "transition_in": transition,
                "sfx": ("impact" if (i == reveal_idx and j == 0) else ("whoosh" if transition != "hard_cut" else "")) or
                       (l.get("sfx") and j == 0 and "subtle:" + l["sfx"][:40]) or "",
                "narration": l["text"] if j == 0 else "", "speaker": l["speaker"],
            })
        prev_beat = beat
    # ending: hold on the final reaction close-up (DNA ending_shot)
    fr = script.get("final_reaction") if script.get("final_reaction") in chars else ant
    end_expr = "shocked" if role_of.get(fr) == "antagonist" else "happy"
    shots.append({"line_id": None, "beat": dna.beats[-1].name, "frac_start": 0, "frac_end": 1,
                  "est_start": lines[-1]["est_end"], "est_end": lines[-1]["est_end"] + 2.0, "hold_s": 2.0,
                  "framing": "cu", "characters": [fr], "expression": end_expr,
                  "asset_key": asset("cu", [fr], end_expr, "ending"), "camera": "push_in",
                  "transition_in": "hard_cut", "sfx": "", "narration": "", "speaker": None, "is_final": True})
    for n, s in enumerate(shots, 1):
        s["n"] = n
    title_s = dna.title_bar.get("duration_s", [4, 6])
    return {
        "shots": shots,
        "assets": list(assets.values()),
        "title_bar": {"text": script["pov_text"].upper(), "start": 0.0,
                      "end": float(sum(title_s) / 2 if isinstance(title_s, list) else title_s)}
        if dna.title_bar.get("present_ratio", 0) >= 0.5 else None,
        "fact_card": script.get("fact_card"),
        "caption_style": dna.captions,
        "grade": dna.grade,
        "dna_format": dna.name,
        "mean_shot_s_target": dna.mean_shot_s,
    }
