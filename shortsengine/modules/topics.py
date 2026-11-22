"""MODULE 1 - Topic discovery and scoring.

Candidates are original story premises for the DNA's active format. Signals:
  * Channel DNA priors per theme and setting (age-adjusted evidence from the reference channel)
  * learned adjustments from this system's own analytics (modules/analytics.py)
  * Wikipedia 'most read' feed (what audiences are reading now) as an optional trend hook
  * a verifiable real-world fact angle per premise, pre-checked against Wikipedia search
  * novelty vs. this system's previous topics and the reference channel's titles
  * overuse: themes already heavily used by the reference channel or by recent own videos
"""
from __future__ import annotations

import json
import math
import re
import time

from .. import db, logs
from ..config import load_settings
from ..dna import FormatDNA, load_dna, reference_titles
from ..providers import wikipedia
from ..providers.llm import get_llm
from ..textutil import fingerprint, jaccard

log = logs.get("topics")

WEIGHTS = {"relevance": 0.15, "novelty": 0.2, "audience": 0.2, "curiosity": 0.15, "reliability": 0.15, "visual": 0.15}

CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "moral": {"type": "string"},
                    "theme": {"type": "string"},
                    "setting": {"type": "string"},
                    "premise": {"type": "string"},
                    "reveal": {"type": "string"},
                    "fact_angle": {"type": "string"},
                    "fact_search_query": {"type": "string"},
                    "trend_link": {"type": "string"},
                    "curiosity": {"type": "number"},
                    "visual_potential": {"type": "number"},
                },
                "required": ["moral", "theme", "setting", "premise", "reveal", "fact_angle", "fact_search_query",
                             "trend_link", "curiosity", "visual_potential"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


def learned_adjustments() -> dict:
    return db.kv_get("strategy", {"themes": {}, "settings": {}, "hooks": {}})


def own_history(limit: int = 30) -> list[dict]:
    return db.q("SELECT title, premise, theme, setting, moral, status FROM topics WHERE status IN ('selected','used') "
                "ORDER BY id DESC LIMIT ?", (limit,))


def overuse_penalty(theme: str, dna: FormatDNA, history: list[dict]) -> float:
    recent = [h for h in history[:14]]
    share_own = sum(1 for h in recent if h["theme"] == theme) / max(1, len(recent))
    ref = next((t["n"] for t in dna.themes if t["theme"] == theme), 0)
    share_ref = ref / max(1, sum(t["n"] for t in dna.themes))
    return min(0.5, max(0.0, share_own - 0.25) * 1.2 + max(0.0, share_ref - 0.2) * 0.5)


def reliability_probe(query: str) -> tuple[float, list[dict]]:
    try:
        hits = wikipedia.search(query, limit=3)
    except Exception as e:
        log.warning("reliability probe failed: %s", e)
        return 0.0, []
    srcs = [{"title": h["title"], "url": f"https://en.wikipedia.org/wiki/{h['title'].replace(' ', '_')}",
             "snippet": h.get("snippet", "")[:200]} for h in hits]
    return (1.0 if len(hits) >= 2 else 0.6 if hits else 0.0), srcs


def score_candidate(c: dict, dna: FormatDNA, history: list[dict], ref_titles: list[str], learned: dict) -> dict:
    themes = {t["theme"] for t in dna.themes}
    settings = {s["setting"] for s in dna.settings_prior}
    relevance = 1.0 if c["theme"] in themes else 0.5
    prior = dna.theme_prior(c["theme"]) + 0.5 * dna.setting_prior(c["setting"])
    prior += learned.get("themes", {}).get(c["theme"], 0.0) + 0.5 * learned.get("settings", {}).get(c["setting"], 0.0)
    audience = 1 / (1 + math.exp(-prior))                         # logistic of log-view-residual evidence
    past_text = [f"{h['moral']} {h['premise']}" for h in history]
    sim_own = max([jaccard(c["moral"] + " " + c["premise"], p) for p in past_text] or [0.0])
    sim_ref = max([jaccard(c["moral"], t) for t in ref_titles] or [0.0])
    novelty = max(0.0, 1 - max(sim_own, sim_ref * 1.2))
    curiosity = min(1.0, max(0.0, float(c.get("curiosity", 0.5))))
    visual = min(1.0, max(0.0, float(c.get("visual_potential", 0.5)))) * (1.0 if c["setting"] in settings else 0.85)
    reliability = c.get("_reliability", 0.0)
    parts = {"relevance": relevance, "novelty": novelty, "audience": audience, "curiosity": curiosity,
             "reliability": reliability, "visual": visual}
    total = sum(WEIGHTS[k] * v for k, v in parts.items()) - overuse_penalty(c["theme"], dna, history)
    parts.update({"overuse_penalty": overuse_penalty(c["theme"], dna, history), "sim_own": sim_own, "sim_ref": sim_ref})
    return {"score": round(total, 4), "breakdown": {k: round(v, 3) for k, v in parts.items()}}


def discover(n: int = 8, job_id: int | None = None, use_trends: bool = True) -> list[dict]:
    dna = load_dna()
    history = own_history()
    learned = learned_adjustments()
    trends = wikipedia.most_read(1, 30) if use_trends else []
    theme_lines = "\n".join(f"- {t['theme']}: evidence {t['age_adj_median']:+.2f} (n={t['n']})" for t in dna.themes)
    setting_lines = "\n".join(f"- {s['setting']}: evidence {s['age_adj_median']:+.2f} (n={s['n']})" for s in dna.settings_prior)
    used = "\n".join(f"- {h['moral']} ({h['theme']}, {h['setting']})" for h in history[:20]) or "- none yet"
    trend_lines = "\n".join(f"- {t['title']}: {t['description']}" for t in trends[:25]) or "- unavailable"
    system = ("You are the head writer of an original short-form drama channel. You invent ORIGINAL fictional "
              "moral stories. You never retell existing films, TV episodes, viral videos or real private people, "
              "and never copy any existing creator's titles or scripts.")
    user = f"""Format (measured from a reference channel; reproduce only its abstract structure):
- {dna.duration_target:.0f}-second dialogue drama, no narrator; a person is judged, humiliated, accused or treated unfairly in a public place; a reveal (identity, evidence, authority, hidden status or skill) turns it around around {int(dna.reveal_target_pct*100)}% of the runtime; the antagonist faces consequences; the title is the moral as a short aphorism ({dna.moral_words.get('min', 4)}-{dna.moral_words.get('max', 14)} words, ideally ~{dna.moral_words.get('median', 8)}).

Themes with evidence (positive = above-median performance in the reference data; small samples, treat as priors):
{theme_lines}

Settings with evidence:
{setting_lines}

Already used by this channel (do not repeat these morals or premises):
{used}

Currently most-read Wikipedia articles (optional inspiration for a timely real-world fact angle; ignore if irrelevant or sensitive; never dramatise real tragedies or real private people):
{trend_lines}

Produce {n} diverse candidates. Each must have:
- moral: the lesson as a fresh aphorism (not a cliche copied from elsewhere)
- theme: one of the theme ids above (or a new snake_case id if clearly better)
- setting: one of the setting ids above (or a new snake_case id)
- premise: 2-3 sentences: who the underdog is, who mistreats them and how, what the twist is
- reveal: the specific twist
- fact_angle: ONE real, checkable fact the story can teach in its final seconds (a law, policy, safety rule, documented historical fact) - must be verifiable on English Wikipedia
- fact_search_query: a Wikipedia search query to verify it
- trend_link: the most-read article title used for inspiration, or "" if none
- curiosity: 0..1 how strongly the setup makes a viewer need to see the payoff
- visual_potential: 0..1 how clearly the status gap and the reveal can be shown in close-ups in a single location"""
    llm = get_llm()

    def validate(o):
        cs = o.get("candidates")
        if not isinstance(cs, list) or len(cs) < max(1, n // 2):
            raise ValueError(f"expected about {n} candidates")
        bad = [c["theme"] for c in cs if not re.fullmatch(r"[a-z][a-z_]{2,40}", c["theme"])] + \
              [c["setting"] for c in cs if not re.fullmatch(r"[a-z][a-z_]{2,40}", c["setting"])]
        if bad:
            raise ValueError(f"theme/setting ids must be short snake_case ids from the lists: {bad[:4]}")
        from collections import Counter
        sc = Counter(c["setting"] for c in cs)
        if len(cs) >= 4 and (len(sc) < 3 or sc.most_common(1)[0][1] > max(2, len(cs) // 3)):
            raise ValueError(f"settings are not diverse enough {dict(sc)}: use at least 3 different settings and "
                             f"at most {max(2, len(cs) // 3)} candidates per setting")

    out = llm.complete_json(system, user, CANDIDATE_SCHEMA, job_id=job_id, stage="topics", validate=validate,
                            max_tokens=12000)
    ref = reference_titles()
    saved = []
    for c in out["candidates"]:
        fp = fingerprint(c["moral"], c["premise"])
        if db.q1("SELECT id FROM topics WHERE fingerprint=?", (fp,)):
            log.info("duplicate topic skipped: %s", c["moral"])
            continue
        rel, srcs = reliability_probe(c["fact_search_query"])
        c["_reliability"] = rel
        sc = score_candidate(c, dna, history, ref, learned)
        sources = {"fact_search": srcs, "trend": next((t for t in trends if t["title"] == c.get("trend_link")), None)}
        tid = db.ex("INSERT INTO topics(fingerprint,title,premise,theme,setting,moral,fact_angle,score,score_breakdown,"
                    "sources,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (fp, c["moral"], c["premise"] + " Twist: " + c["reveal"], c["theme"], c["setting"], c["moral"],
                     json.dumps({"fact": c["fact_angle"], "query": c["fact_search_query"]}), sc["score"],
                     json.dumps(sc["breakdown"]), json.dumps(sources), "candidate", time.time()))
        saved.append({"id": tid, **c, **sc})
    log.info("discovered %d new topics", len(saved), extra={"job_id": job_id, "stage": "topics"})
    return sorted(saved, key=lambda x: -x["score"])


def select_topic(min_score: float | None = None) -> dict | None:
    """Pick the best unused candidate above the threshold and mark it selected."""
    ms = load_settings().automation.min_topic_score if min_score is None else min_score
    with db.tx() as c:
        r = c.execute("SELECT * FROM topics WHERE status='candidate' AND score>=? ORDER BY score DESC LIMIT 1",
                      (ms,)).fetchone()
        if not r:
            return None
        c.execute("UPDATE topics SET status='selected' WHERE id=?", (r["id"],))
    return dict(r)


def reject_topic(topic_id: int, reason: str) -> None:
    db.ex("UPDATE topics SET status='rejected', reject_reason=? WHERE id=?", (reason[:500], topic_id))
