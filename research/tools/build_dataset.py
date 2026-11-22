"""Build the video dataset (CSV + JSON) and aggregate performance statistics.

Combines slimmed metadata (research/raw/meta), frame metrics (research/frame_metrics.json)
and manual visual annotations made from the storyboard frames (research/annotations.json).

Usage: python research/tools/build_dataset.py
"""
from __future__ import annotations

import csv
import json
import re
import statistics as st
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLECTED_AT = datetime(2026, 11, 22, 1, 50, tzinfo=timezone.utc)  # time of metadata snapshot

# Title -> moral/hook category (keyword rules, checked manually against all 41 Era B titles)
HOOK_RULES = [
    ("prejudice_appearance", r"judg|appearance|look down|prejudice|differences|\bblind\b|young doesn|rich|fear"),
    ("respect_authority", r"respect|badge|authority|disrespect|arrogance"),
    ("greed_honesty", r"greed|honest"),
    ("kindness_goodwill", r"kindness|hospitality|goodwill|care for others|good intention|best self|putting others down"),
    ("slander_false_accusation", r"slander|evidence|facts"),
    ("quick_thinking_survival", r"quick thinking|save herself"),
    ("bullying_disability", r"bull|disabilit|mocked"),
    ("parenting_family", r"father|mother|child"),
    ("entitlement_boundaries", r"lenient|unreasonable|taking advantage|rival|decision"),
    ("character_motivation", r"character|desire|love|motivation|talent"),
    ("twist_reveal", r"didn't expect|one they were looking|decisions about her own body"),
]


def title_grammar(t: str) -> str:
    tl = t.lower()
    if re.match(r"(never|don't|do not|please|be |learn|shouldn't)", tl):
        return "imperative"
    if re.match(r"(he|she|his|her|people|i )", tl):
        return "narrative_teaser"
    if re.match(r"(is|are|do|does|can|why|what) ", tl):
        return "question"
    return "general_maxim"


def era(m: dict) -> str:
    return "B_moral_drama" if m["upload_date"] >= "20261012" else "A_movie_recap"


def hook_category(title: str) -> str:
    t = title.lower()
    for name, rx in HOOK_RULES:
        if re.search(rx, t):
            return name
    return "other"


def clean_title(title: str) -> str:
    t = re.sub(r"#\w+", "", title)
    t = re.sub(r"[☠️🔥|]", "", t)
    return re.sub(r"\s+", " ", t).strip().strip('"“”').strip()


def duration_bucket(d: int) -> str:
    for lo, hi in [(0, 60), (60, 75), (75, 90), (90, 120), (120, 160), (160, 181)]:
        if lo <= d < hi:
            return f"{lo}-{hi - 1}s"
    return "181s+"


def main() -> None:
    metas = [json.loads(p.read_text()) for p in (ROOT / "raw" / "meta").glob("*.json")]
    metas.sort(key=lambda m: m["timestamp"])
    frames = json.loads((ROOT / "frame_metrics.json").read_text())
    ann = json.loads((ROOT / "annotations.json").read_text()) if (ROOT / "annotations.json").exists() else {}
    subs = metas[-1].get("channel_follower_count") or 0
    rows = []
    for m in metas:
        age_days = max((COLLECTED_AT - datetime.fromtimestamp(m["timestamp"], timezone.utc)).total_seconds() / 86400, 0.5)
        v, l, c = m.get("view_count") or 0, m.get("like_count") or 0, m.get("comment_count") or 0
        desc = m.get("description") or ""
        track = re.search(r"Track:\s*(.+)", desc)
        show = re.search(r"\|\s*([^|#]+?)\s*\|", m["title"])
        movie = re.search(r"story of\s*[\"“](.+?)[\"”]", m["title"])
        fm = frames.get(m["id"], {})
        a = ann.get(m["id"], {})
        rows.append({
            "video_id": m["id"],
            "url": f"https://www.youtube.com/shorts/{m['id']}",
            "era": era(m),
            "title": m["title"],
            "clean_title": clean_title(m["title"]),
            "source_work_named": (show or movie).group(1).strip() if (show or movie) else "",
            "hook_category": hook_category(clean_title(m["title"])) if era(m).startswith("B") else "movie_recap",
            "title_grammar": title_grammar(clean_title(m["title"])) if era(m).startswith("B") else "tell_the_story_of",
            "title_words": len(clean_title(m["title"]).split()),
            "description_template": "editor_credit+fair_use+hashtag_block" if "Fair Use" in desc else ("empty" if not desc.strip() else "other"),
            "hashtags_in_title": " ".join(re.findall(r"#\w+", m["title"])),
            "hashtag_count_desc": len(re.findall(r"#\w+", desc)),
            "music_track_credited": track.group(1).strip() if track else "",
            "published": datetime.fromtimestamp(m["timestamp"], timezone.utc).isoformat(),
            "publish_hour_utc": datetime.fromtimestamp(m["timestamp"], timezone.utc).hour,
            "weekday": datetime.fromtimestamp(m["timestamp"], timezone.utc).strftime("%a"),
            "age_days": round(age_days, 2),
            "duration_s": m["duration"],
            "duration_bucket": duration_bucket(m["duration"]),
            "views": v, "likes": l, "comments": c,
            "views_per_day": round(v / age_days, 1),
            "like_rate": round(l / v, 5) if v else 0,
            "comment_rate": round(c / v, 6) if v else 0,
            "views_per_subscriber": round(v / subs, 3) if subs else None,
            "captions_available": bool(m.get("subtitles") or m.get("automatic_captions")),
            "thumbnail": m.get("thumbnail") or "",
            "category": ",".join(m.get("categories") or []),
            **{f"fm_{k}": fm.get(k) for k in [
                "frames", "mean_luma", "mean_saturation", "mean_warmth_r_minus_b", "mean_colourfulness",
                "mean_contrast_std", "visual_change_ratio", "est_changes_per_min_lower_bound",
                "est_mean_shot_s_upper_bound", "caption_frame_ratio", "caption_y_median",
                "title_bar_first_frame", "title_bar_seconds", "black_frames", "last_frame_luma"]},
            "setting": a.get("setting", ""),
            "opening_shot": a.get("opening_shot", ""),
            "ending_shot": a.get("ending_shot", ""),
        })
    # Age-adjusted performance: residual of log(views) regressed on log(age) within each era.
    # Raw views favour old videos and views/day favours very young ones; the residual removes
    # the average age effect so videos of different ages are comparable.
    import math
    for e in {r["era"] for r in rows}:
        er = [r for r in rows if r["era"] == e]
        xs = [math.log(r["age_days"]) for r in er]
        ys = [math.log(max(r["views"], 1)) for r in er]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / max(1e-9, sum((x - mx) ** 2 for x in xs))
        a0 = my - b * mx
        for r, x, y in zip(er, xs, ys):
            r["age_adjusted_score"] = round(y - (a0 + b * x), 3)
    # performance tier within era, by age-adjusted score percentile
    for e in {r["era"] for r in rows}:
        er = sorted([r for r in rows if r["era"] == e], key=lambda r: r["age_adjusted_score"])
        for i, r in enumerate(er):
            p = i / max(1, len(er) - 1)
            r["perf_percentile_in_era"] = round(p, 3)
            r["performance_tier"] = "top" if p >= 0.8 else "high" if p >= 0.6 else "mid" if p >= 0.4 else "low" if p >= 0.2 else "bottom"

    out_csv = ROOT / "video_dataset.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (ROOT / "video_dataset.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))

    # ---- aggregates
    def agg(rs: list[dict]) -> dict:
        if not rs:
            return {}
        vpd = [r["views_per_day"] for r in rs]
        return {
            "n": len(rs),
            "views_median": st.median(r["views"] for r in rs),
            "views_mean": round(st.mean(r["views"] for r in rs)),
            "vpd_median": round(st.median(vpd), 1),
            "age_adj_score_median": round(st.median(r.get("age_adjusted_score", 0) for r in rs), 3),
            "vpd_mean": round(st.mean(vpd), 1),
            "like_rate_median": round(st.median(r["like_rate"] for r in rs), 5),
            "comment_rate_median": round(st.median(r["comment_rate"] for r in rs), 6),
            "duration_median": st.median(r["duration_s"] for r in rs),
            "duration_p10_p90": [sorted(r["duration_s"] for r in rs)[int(len(rs) * 0.1)],
                                 sorted(r["duration_s"] for r in rs)[min(len(rs) - 1, int(len(rs) * 0.9))]],
        }

    summary: dict = {"collected_at": COLLECTED_AT.isoformat(), "subscribers": subs, "n_videos": len(rows)}
    for e in sorted({r["era"] for r in rows}):
        er = [r for r in rows if r["era"] == e]
        summary[e] = agg(er)
        summary[e]["first"] = er[0]["published"][:10]
        summary[e]["last"] = er[-1]["published"][:10]
        days = (datetime.fromisoformat(er[-1]["published"]) - datetime.fromisoformat(er[0]["published"])).days + 1
        summary[e]["uploads_per_day"] = round(len(er) / days, 2)
        summary[e]["by_hook_category"] = {k: agg([r for r in er if r["hook_category"] == k])
                                          for k in sorted({r["hook_category"] for r in er})}
        summary[e]["by_title_grammar"] = {k: agg([r for r in er if r["title_grammar"] == k])
                                          for k in sorted({r["title_grammar"] for r in er})}
        summary[e]["by_opening_shot"] = {k: agg([r for r in er if r["opening_shot"] == k])
                                         for k in sorted({r["opening_shot"] for r in er}) if k}
        summary[e]["by_ending_shot"] = {k: agg([r for r in er if r["ending_shot"] == k])
                                        for k in sorted({r["ending_shot"] for r in er}) if k}
        summary[e]["by_duration_bucket"] = {k: agg([r for r in er if r["duration_bucket"] == k])
                                            for k in sorted({r["duration_bucket"] for r in er})}
        summary[e]["by_setting"] = {k: agg([r for r in er if r["setting"] == k])
                                    for k in sorted({r["setting"] for r in er}) if k}
        summary[e]["by_tier_frame_metrics"] = {}
        for tier in ["top", "high", "mid", "low", "bottom"]:
            tr = [r for r in er if r["performance_tier"] == tier]
            if tr:
                summary[e]["by_tier_frame_metrics"][tier] = {
                    k: round(st.median(r[k] for r in tr if r[k] is not None), 3)
                    for k in ["fm_est_changes_per_min_lower_bound", "fm_caption_frame_ratio", "fm_mean_saturation",
                              "fm_mean_colourfulness", "fm_mean_luma", "duration_s", "like_rate"]}
                summary[e]["by_tier_frame_metrics"][tier]["n"] = len(tr)
        summary[e]["frame_metrics_median"] = {
            k: round(st.median(r[k] for r in er if r[k] is not None), 3)
            for k in ["fm_mean_luma", "fm_mean_saturation", "fm_mean_warmth_r_minus_b", "fm_mean_colourfulness",
                      "fm_mean_contrast_std", "fm_visual_change_ratio", "fm_est_changes_per_min_lower_bound",
                      "fm_est_mean_shot_s_upper_bound", "fm_caption_frame_ratio", "fm_caption_y_median",
                      "fm_title_bar_seconds"]}
        summary[e]["title_bar_first_frame_ratio"] = round(sum(bool(r["fm_title_bar_first_frame"]) for r in er) / len(er), 3)
        summary[e]["videos_with_black_frames"] = sum(1 for r in er if (r["fm_black_frames"] or 0) > 0)
        summary[e]["publish_hour_utc_counts"] = {h: sum(1 for r in er if r["publish_hour_utc"] == h)
                                                 for h in sorted({r["publish_hour_utc"] for r in er})}
    # Spearman correlations within Era B (ranks), descriptive only
    eb = [r for r in rows if r["era"].startswith("B")]

    def rank(xs):
        s = sorted(range(len(xs)), key=lambda i: xs[i])
        rk = [0.0] * len(xs)
        for pos, i in enumerate(s):
            rk[i] = pos
        return rk

    def spearman(a, b):
        ra, rb = rank(a), rank(b)
        n = len(a)
        ma, mb = sum(ra) / n, sum(rb) / n
        num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
        den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
        return round(num / den, 3) if den else None

    summary["eraB_spearman_vs_age_adjusted_score"] = {
        k: spearman([r[k] or 0 for r in eb], [r["age_adjusted_score"] for r in eb])
        for k in ["duration_s", "fm_est_changes_per_min_lower_bound", "fm_caption_frame_ratio",
                  "fm_mean_saturation", "fm_mean_colourfulness", "fm_mean_luma", "fm_mean_warmth_r_minus_b",
                  "age_days", "like_rate", "publish_hour_utc"]}
    (ROOT / "performance_summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    print(json.dumps({k: summary[k] for k in ("n_videos", "subscribers")}, indent=1))
    for e in ("A_movie_recap", "B_moral_drama"):
        print(e, {k: summary[e][k] for k in ("n", "vpd_median", "views_median", "duration_median", "uploads_per_day", "like_rate_median")})


if __name__ == "__main__":
    main()
