"""Collect public metadata, storyboards and sample comments for a YouTube channel's Shorts.

Uses yt-dlp with the `mweb` player client (the default `web` client is gated behind a
bot-check from datacenter IPs). Only public data is fetched; no cookies or credentials.

Usage:
    python research/tools/collect_channel.py --channel "https://www.youtube.com/@Yah1231/shorts" \
        --out research/raw --comments 15

Outputs (under --out):
    channel_flat.json           flat playlist listing (channel name/id/followers)
    meta/<id>.json              slimmed per-video metadata (no signed stream URLs)
    storyboards/<id>/sbN.jpg    storyboard sprite sheets (real sampled frames)
    storyboards/<id>/spec.json  sprite geometry (cols/rows/frame size/fps)
    comments/<id>.json          up to 60 top comments for a sample of videos
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

YTDLP_BASE = [
    "yt-dlp", "--js-runtimes", "node",
    "--extractor-args", "youtube:player_client=mweb",
    "--ignore-no-formats-error", "--skip-download",
]

KEEP = [
    "id", "title", "description", "upload_date", "timestamp", "duration", "view_count",
    "like_count", "comment_count", "tags", "categories", "channel", "channel_id",
    "channel_follower_count", "thumbnail", "language", "webpage_url", "availability",
    "age_limit", "subtitles", "automatic_captions",
]


def run_json(args: list[str]) -> dict | None:
    p = subprocess.run(YTDLP_BASE + ["-J"] + args, capture_output=True, text=True)
    if p.returncode != 0 or not p.stdout.strip():
        sys.stderr.write(p.stderr[-500:])
        return None
    return json.loads(p.stdout)


def slim(info: dict) -> dict:
    out = {k: info.get(k) for k in KEEP}
    out["subtitles"] = sorted((info.get("subtitles") or {}).keys())
    out["automatic_captions"] = sorted((info.get("automatic_captions") or {}).keys())
    sbs = []
    for f in info.get("formats") or []:
        if str(f.get("format_id", "")).startswith("sb"):
            sbs.append({
                "format_id": f["format_id"], "width": f.get("width"), "height": f.get("height"),
                "rows": f.get("rows"), "columns": f.get("columns"), "fps": f.get("fps"),
                "fragments": [fr.get("url") for fr in f.get("fragments") or []],
                "fragment_durations": [fr.get("duration") for fr in f.get("fragments") or []],
            })
    out["storyboards"] = sbs
    return out


def fetch_video(vid: str, out: Path) -> dict | None:
    dst = out / "meta" / f"{vid}.json"
    if dst.exists():
        return json.loads(dst.read_text())
    info = run_json([f"https://www.youtube.com/shorts/{vid}"])
    if not info:
        return None
    s = slim(info)
    dst.write_text(json.dumps(s, ensure_ascii=False, indent=1))
    return s


def fetch_storyboard(meta: dict, out: Path) -> int:
    sb = next((s for s in meta.get("storyboards", []) if s["format_id"] == "sb0"), None)
    if not sb:
        return 0
    d = out / "storyboards" / meta["id"]
    d.mkdir(parents=True, exist_ok=True)
    spec = {k: sb[k] for k in ("width", "height", "rows", "columns", "fps", "fragment_durations")}
    spec["duration"] = meta.get("duration")
    (d / "spec.json").write_text(json.dumps(spec))
    n = 0
    for i, url in enumerate(sb["fragments"]):
        p = d / f"sb{i:02d}.jpg"
        if not p.exists():
            try:
                urllib.request.urlretrieve(url, p)
            except Exception as e:  # noqa: BLE001 - keep going, document gaps
                sys.stderr.write(f"storyboard {meta['id']} #{i}: {e}\n")
                continue
        n += 1
    return n


def fetch_comments(vid: str, out: Path, n: int = 60) -> int:
    dst = out / "comments" / f"{vid}.json"
    if dst.exists():
        return len(json.loads(dst.read_text()))
    info = run_json(["--write-comments", "--extractor-args",
                     f"youtube:player_client=mweb;max_comments={n},all,0",
                     f"https://www.youtube.com/shorts/{vid}"])
    comments = [{"text": c.get("text"), "like_count": c.get("like_count"),
                 "timestamp": c.get("timestamp")} for c in (info or {}).get("comments") or []]
    dst.write_text(json.dumps(comments, ensure_ascii=False, indent=1))
    return len(comments)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--out", default="research/raw")
    ap.add_argument("--comments", type=int, default=15, help="number of videos to sample comments from")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    out = Path(a.out)
    for sub in ("meta", "storyboards", "comments"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    flat_p = out / "channel_flat.json"
    if not flat_p.exists():
        p = subprocess.run(["yt-dlp", "--flat-playlist", "-J", a.channel], capture_output=True, text=True, check=True)
        flat_p.write_text(p.stdout)
    flat = json.loads(flat_p.read_text())
    ids = [e["id"] for e in flat["entries"]]
    print(f"{flat.get('channel')} ({flat.get('channel_id')}): {len(ids)} shorts listed")

    with cf.ThreadPoolExecutor(a.workers) as ex:
        metas = [m for m in ex.map(lambda v: fetch_video(v, out), ids) if m]
    print(f"metadata: {len(metas)}/{len(ids)}")
    with cf.ThreadPoolExecutor(a.workers) as ex:
        frames = list(ex.map(lambda m: fetch_storyboard(m, out), metas))
    print(f"storyboard sheets: {sum(frames)} for {sum(1 for f in frames if f)} videos")

    # Comment sample: top, bottom and median performers across eras
    ranked = sorted(metas, key=lambda m: m.get("view_count") or 0)
    k = a.comments
    sample = {m["id"] for m in ranked[-(k // 2):] + ranked[: k // 4] + ranked[len(ranked) // 2 - k // 8: len(ranked) // 2 + k // 8]}
    with cf.ThreadPoolExecutor(3) as ex:
        counts = list(ex.map(lambda v: fetch_comments(v, out), sorted(sample)))
    print(f"comments: {sum(counts)} across {len(sample)} videos")


if __name__ == "__main__":
    main()
