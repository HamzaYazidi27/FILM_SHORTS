"""MODULE 3 - Script generation.

Generates an original dialogue script that follows the DNA's abstract narrative structure
(beat order and proportions, reveal position, duration, title/moral conventions), with three
hook options scored against the DNA evidence. Facts may only appear when they reference a
verified research claim. Validation feedback is returned to the model on failure."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .. import db, logs
from ..config import load_settings
from ..dna import FormatDNA, load_dna, reference_titles
from ..providers.llm import get_llm
from ..textutil import factual_numbers, jaccard, numbers, shingle_sim

log = logs.get("script")

EMOTIONS = ["neutral", "angry", "smug", "shocked", "sad", "happy", "firm", "nervous"]

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "hooks": {"type": "array", "items": {
            "type": "object",
            "properties": {"moral": {"type": "string"}, "grammar": {"type": "string",
                           "enum": ["general_maxim", "question", "narrative_teaser", "imperative"]},
                           "rationale": {"type": "string"}},
            "required": ["moral", "grammar", "rationale"], "additionalProperties": False}},
        "setting": {"type": "object", "properties": {"id": {"type": "string"}, "visual": {"type": "string"}},
                    "required": ["id", "visual"], "additionalProperties": False},
        "characters": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"}, "name": {"type": "string"},
                "gender": {"type": "string", "enum": ["male", "female"]},
                "age": {"type": "integer"},
                "role": {"type": "string", "enum": ["protagonist", "antagonist", "authority", "bystander"]},
                "look": {"type": "string"}, "voice_style": {"type": "string"}},
            "required": ["id", "name", "gender", "age", "role", "look", "voice_style"], "additionalProperties": False}},
        "lines": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "beat": {"type": "string"}, "speaker": {"type": "string"}, "addressee": {"type": "string"},
                "text": {"type": "string"}, "emotion": {"type": "string", "enum": EMOTIONS},
                "is_reveal": {"type": "boolean"}, "claim_id": {"type": "string"},
                "visual_note": {"type": "string"}, "sfx": {"type": "string"}},
            "required": ["beat", "speaker", "addressee", "text", "emotion", "is_reveal", "claim_id", "visual_note", "sfx"],
            "additionalProperties": False}},
        "final_reaction": {"type": "string"},
        "fact_card": {"type": "object", "properties": {"claim_id": {"type": "string"}, "text": {"type": "string"}},
                      "required": ["claim_id", "text"], "additionalProperties": False},
    },
    "required": ["hooks", "setting", "characters", "lines", "final_reaction", "fact_card"],
    "additionalProperties": False,
}


class ScriptRejected(RuntimeError):
    pass


def target_words(dna: FormatDNA, wpm: int, n_lines_guess: int | None = None) -> int:
    """Words needed to fill the target duration, accounting for inter-line and beat pauses."""
    st = load_settings()
    dur = st.video.duration_override_s or dna.duration_target
    words = dur * wpm / 60
    for _ in range(4):  # fixed-point: lines ~ words / 9
        lines = n_lines_guess or max(6, words / 9)
        gaps = lines * st.voice.line_gap_s + len(dna.beats) * st.voice.beat_gap_s + st.voice.pre_reveal_pause_s + 2.2
        words = (dur - gaps) * wpm / 60
    return int(words)


def score_hook(h: dict, dna: FormatDNA, history_morals: list[str], ref_titles: list[str]) -> dict:
    n = len(h["moral"].split())
    mw = dna.moral_words
    length = 1.0 if mw.get("min", 4) <= n <= mw.get("max", 14) else 0.4
    length *= 1 - min(0.5, abs(n - mw.get("median", 8)) / 20)
    pref = dna.title_grammar_preference
    grammar = 1 - (pref.index(h["grammar"]) / max(1, len(pref))) * 0.6 if h["grammar"] in pref else 0.5
    novelty = 1 - max([jaccard(h["moral"], m) for m in history_morals + ref_titles] or [0])
    total = 0.35 * length + 0.25 * grammar + 0.4 * novelty
    return {"length": round(length, 3), "grammar": round(grammar, 3), "novelty": round(novelty, 3), "score": round(total, 3)}


def validate_script(s: dict, dna: FormatDNA, research: dict, wpm: int) -> list[str]:
    errs: list[str] = []
    beats = [b.name for b in dna.beats]
    chars = {c["id"] for c in s.get("characters", [])}
    roles = [c["role"] for c in s.get("characters", [])]
    if len(s.get("hooks", [])) != 3:
        errs.append("exactly 3 hook options are required")
    if not (2 <= len(chars) <= 4):
        errs.append("use 2-4 characters")
    if "protagonist" not in roles or "antagonist" not in roles:
        errs.append("need one protagonist and one antagonist")
    lines = s.get("lines", [])
    seen = [l["beat"] for l in lines]
    order = [b for i, b in enumerate(seen) if i == 0 or seen[i - 1] != b]
    if [b for b in order if b in beats] != [b for b in beats if b in order] or any(b not in beats for b in seen):
        errs.append(f"beats must use exactly these ids in this order without going back: {beats}")
    missing = [b for b in beats if b not in seen]
    if missing:
        errs.append(f"missing beats: {missing}")
    for i, l in enumerate(lines):
        if l["speaker"] not in chars:
            errs.append(f"line {i}: unknown speaker {l['speaker']}")
        if len(l["text"].split()) > 30:
            errs.append(f"line {i}: too long (max 30 words)")
    reveals = [i for i, l in enumerate(lines) if l["is_reveal"]]
    if len(reveals) != 1:
        errs.append("exactly one line must have is_reveal=true")
    words = sum(len(l["text"].split()) for l in lines)
    tw = target_words(dna, wpm, len(lines) or None)
    dur = load_settings().video.duration_override_s or dna.duration_target
    if not (0.75 * tw <= words <= 1.3 * tw):  # the voice stage enforces the exact duration afterwards
        errs.append(f"dialogue has {words} words; it must have {int(0.85*tw)}-{int(1.15*tw)} words to fill "
                    f"{dur:.0f} s")
    if reveals and lines:
        cum = 0
        for i, l in enumerate(lines):
            if i == reveals[0]:
                break
            cum += len(l["text"].split())
        pct = cum / max(1, words)
        if not (dna.reveal_target_pct - 0.14 <= pct <= dna.reveal_target_pct + 0.1):
            errs.append(f"the reveal line starts at {pct:.0%} of the dialogue; it must start near "
                        f"{dna.reveal_target_pct:.0%}")
    verified = {c["id"]: c for c in research.get("verified_claims", [])}
    for i, l in enumerate(lines):
        cid = l.get("claim_id") or ""
        if cid and cid not in verified:
            errs.append(f"line {i}: claim_id {cid} is not a verified claim")
        if cid and factual_numbers(l["text"]) - numbers(verified.get(cid, {}).get("claim", "") + verified.get(cid, {}).get("quote", "")):
            errs.append(f"line {i}: contains numbers not supported by claim {cid}")
        if not cid and factual_numbers(l["text"]):
            errs.append(f"line {i}: factual-looking numbers {sorted(factual_numbers(l['text']))} need a claim_id (or remove them)")
    fc = s.get("fact_card", {})
    if verified and fc.get("claim_id") not in verified:
        errs.append("fact_card.claim_id must reference a verified claim")
    if fc.get("claim_id") in verified and factual_numbers(fc.get("text", "")) - numbers(verified[fc["claim_id"]]["quote"]):
        errs.append("fact_card text contains numbers not in its source quote")
    if lines and s.get("final_reaction") not in chars:
        errs.append("final_reaction must be a character id")
    for h in s.get("hooks", []):
        if max([jaccard(h["moral"], t) for t in reference_titles()] or [0]) > 0.6:
            errs.append(f"hook '{h['moral']}' is too close to an existing reference title; write an original one")
    return errs


def previous_scripts(limit: int = 50) -> list[dict]:
    return db.q("SELECT job_id, title, moral, text, structure_sig FROM scripts ORDER BY created_at DESC LIMIT ?", (limit,))


def script_text(s: dict) -> str:
    return " ".join(l["text"] for l in s["lines"])


def structure_sig(s: dict) -> str:
    roles = {c["id"]: c["role"] for c in s["characters"]}
    return f"{s['setting']['id']}|" + ",".join(f"{l['beat'][:3]}:{roles.get(l['speaker'], '?')[:3]}" for l in s["lines"])


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "hooks": SCRIPT_SCHEMA["properties"]["hooks"],
        "setting": SCRIPT_SCHEMA["properties"]["setting"],
        "characters": SCRIPT_SCHEMA["properties"]["characters"],
        "beat_plan": {"type": "array", "items": {
            "type": "object",
            "properties": {"beat": {"type": "string"}, "summary": {"type": "string"}},
            "required": ["beat", "summary"], "additionalProperties": False}},
        "final_reaction": {"type": "string"},
        "fact_card": SCRIPT_SCHEMA["properties"]["fact_card"],
    },
    "required": ["hooks", "setting", "characters", "beat_plan", "final_reaction", "fact_card"],
    "additionalProperties": False,
}
BEAT_SCHEMA = {
    "type": "object",
    "properties": {"lines": SCRIPT_SCHEMA["properties"]["lines"]},
    "required": ["lines"], "additionalProperties": False,
}
SYSTEM = ("You write ORIGINAL short vertical dramas with natural, punchy spoken dialogue. You never reuse lines, "
          "jokes, catchphrases or plots from existing films, TV shows, skits or creators. Characters are fictional. "
          "Avoid stereotypes: status, wealth, age, disability or appearance are what is misjudged - never make "
          "ethnicity or religion the punchline. No profanity beyond mild words; no graphic violence. Output JSON only.")


def _claims_text(research: dict) -> str:
    return "\n".join(f"- {c['id']}: {c['claim']} (source: {c['page_title']})"
                     for c in research.get("verified_claims", [])) or "- none (do not state any real-world facts)"


def _plan(topic: dict, research: dict, dna: FormatDNA, prev: list[dict], feedback: str, job_id) -> dict:
    beats = "\n".join(f"- {b.name}: {int(b.start_pct*100)}-{int(b.end_pct*100)}% of runtime. {b.notes}" for b in dna.beats)
    avoid = "\n".join(f"- {p['moral']}" for p in prev) or "- none"
    user = f"""Plan an original {dna.duration_target:.0f}-second dialogue drama (no narrator).

STORY  Moral: {topic['moral']} | Theme: {topic['theme']} | Setting: {topic['setting']}
Premise: {topic['premise']}

BEATS (use exactly these ids, in this order, one entry each in beat_plan):
{beats}
The twist is revealed at the START of the 'reveal' beat (~{int(dna.reveal_target_pct*100)}% of the runtime).

VERIFIED FACTS (the only real-world facts allowed):
{_claims_text(research)}
fact_card: pick ONE verified claim id and a short on-screen version (max 22 words, no numbers that are not in the claim).

HOOKS: 3 different moral aphorisms usable as the title and an on-screen "POV:" bar, {dna.moral_words.get('min',4)}-{dna.moral_words.get('max',14)} words (ideal ~{dna.moral_words.get('median',8)}); grammar one of general_maxim/question/narrative_teaser/imperative; different from:
{avoid}

CHARACTERS: 2-4, ids lowercase without spaces, exactly one protagonist and one antagonist (optional authority, bystander); 'look' = precise consistent visual description (age, build, hair, clothing, distinctive item); voice_style.
setting.id = "{topic['setting']}", setting.visual = precise description of the single location.
final_reaction = id of the character whose reaction close-up ends the video (usually the antagonist).
beat_plan: for each beat, a 2-3 sentence summary of what happens and what is said.
{('FEEDBACK FROM PREVIOUS ATTEMPT:' + chr(10) + feedback) if feedback else ''}"""

    def validate(o):
        errs = []
        if len(o.get("hooks", [])) != 3:
            errs.append("exactly 3 hooks")
        roles = [c["role"] for c in o.get("characters", [])]
        if roles.count("protagonist") != 1 or roles.count("antagonist") != 1 or not 2 <= len(roles) <= 4:
            errs.append("2-4 characters with exactly one protagonist and one antagonist")
        ids = [c["id"] for c in o.get("characters", [])]
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{1,20}", i) for i in ids):
            errs.append("character ids must be lowercase without spaces")
        if [b["beat"] for b in o.get("beat_plan", [])] != [b.name for b in dna.beats]:
            errs.append(f"beat_plan beats must be exactly {[b.name for b in dna.beats]}")
        if o.get("final_reaction") not in ids:
            errs.append("final_reaction must be a character id")
        vids = {c["id"] for c in research.get("verified_claims", [])}
        if vids and o.get("fact_card", {}).get("claim_id") not in vids:
            errs.append(f"fact_card.claim_id must be one of {sorted(vids)}")
        for h in o.get("hooks", []):
            if max([jaccard(h["moral"], t) for t in reference_titles()] or [0]) > 0.6:
                errs.append(f"hook '{h['moral']}' is too close to an existing title")
        if errs:
            raise ValueError("; ".join(errs))

    return get_llm().complete_json(SYSTEM, user, PLAN_SCHEMA, job_id=job_id, stage="script", validate=validate,
                                   max_tokens=8000, retries=3)


def _beat_lines(plan: dict, beat, words: int, so_far: list[dict], research: dict, dna: FormatDNA, topic: dict,
                job_id, feedback: str) -> list[dict]:
    chars = "\n".join(f"- {c['id']} ({c['role']}, {c['gender']}, {c['age']}): {c['name']}; {c['voice_style']}"
                      for c in plan["characters"])
    ids = [c["id"] for c in plan["characters"]]
    summary = next(b["summary"] for b in plan["beat_plan"] if b["beat"] == beat.name)
    recent = "\n".join(f"{l['speaker']}: {l['text']}" for l in so_far[-14:]) or "(this is the opening)"
    is_reveal_beat = beat.name == "reveal"
    fc = plan.get("fact_card", {})
    rules = [f"Write {max(4, round(words / 9))}-{max(6, round(words / 6))} lines totalling about {words} spoken words "
             f"(allowed {int(words*0.85)}-{int(words*1.2)}).",
             "Lines of 3-18 words, never more than 30. Natural spoken English; vary who speaks.",
             f"speaker and addressee must be ids from: {ids}.",
             "claim_id = \"\" unless the line states a verified fact; is_reveal=false unless told otherwise.",
             "visual_note = what the camera shows; sfx = a subtle sound or \"\"."]
    if not so_far:
        rules.append("The first line drops straight into the conflict; protagonist and antagonist are both present.")
    if is_reveal_beat:
        rules.append("The FIRST line of this beat is the twist itself: set is_reveal=true on it and on no other line.")
    if beat.name in ("reveal", "comeuppance_and_moral") and fc.get("claim_id"):
        rules.append(f"One line in this beat states the verified fact {fc['claim_id']} naturally and sets "
                     f"claim_id=\"{fc['claim_id']}\" (only if not already stated earlier).")
    if beat.name == dna.beats[-1].name:
        rules.append("End with the antagonist facing the consequence and a short dignified final line from the "
                     "protagonist that echoes the moral. No calls to subscribe.")
    user = f"""Story moral: {plan['hooks'][0]['moral']} | Setting: {plan['setting']['visual']}
Premise: {topic['premise']}
Characters:
{chars}
Verified facts allowed:
{_claims_text(research)}

Dialogue so far (most recent last):
{recent}

Now write ONLY the '{beat.name}' beat: {summary}
Rules:
""" + "\n".join("- " + r for r in rules) + (f"\nFix: {feedback}" if feedback else "")
    vids = {c["id"]: c for c in research.get("verified_claims", [])}
    alias = {}
    for c in plan["characters"]:
        alias[c["id"].lower()] = c["id"]
        alias[c["name"].lower()] = c["id"]
        alias[c["name"].split()[0].lower()] = c["id"]
        alias[c["role"].lower()] = c["id"]

    def repair(ls: list[dict]) -> list[dict]:
        for l in ls:
            l["speaker"] = alias.get(str(l.get("speaker", "")).lower().strip(), l.get("speaker"))
            ad = str(l.get("addressee", "")).lower().strip()
            l["addressee"] = alias.get(ad, "") if ad else ""
            l["text"] = re.sub(r"\s+", " ", l.get("text", "")).strip()
            if l.get("emotion") not in EMOTIONS:
                l["emotion"] = "neutral"
        return [l for l in ls if l["text"]]

    lo_w, hi_w = int(min(words * 0.7, words - 6)), int(max(words * 1.45, words + 8))

    def check(ls: list[dict], need_words: bool) -> list[str]:
        errs = []
        n = sum(len(l["text"].split()) for l in ls)
        if need_words and not lo_w <= n <= hi_w:
            errs.append(f"this beat has {n} words; write {int(words*0.85)}-{int(words*1.2)} words")
        for i, l in enumerate(ls):
            if l["speaker"] not in ids:
                errs.append(f"line {i}: speaker must be one of {ids}")
            if len(l["text"].split()) > 30:
                errs.append(f"line {i}: max 30 words")
            cid = l.get("claim_id") or ""
            if cid and cid not in vids:
                errs.append(f"line {i}: unknown claim_id {cid}")
            elif cid and factual_numbers(l["text"]) - numbers(vids[cid]["claim"] + " " + vids[cid]["quote"]):
                errs.append(f"line {i}: numbers not supported by {cid}")
            if not cid and factual_numbers(l["text"]):
                errs.append(f"line {i}: remove factual-looking numbers {sorted(factual_numbers(l['text']))}")
        rv = [l for l in ls if l["is_reveal"]]
        if is_reveal_beat and (len(rv) != 1 or not ls or not ls[0]["is_reveal"]):
            errs.append("the first line (and only it) must have is_reveal=true")
        if not is_reveal_beat and rv:
            errs.append("is_reveal must be false in this beat")
        return errs

    def validate(o):
        o["lines"] = repair(o.get("lines", []))
        n = sum(len(l["text"].split()) for l in o["lines"])
        errs = check(o["lines"], need_words=n > hi_w)  # too short is fixed by continuation below
        if errs:
            raise ValueError("; ".join(errs[:8]))

    def validate_continuation(o):
        o["lines"] = [dict(l, is_reveal=False) for l in repair(o.get("lines", []))]
        errs = check(o["lines"], need_words=False)
        errs = [e for e in errs if "is_reveal" not in e]
        if errs:
            raise ValueError("; ".join(errs[:6]))

    llm = get_llm()
    out = llm.complete_json(SYSTEM, user, BEAT_SCHEMA, job_id=job_id, stage="script", validate=validate,
                            max_tokens=6000, retries=3)
    lines = out["lines"]
    for _ in range(2):  # continuation calls when a beat came back short
        n = sum(len(l["text"].split()) for l in lines)
        if n >= max(lo_w, words * 0.8):
            break
        more = words - n
        cont = user.replace(f"Dialogue so far (most recent last):\n{recent}",
                            "Dialogue so far (most recent last):\n" + "\n".join(
                                f"{l['speaker']}: {l['text']}" for l in (so_far + lines)[-14:]))
        cont += (f"\n\nCONTINUE this same beat: write {max(2, round(more / 9))}-{max(3, round(more / 6))} MORE lines "
                 f"(about {more} words) that follow directly from the last line. is_reveal=false on all of them.")
        extra = llm.complete_json(SYSTEM, cont, BEAT_SCHEMA, job_id=job_id, stage="script",
                                  validate=validate_continuation, max_tokens=4000, retries=2)
        for l in extra["lines"]:  # take continuation lines only while within budget
            if sum(len(x["text"].split()) for x in lines) + len(l["text"].split()) > hi_w:
                break
            lines.append(l)
    errs = check(lines, need_words=True)
    if errs:
        raise ScriptRejected(f"beat {beat.name}: " + "; ".join(errs[:6]))
    for l in lines:
        l["beat"] = beat.name
    return lines


def generate(topic: dict, research: dict, job_id: int | None = None, feedback: str = "",
             checkpoint: "Path | None" = None) -> dict:
    """Staged generation: a plan call, then one call per beat with word budgets from the DNA's
    beat proportions. Works with large hosted models and small local models alike."""
    dna = load_dna()
    st = load_settings()
    wpm = st.voice.words_per_minute
    prev = previous_scripts(10)
    ck = json.loads(checkpoint.read_text()) if checkpoint and checkpoint.exists() else {}
    if ck.get("feedback") != feedback:
        ck = {"feedback": feedback}   # new feedback invalidates partial work
    plan = ck.get("plan") or _plan(topic, research, dna, prev, feedback, job_id)
    ck["plan"] = plan
    if checkpoint:
        checkpoint.write_text(json.dumps(ck))
    total_words = target_words(dna, wpm)
    m = re.search(r"(Add|Remove) about (\d+) words", feedback or "")
    if m:  # duration feedback from the voice stage
        total_words += int(m.group(2)) * (1 if m.group(1) == "Add" else -1)
    lines: list[dict] = []
    done = ck.setdefault("beats", {})
    for b in dna.beats:
        share = b.end_pct - b.start_pct
        if b.name not in done:
            done[b.name] = _beat_lines(plan, b, max(6, round(total_words * share)), lines, research, dna, topic, job_id, "")
            if checkpoint:
                checkpoint.write_text(json.dumps(ck))
        lines += done[b.name]
    s = {k: plan[k] for k in ("hooks", "setting", "characters", "final_reaction", "fact_card")}
    s["beat_plan"] = plan["beat_plan"]
    s["lines"] = lines
    if not any(l.get("claim_id") == s["fact_card"]["claim_id"] for l in lines) and s["fact_card"].get("claim_id"):
        s["fact_card_spoken"] = False   # shown on screen only
    errs = validate_script(s, dna, research, wpm)
    if errs:
        if checkpoint and checkpoint.exists():
            checkpoint.unlink()  # whole-script problem: start over next attempt
        raise ScriptRejected("; ".join(errs[:10]))
    txt = script_text(s)
    for p in previous_scripts():
        if shingle_sim(txt, p["text"]) > 0.25:
            raise ScriptRejected(f"dialogue too similar to previous script (job {p['job_id']})")
    ref = reference_titles()
    history_morals = [p["moral"] for p in prev if p.get("moral")]
    for h in s["hooks"]:
        h["score"] = score_hook(h, dna, history_morals, ref)
    best = max(range(3), key=lambda i: s["hooks"][i]["score"]["score"])
    s["recommended_hook"] = best
    moral = s["hooks"][best]["moral"].strip().rstrip(".")
    s["moral"] = moral
    s["pov_text"] = f"POV: {moral}"
    s["title"] = dna.title_pattern.replace("{moral}", moral) if "{moral}" in dna.title_pattern else moral
    t = 0.0
    for i, l in enumerate(s["lines"]):
        l["id"] = f"L{i:03d}"
        d = len(l["text"].split()) * 60 / wpm + 0.25
        l["est_start"], l["est_end"] = round(t, 2), round(t + d, 2)
        t += d + st.voice.line_gap_s
    s["est_duration"] = round(t, 2)
    s["topic_id"] = topic.get("id")
    s["word_count"] = sum(len(l["text"].split()) for l in s["lines"])
    s["created_at"] = time.time()
    s["originality"] = {
        "max_similarity_prev_scripts": round(max([shingle_sim(txt, p["text"]) for p in previous_scripts()] or [0]), 3),
        "max_title_similarity_reference": round(max([jaccard(moral, r) for r in ref] or [0]), 3),
    }
    return s


def register(job_id: int, s: dict) -> None:
    db.ex("INSERT OR REPLACE INTO scripts(job_id,title,moral,hook,text,structure_sig,created_at) VALUES(?,?,?,?,?,?,?)",
          (job_id, s["title"], s["moral"], s["pov_text"], script_text(s), structure_sig(s), time.time()))


def clean_caption_text(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def dumps(s: dict) -> str:
    return json.dumps(s, indent=1, ensure_ascii=False)
