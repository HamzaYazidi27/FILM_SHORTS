"""Phase 5 - analytics and continuous learning.

Fetches statistics for this system's own uploads (YouTube Data API) and, when authorised,
average view duration/percentage and the audience-retention curve (YouTube Analytics API).
Learning rules (deliberately conservative):
  * performance = age-adjusted score: residual of log(views) on log(age) - same method as the
    reference analysis - so young and old videos are comparable
  * a feature's adjustment = mean residual of its group shrunk by n/(n+K) (K=5): one video
    can never swing the strategy; adjustments are capped to +-0.6 and need n>=2
  * results are associations, not causes; the report says so
  * exploration: features with few samples are proposed as 'test next'
Adjustments are stored in kv('strategy') and consumed by topic scoring."""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict

from .. import db, logs
from ..dna import load_dna

log = logs.get("analytics")
K_SHRINK = 5
CAP = 0.6


def fetch(client=None) -> int:
    """Pull statistics for all uploaded videos. Returns number of snapshots stored."""
    from .publish import youtube
    vids = db.q("SELECT id, youtube_id FROM videos WHERE youtube_id IS NOT NULL")
    if not vids:
        return 0
    yt = client or youtube()
    n = 0
    for i in range(0, len(vids), 50):
        batch = vids[i:i + 50]
        resp = yt.videos().list(part="statistics,contentDetails", id=",".join(v["youtube_id"] for v in batch)).execute()
        stats = {it["id"]: it for it in resp.get("items", [])}
        for v in batch:
            it = stats.get(v["youtube_id"])
            if not it:
                continue
            s = it.get("statistics", {})
            avd, avp, curve = retention(v["youtube_id"])
            db.ex("INSERT INTO analytics(video_id,fetched_at,views,likes,comments,avg_view_duration_s,avg_view_pct,raw) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (v["id"], time.time(), int(s.get("viewCount", 0)), int(s.get("likeCount", 0)),
                   int(s.get("commentCount", 0)), avd, avp, json.dumps({"retention": curve})))
            n += 1
    log.info("analytics: stored %d snapshots", n)
    return n


def retention(youtube_id: str):
    """Average view duration/percentage + retention curve; None if the Analytics API is unavailable."""
    try:
        from googleapiclient.discovery import build
        from .publish import credentials
        ya = build("youtubeAnalytics", "v2", credentials=credentials(), cache_discovery=False)
        end = time.strftime("%Y-%m-%d")
        r = ya.reports().query(ids="channel==MINE", startDate="2020-01-01", endDate=end,
                               metrics="averageViewDuration,averageViewPercentage", filters=f"video=={youtube_id}").execute()
        row = (r.get("rows") or [[None, None]])[0]
        c = ya.reports().query(ids="channel==MINE", startDate="2020-01-01", endDate=end, metrics="audienceWatchRatio",
                               dimensions="elapsedVideoTimeRatio", filters=f"video=={youtube_id}").execute()
        return row[0], row[1], c.get("rows") or []
    except Exception as e:  # analytics scope missing / not enough data yet
        log.info("retention unavailable for %s: %s", youtube_id, e)
        return None, None, []


def latest_rows() -> list[dict]:
    return db.q("""SELECT v.id, v.title, v.duration_s, v.features, v.published_at, a.views, a.likes, a.comments,
                          a.avg_view_duration_s, a.avg_view_pct, a.raw, a.fetched_at
                   FROM videos v JOIN analytics a ON a.id = (SELECT MAX(id) FROM analytics WHERE video_id=v.id)
                   WHERE v.published_at IS NOT NULL""")


def age_adjusted(rows: list[dict]) -> list[dict]:
    pts = [(math.log(max(0.5, (r["fetched_at"] - r["published_at"]) / 86400)), math.log(max(1, r["views"] or 0)), r)
           for r in rows]
    if len(pts) < 3:
        return [dict(r, score=0.0) for _, _, r in pts]
    mx = sum(p[0] for p in pts) / len(pts)
    my = sum(p[1] for p in pts) / len(pts)
    den = sum((p[0] - mx) ** 2 for p in pts) or 1e-9
    b = sum((p[0] - mx) * (p[1] - my) for p in pts) / den
    a = my - b * mx
    return [dict(r, score=y - (a + b * x)) for x, y, r in pts]


def drop_beats(curve: list, features: dict) -> list[str]:
    """Map the steepest retention drops onto narrative beats using the video's beat timing."""
    beats = features.get("beat_spans") or []
    if len(curve) < 5 or not beats:
        return []
    drops = sorted(((curve[i][1] - curve[i + 1][1], curve[i][0]) for i in range(len(curve) - 1)), reverse=True)[:3]
    out = []
    for _, pos in drops:
        for b in beats:
            if b["start_pct"] <= pos < b["end_pct"]:
                out.append(b["beat"])
    return out


def learn() -> dict:
    rows = age_adjusted(latest_rows())
    groups: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    drops = defaultdict(int)
    for r in rows:
        f = json.loads(r["features"] or "{}")
        for key in ("theme", "setting", "hook_grammar", "duration_bucket", "pace_bucket", "opening_shot"):
            if f.get(key):
                groups[key][f[key]].append(r["score"])
        curve = json.loads(r["raw"] or "{}").get("retention") or []
        for b in drop_beats(curve, f):
            drops[b] += 1
    effects = {}
    for key, vals in groups.items():
        effects[key] = {}
        for val, xs in vals.items():
            if len(xs) < 2:
                continue
            m = sum(xs) / len(xs)
            effects[key][val] = round(max(-CAP, min(CAP, m * len(xs) / (len(xs) + K_SHRINK))), 3)
    dna = load_dna()
    tried = {k: {v: len(x) for v, x in g.items()} for k, g in groups.items()}
    explore_themes = sorted((t["theme"] for t in dna.themes), key=lambda t: tried.get("theme", {}).get(t, 0))[:3]
    strategy = {
        "themes": effects.get("theme", {}), "settings": effects.get("setting", {}), "hooks": effects.get("hook_grammar", {}),
        "durations": effects.get("duration_bucket", {}), "pacing": effects.get("pace_bucket", {}),
        "openings": effects.get("opening_shot", {}),
        "n_videos": len(rows), "sample_counts": tried, "retention_drop_beats": dict(drops),
        "test_next": {"themes": explore_themes},
        "updated_at": time.time(),
        "caveat": "Associations from a small sample of own videos; shrunk toward 0 and capped. Not causal.",
    }
    db.kv_set("strategy", strategy)
    log.info("strategy updated from %d videos", len(rows))
    return strategy


def report() -> dict:
    rows = age_adjusted(latest_rows())
    return {"videos": [{k: r.get(k) for k in ("id", "title", "views", "likes", "comments", "avg_view_pct", "score")}
                       for r in rows], "strategy": db.kv_get("strategy", {})}
