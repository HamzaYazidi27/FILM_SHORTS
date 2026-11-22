"""Phase 6 - dashboard API (FastAPI) + static single-page UI.

Every endpoint reads/writes real workflow state (SQLite + job files); there are no mock values."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .. import costs, db, logs
from ..config import ROOT, data_dir, load_settings, secrets_status, update_settings
from ..dna import load_dna
from ..modules import analytics, assets, publish, topics
from ..pipeline import orchestrator as o

log = logs.get("web")
STATIC = Path(__file__).parent / "static"
_running: dict[int, threading.Thread] = {}


def _run_bg(jid: int, until: str | None = None) -> None:
    t = _running.get(jid)
    if t and t.is_alive():
        raise HTTPException(409, f"job {jid} is already running in this server")
    th = threading.Thread(target=o.run_job, args=(jid, until), daemon=True, name=f"job-{jid}")
    th.start()
    _running[jid] = th


def _json(p: Path):
    return json.loads(p.read_text()) if p.exists() else None


def create_app(start_worker: bool = True) -> FastAPI:
    logs.setup()
    db.connect()
    app = FastAPI(title="ShortsEngine", version="0.1.0")

    @app.exception_handler(KeyError)
    async def _not_found(_req, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": f"not found: {exc}"}, status_code=404)
    if start_worker:
        from ..pipeline import worker
        worker.start_background(20)

    # ------------------------------------------------------------- overview
    @app.get("/api/overview")
    def overview():
        a = load_settings().automation
        hb = db.kv_get("worker_heartbeat")
        jobs = db.q("SELECT status, COUNT(*) n FROM jobs GROUP BY status")
        recent = db.q("SELECT j.id, j.status, j.current_stage, j.quality_score, j.error, j.updated_at, t.moral "
                      "FROM jobs j LEFT JOIN topics t ON t.id=j.topic_id ORDER BY j.id DESC LIMIT 8")
        vids = db.q1("SELECT COUNT(*) n, SUM(CASE WHEN youtube_id IS NOT NULL THEN 1 ELSE 0 END) up FROM videos")
        return {"automation_enabled": a.enabled, "videos_per_day": a.videos_per_day,
                "worker": {"alive": bool(hb and time.time() - hb["ts"] < 120), **(hb or {})},
                "jobs_by_status": {r["status"]: r["n"] for r in jobs}, "recent_jobs": recent,
                "videos": vids, "cost_today": round(costs.spent_today(), 4), "daily_budget": a.daily_budget_usd,
                "topics_available": db.q1("SELECT COUNT(*) n FROM topics WHERE status='candidate'")["n"],
                "avg_quality": db.q1("SELECT ROUND(AVG(quality_score),1) q FROM jobs WHERE status='done'")["q"],
                "errors_24h": db.q1("SELECT COUNT(*) n FROM events WHERE level IN ('ERROR','CRITICAL') AND ts>?",
                                    (time.time() - 86400,))["n"]}

    @app.post("/api/automation")
    def automation(enabled: bool = Body(..., embed=True)):
        update_settings({"automation": {"enabled": enabled}})
        logs.event(log, f"automation {'started' if enabled else 'paused'} from dashboard")
        return {"enabled": enabled}

    # ------------------------------------------------------------- channel intelligence
    @app.get("/api/channel")
    def channel():
        r = ROOT / "research"
        summ = _json(r / "performance_summary.json") or {}
        ds = _json(r / "video_dataset.json") or []
        cols = ["video_id", "era", "clean_title", "published", "duration_s", "views", "likes", "comments",
                "views_per_day", "like_rate", "age_adjusted_score", "performance_tier", "hook_category", "setting",
                "opening_shot", "fm_est_changes_per_min_lower_bound", "fm_caption_frame_ratio"]
        return {"dna": _json(r / "channel_dna.json"), "summary": summ,
                "dataset": [{k: d.get(k) for k in cols} for d in ds],
                "reports": sorted(p.name for p in r.glob("*.md"))}

    @app.get("/api/channel/report/{name}")
    def report(name: str):
        p = ROOT / "research" / name
        if not name.endswith(".md") or not p.exists() or p.parent != ROOT / "research":
            raise HTTPException(404)
        return {"name": name, "markdown": p.read_text()}

    # ------------------------------------------------------------- topics
    @app.get("/api/topics")
    def list_topics(status: str | None = None):
        sql = "SELECT * FROM topics" + (" WHERE status=?" if status else "") + " ORDER BY score DESC LIMIT 200"
        rows = db.q(sql, (status,) if status else ())
        for r in rows:
            for k in ("score_breakdown", "sources", "fact_angle"):
                r[k] = json.loads(r[k]) if r.get(k) else None
        return rows

    @app.post("/api/topics/discover")
    def discover(n: int = Body(8, embed=True)):
        th = threading.Thread(target=topics.discover, args=(n,), daemon=True)
        th.start()
        return {"started": True, "n": n}

    @app.get("/api/trends")
    def trends():
        from ..providers import wikipedia
        return wikipedia.most_read(1, 30)

    @app.post("/api/topics/{tid}/reject")
    def reject(tid: int, reason: str = Body("rejected in dashboard", embed=True)):
        topics.reject_topic(tid, reason)
        return {"ok": True}

    @app.post("/api/topics/{tid}/produce")
    def produce(tid: int):
        t = db.q1("SELECT * FROM topics WHERE id=?", (tid,))
        if not t or t["status"] in ("used", "rejected"):
            raise HTTPException(400, "topic not available")
        jid = o.create_job(tid)
        _run_bg(jid)
        return {"job": jid}

    # ------------------------------------------------------------- jobs / queue
    @app.get("/api/jobs")
    def jobs(limit: int = 50):
        rows = db.q("SELECT j.*, t.moral, t.theme FROM jobs j LEFT JOIN topics t ON t.id=j.topic_id ORDER BY j.id DESC LIMIT ?",
                    (limit,))
        for r in rows:
            r["stages"] = o.stage_rows(r["id"])
            r["cost_usd"] = round(costs.spent_job(r["id"]), 4)
        return rows

    @app.post("/api/jobs")
    def new_job(run: bool = Body(True, embed=True)):
        jid = o.create_job()
        if run:
            _run_bg(jid)
        return {"job": jid}

    @app.get("/api/jobs/{jid}")
    def job_detail(jid: int):
        j = o.job(jid)
        wd = Path(j["workdir"])
        return {"job": j, "stages": o.stage_rows(jid), "qc": _json(wd / "qc.json"),
                "outputs": {s: (wd / f"{s}.json").exists() for s in o.STAGES},
                "has_video": (wd / "final.mp4").exists(), "cost_usd": round(costs.spent_job(jid), 4),
                "events": db.q("SELECT ts, level, stage, message FROM events WHERE job_id=? ORDER BY id DESC LIMIT 60", (jid,))}

    @app.get("/api/jobs/{jid}/stage/{stage}")
    def stage_output(jid: int, stage: str):
        if stage not in o.STAGES:
            raise HTTPException(404)
        d = _json(o.out_path(jid, stage))
        if d is None:
            raise HTTPException(404, "stage has no output yet")
        return d

    @app.post("/api/jobs/{jid}/run")
    def run(jid: int, until: str | None = Body(None, embed=True)):
        if o.job(jid)["status"] in ("failed", "paused"):
            o.retry_job(jid)
        _run_bg(jid, until)
        return {"started": jid}

    @app.post("/api/jobs/{jid}/retry")
    def retry(jid: int, from_stage: str | None = Body(None, embed=True)):
        if from_stage and from_stage not in o.STAGES:
            raise HTTPException(400, "unknown stage")
        o.retry_job(jid, from_stage)
        _run_bg(jid)
        return {"retrying": jid, "from": from_stage}

    @app.post("/api/jobs/{jid}/cancel")
    def cancel(jid: int):
        db.ex("UPDATE jobs SET status='cancelled', locked_by=NULL WHERE id=? AND status<>'running'", (jid,))
        return {"status": o.job(jid)["status"]}

    # ------------------------------------------------------------- script studio / storyboard editor
    @app.put("/api/jobs/{jid}/script")
    def edit_script(jid: int, lines: list[dict] = Body(..., embed=True), pov_text: str | None = Body(None, embed=True)):
        s = o.load(jid, "script")
        by_id = {l["id"]: l for l in s["lines"]}
        for e in lines:
            if e.get("id") in by_id:
                for k in ("text", "emotion", "speaker"):
                    if k in e:
                        by_id[e["id"]][k] = e[k]
        if pov_text:
            s["pov_text"] = pov_text
        o.save(jid, "script", s)
        o.reset_from(jid, "storyboard")
        db.ex("UPDATE jobs SET status='queued' WHERE id=? AND status IN ('done','failed','paused')", (jid,))
        return {"saved": True, "rerun_from": "storyboard"}

    @app.put("/api/jobs/{jid}/storyboard")
    def edit_storyboard(jid: int, shots: list[dict] = Body(..., embed=True)):
        sb = o.load(jid, "storyboard")
        by_n = {s["n"]: s for s in sb["shots"]}
        prompts_changed = False
        for e in shots:
            s = by_n.get(e.get("n"))
            if not s:
                continue
            for k in ("camera", "transition_in", "framing", "sfx"):
                if k in e:
                    s[k] = e[k]
            if "prompt" in e:
                a = next(a for a in sb["assets"] if a["key"] == s["asset_key"])
                if a["prompt"] != e["prompt"]:
                    a["prompt"] = e["prompt"]
                    db.ex("DELETE FROM assets WHERE job_id=? AND asset_key=?", (jid, a["key"]))
                    prompts_changed = True
        o.save(jid, "storyboard", sb)
        o.reset_from(jid, "assets" if prompts_changed else "edit")
        db.ex("UPDATE jobs SET status='queued' WHERE id=? AND status IN ('done','failed','paused')", (jid,))
        return {"saved": True, "rerun_from": "assets" if prompts_changed else "edit"}

    # ------------------------------------------------------------- videos / media / assets
    @app.get("/api/videos")
    def videos():
        rows = db.q("SELECT v.*, j.quality_score FROM videos v JOIN jobs j ON j.id=v.job_id ORDER BY v.id DESC")
        for r in rows:
            r["tags"] = json.loads(r["tags"] or "[]")
            r["features"] = json.loads(r["features"] or "{}")
        return rows

    @app.get("/media/{jid}/{name}")
    def media(jid: int, name: str):
        if name not in ("final.mp4", "thumbnail.jpg", "captions.srt"):
            raise HTTPException(404)
        p = Path(o.job(jid)["workdir"]) / name
        if not p.exists():
            raise HTTPException(404)
        dl = {"filename": f"short_{jid:05d}_{name}"} if name != "thumbnail.jpg" else {}
        return FileResponse(p, **dl)

    @app.get("/asset/{aid}")
    def asset_file(aid: int):
        a = db.q1("SELECT path FROM assets WHERE id=?", (aid,))
        if not a or not Path(a["path"]).exists() or data_dir() not in Path(a["path"]).resolve().parents:
            raise HTTPException(404)
        return FileResponse(a["path"])

    @app.get("/api/assets")
    def list_assets(job_id: int | None = None):
        sql = "SELECT * FROM assets" + (" WHERE job_id=?" if job_id else "") + " ORDER BY id DESC LIMIT 300"
        rows = db.q(sql, (job_id,) if job_id else ())
        for r in rows:
            r["allowed"] = r["license"].startswith(assets.ALLOWED_LICENSES_PREFIX)
        return rows

    # ------------------------------------------------------------- publishing
    @app.get("/api/publish/status")
    def pub_status():
        return publish.status()

    @app.post("/api/videos/{vid}/publish")
    def do_publish(vid: int, privacy: str = Body("private", embed=True)):
        if privacy not in ("private", "unlisted", "public"):
            raise HTTPException(400)
        try:
            return publish.upload(vid, privacy)
        except publish.PublishError as e:
            raise HTTPException(400, str(e))

    # ------------------------------------------------------------- analytics
    @app.get("/api/analytics")
    def get_analytics():
        return analytics.report()

    @app.post("/api/analytics/refresh")
    def refresh_analytics():
        try:
            n = analytics.fetch()
        except Exception as e:
            raise HTTPException(400, f"fetch failed: {e}")
        return {"snapshots": n, "strategy": analytics.learn()}

    # ------------------------------------------------------------- settings / providers
    @app.get("/api/settings")
    def get_settings():
        return json.loads(load_settings().model_dump_json())

    @app.put("/api/settings")
    def put_settings(patch: dict = Body(...)):
        try:
            return json.loads(update_settings(patch).model_dump_json())
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/providers")
    def providers():
        p = load_settings().providers
        return {"secrets": secrets_status(), "selected": json.loads(p.model_dump_json()),
                "available": {"llm": ["anthropic", "openai_compatible", "pollinations"],
                              "image": ["pollinations", "openai", "procedural"],
                              "tts": ["edge", "elevenlabs", "openai", "espeak"]}}

    @app.post("/api/providers/test")
    def test_provider(kind: str = Body(..., embed=True)):
        t0 = time.time()
        try:
            if kind == "llm":
                from ..providers.llm import get_llm
                r = get_llm().complete_json("Reply with JSON.", 'Return {"ok": true}.',
                                            {"type": "object", "properties": {"ok": {"type": "boolean"}},
                                             "required": ["ok"], "additionalProperties": False}, max_tokens=500)
            elif kind == "tts":
                import tempfile
                from ..providers.tts import get_tts
                v = load_settings().voice
                with tempfile.TemporaryDirectory() as d:
                    res = get_tts(v.provider).synth("Provider test.", v.male_voices[0], Path(d), "t")
                    r = {"duration": res.duration, "words": len(res.words)}
            elif kind == "image":
                from ..providers.images import get_image_provider
                res = get_image_provider().generate("a calm empty cafe interior, photo", 1080, 1920, 1)
                r = {"source": res.source, "size": res.image.size}
            elif kind == "research":
                from ..providers import wikipedia
                r = {"hits": len(wikipedia.search("consumer protection", 3))}
            else:
                raise HTTPException(400, "unknown provider kind")
            return {"ok": True, "result": r, "seconds": round(time.time() - t0, 1)}
        except HTTPException:
            raise
        except Exception as e:
            return {"ok": False, "error": str(e)[:500], "seconds": round(time.time() - t0, 1)}

    # ------------------------------------------------------------- logs / costs
    @app.get("/api/logs")
    def get_logs(level: str | None = None, job_id: int | None = None, limit: int = 200):
        where, args = [], []
        if level:
            where.append("level=?")
            args.append(level)
        if job_id:
            where.append("job_id=?")
            args.append(job_id)
        sql = "SELECT * FROM events" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
        return db.q(sql, (*args, limit))

    @app.get("/api/costs")
    def get_costs(days: int = 30):
        return costs.summary(days)

    @app.get("/api/dna/format")
    def dna_format():
        d = load_dna()
        return {"name": d.name, "duration_target": d.duration_target, "mean_shot_s": d.mean_shot_s,
                "beats": [b.__dict__ for b in d.beats]}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC / "index.html").read_text()

    return app
