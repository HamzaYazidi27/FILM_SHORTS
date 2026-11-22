"""Phase 3 - orchestration: persistent job queue, per-stage state, retries with backoff,
send-back loops (research rejection -> new topic; duration -> script; QC -> failing stage),
budget/daily limits, duplicate prevention, crash recovery and scheduling.

Every stage writes data/jobs/<id>/<stage>.json, so any stage can be re-run without redoing
earlier work, and an interrupted job resumes from its first unfinished stage."""
from __future__ import annotations

import json
import os
import socket
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .. import costs, db, logs
from ..config import data_dir, load_settings
from ..dna import load_dna
from ..modules import analytics, assets, editor, metadata, publish, qc, research, script, storyboard, topics, voice

log = logs.get("orchestrator")
STAGES = ["topic", "research", "script", "storyboard", "assets", "voice", "edit", "qc", "metadata", "publish"]
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
STALE_LOCK_S = 3 * 3600
MAX_TOPIC_SWAPS = 3


class SendBack(Exception):
    def __init__(self, stage: str, reason: str, feedback: str = ""):
        super().__init__(reason)
        self.stage, self.reason, self.feedback = stage, reason, feedback


class Paused(Exception):
    pass


# ------------------------------------------------------------------ job helpers
def create_job(topic_id: int | None = None) -> int:
    ts = time.time()
    jid = db.ex("INSERT INTO jobs(topic_id,status,workdir,created_at,updated_at) VALUES(?,?,?,?,?)",
                (topic_id, "queued", "", ts, ts))
    wd = data_dir() / "jobs" / f"{jid:05d}"
    wd.mkdir(parents=True, exist_ok=True)
    db.ex("UPDATE jobs SET workdir=? WHERE id=?", (str(wd), jid))
    for s in STAGES:
        db.ex("INSERT INTO stages(job_id,stage,status) VALUES(?,?,?)", (jid, s, "pending"))
    logs.event(log, f"job {jid} created", jid, None)
    return jid


def job(jid: int) -> dict:
    j = db.q1("SELECT * FROM jobs WHERE id=?", (jid,))
    if not j:
        raise KeyError(jid)
    return j


def stage_rows(jid: int) -> list[dict]:
    rows = {r["stage"]: r for r in db.q("SELECT * FROM stages WHERE job_id=?", (jid,))}
    return [rows[s] for s in STAGES if s in rows]


def out_path(jid: int, stage: str) -> Path:
    return Path(job(jid)["workdir"]) / f"{stage}.json"


def load(jid: int, stage: str) -> dict:
    return json.loads(out_path(jid, stage).read_text())


def save(jid: int, stage: str, data: dict) -> None:
    p = out_path(jid, stage)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str))
    tmp.replace(p)


def reset_from(jid: int, stage: str) -> None:
    i = STAGES.index(stage)
    if i <= STAGES.index("script"):
        (Path(job(jid)["workdir"]) / "script.partial.json").unlink(missing_ok=True)
    for s in STAGES[i:]:
        db.ex("UPDATE stages SET status='pending', error=NULL WHERE job_id=? AND stage=?", (jid, s))


def set_feedback(jid: int, stage: str, text: str) -> None:
    fb = db.kv_get(f"feedback:{jid}", {})
    fb[stage] = (fb.get(stage, "") + "\n" + text).strip()[-3000:]
    db.kv_set(f"feedback:{jid}", fb)


def feedback(jid: int, stage: str) -> str:
    return db.kv_get(f"feedback:{jid}", {}).get(stage, "")


# ------------------------------------------------------------------ stage implementations
def st_topic(jid: int) -> dict:
    j = job(jid)
    t = db.q1("SELECT * FROM topics WHERE id=?", (j["topic_id"],)) if j["topic_id"] else None
    if t and t["status"] == "rejected":
        t = None
    if not t:
        t = topics.select_topic()
        if not t:
            topics.discover(8, job_id=jid)
            t = topics.select_topic()
        if not t:
            raise RuntimeError("no topic above the minimum score; adjust min_topic_score or discover more")
        db.ex("UPDATE jobs SET topic_id=? WHERE id=?", (t["id"], jid))
    if db.q1("SELECT id FROM jobs WHERE topic_id=? AND id<>? AND status='done'", (t["id"], jid)):
        raise SendBack("topic", "topic already produced by another job")
    db.ex("UPDATE topics SET status='selected' WHERE id=?", (t["id"],))
    return {k: t[k] for k in ("id", "title", "premise", "theme", "setting", "moral", "fact_angle", "score", "score_breakdown",
                              "sources")}


def st_research(jid: int) -> dict:
    t = load(jid, "topic")
    try:
        return research.run(t, jid)
    except research.ResearchRejected as e:
        topics.reject_topic(t["id"], str(e))
        db.ex("UPDATE jobs SET topic_id=NULL WHERE id=?", (jid,))
        swaps = db.kv_get(f"topic_swaps:{jid}", 0) + 1
        db.kv_set(f"topic_swaps:{jid}", swaps)
        if swaps > MAX_TOPIC_SWAPS:
            raise RuntimeError(f"{swaps} topics failed fact-checking: {e}") from e
        raise SendBack("topic", f"research rejected topic {t['id']}: {e}") from e


def st_script(jid: int) -> dict:
    try:
        s = script.generate(load(jid, "topic"), load(jid, "research"), jid, feedback(jid, "script"),
                            checkpoint=Path(job(jid)["workdir"]) / "script.partial.json")
    except script.ScriptRejected as e:
        set_feedback(jid, "script", f"Previous script was rejected: {e}")
        raise
    script.register(jid, s)
    return s


def st_storyboard(jid: int) -> dict:
    return storyboard.build(load(jid, "script"), load_dna())


def st_assets(jid: int) -> dict:
    sb = load(jid, "storyboard")
    paths = assets.acquire(jid, sb, Path(job(jid)["workdir"]))
    return {"assets": paths, "rights": assets.rights_report(jid)}


def st_voice(jid: int) -> dict:
    try:
        return voice.run(load(jid, "script"), Path(job(jid)["workdir"]), jid)
    except voice.DurationOutOfRange as e:
        verb = "Add" if e.words_delta > 0 else "Remove"
        raise SendBack("script", str(e), f"{verb} about {abs(e.words_delta)} words of dialogue "
                       f"({str(e)}).") from e


def st_edit(jid: int) -> dict:
    wd = Path(job(jid)["workdir"])
    return editor.render(jid, load(jid, "script"), load(jid, "storyboard"), load(jid, "voice"),
                         load(jid, "assets")["assets"], wd, load(jid, "research"))


def st_qc(jid: int) -> dict:
    wd = Path(job(jid)["workdir"])
    rep = qc.run(jid, load(jid, "script"), load(jid, "storyboard"), load(jid, "voice"), load(jid, "edit"),
                 load(jid, "research"), wd)
    db.ex("UPDATE jobs SET quality_score=? WHERE id=?", (rep["score"], jid))
    if not rep["passed"]:
        save(jid, "qc", rep)
        raise SendBack(rep["send_back_to"] or "edit", "QC rejected: " + "; ".join(rep["rejections"][:4]),
                       "Quality control rejected the previous version:\n" + "\n".join(rep["rejections"]))
    return rep


def video_features(jid: int) -> dict:
    s, tl, t, ed = load(jid, "script"), load(jid, "voice"), load(jid, "topic"), load(jid, "edit")
    hook = s["hooks"][s["recommended_hook"]]
    total = tl["total"]
    spans, cur = [], None
    for l in tl["lines"]:
        if not cur or cur["beat"] != l["beat"]:
            cur = {"beat": l["beat"], "start_pct": round(l["start"] / total, 3)}
            spans.append(cur)
        cur["end_pct"] = round(l["end"] / total, 3)
    ms = ed["mean_shot_s"]
    return {"theme": t["theme"], "setting": t["setting"], "hook_grammar": hook["grammar"],
            "duration_bucket": f"{int(total // 10 * 10)}s", "duration_s": total,
            "pace_bucket": "fast" if ms < 2.2 else "medium" if ms < 3.0 else "slow", "mean_shot_s": ms,
            "opening_shot": ed["shots"][0]["framing"], "beat_spans": spans,
            "reveal_pct": next((round(l["start"] / total, 3) for l in tl["lines"] if l["is_reveal"]), None)}


def st_metadata(jid: int) -> dict:
    m = metadata.build(load(jid, "script"), load(jid, "research"), load(jid, "topic"))
    ed = load(jid, "edit")
    feats = video_features(jid)
    feats["metadata_flags"] = {k: m[k] for k in ("category_id", "contains_synthetic_media", "made_for_kids", "default_language")}
    if not db.q1("SELECT id FROM videos WHERE job_id=?", (jid,)):
        db.ex("INSERT INTO videos(job_id,title,description,tags,path,duration_s,features,created_at) VALUES(?,?,?,?,?,?,?,?)",
              (jid, m["title"], m["description"], json.dumps(m["tags"]), ed["final"], ed["duration"], json.dumps(feats),
               time.time()))
    else:
        db.ex("UPDATE videos SET title=?, description=?, tags=?, path=?, duration_s=?, features=? WHERE job_id=?",
              (m["title"], m["description"], json.dumps(m["tags"]), ed["final"], ed["duration"], json.dumps(feats), jid))
    db.ex("UPDATE topics SET status='used' WHERE id=(SELECT topic_id FROM jobs WHERE id=?)", (jid,))
    return m


def st_publish(jid: int) -> dict:
    a = load_settings().automation
    if not a.auto_publish:
        return {"skipped": True, "reason": "auto_publish disabled (publish manually from the dashboard)"}
    v = db.q1("SELECT id FROM videos WHERE job_id=?", (jid,))
    return publish.upload(v["id"], a.publish_privacy, schedule=a.publish_privacy == "public")


IMPL = {"topic": st_topic, "research": st_research, "script": st_script, "storyboard": st_storyboard,
        "assets": st_assets, "voice": st_voice, "edit": st_edit, "qc": st_qc, "metadata": st_metadata,
        "publish": st_publish}
NON_RETRYABLE = (costs.BudgetExceeded,)


# ------------------------------------------------------------------ runner
def acquire(jid: int) -> bool:
    with db.tx() as c:
        r = c.execute("SELECT status, locked_by, locked_at FROM jobs WHERE id=?", (jid,)).fetchone()
        if not r or r["status"] in ("done", "cancelled"):
            return False
        if r["locked_by"] and r["locked_by"] != WORKER_ID and time.time() - (r["locked_at"] or 0) < STALE_LOCK_S:
            return False
        c.execute("UPDATE jobs SET locked_by=?, locked_at=?, status='running', updated_at=? WHERE id=?",
                  (WORKER_ID, time.time(), time.time(), jid))
    return True


def release(jid: int, status: str, error: str | None = None) -> None:
    db.ex("UPDATE jobs SET locked_by=NULL, status=?, error=?, updated_at=? WHERE id=?", (status, error, time.time(), jid))


def run_job(jid: int, until: str | None = None) -> dict:
    """Run (or resume) a job until done/failed. `until` stops after the named stage."""
    if not acquire(jid):
        return {"job": jid, "status": job(jid)["status"], "note": "not runnable or locked"}
    a = load_settings().automation
    try:
        while True:
            pending = [r for r in stage_rows(jid) if r["status"] != "done"]
            if not pending:
                release(jid, "done")
                db.ex("UPDATE jobs SET output_path=(SELECT path FROM videos WHERE job_id=?) WHERE id=?", (jid, jid))
                logs.event(log, f"job {jid} finished", jid)
                return {"job": jid, "status": "done"}
            stage = pending[0]["stage"]
            if until and STAGES.index(stage) > STAGES.index(until):
                release(jid, "queued")
                return {"job": jid, "status": "queued", "stopped_before": stage}
            if pending[0]["attempts"] >= a.max_stage_attempts:
                raise RuntimeError(f"stage {stage} exhausted {a.max_stage_attempts} attempts: {pending[0]['error']}")
            db.ex("UPDATE stages SET status='running', attempts=attempts+1, started_at=?, error=NULL WHERE job_id=? AND stage=?",
                  (time.time(), jid, stage))
            db.ex("UPDATE jobs SET current_stage=?, updated_at=?, locked_at=? WHERE id=?", (stage, time.time(), time.time(), jid))
            logs.event(log, f"stage {stage} started", jid, stage)
            t0 = time.time()
            try:
                costs.check_budget(jid)
                out = IMPL[stage](jid)
                save(jid, stage, out)
                db.ex("UPDATE stages SET status='done', finished_at=?, output=? WHERE job_id=? AND stage=?",
                      (time.time(), str(out_path(jid, stage)), jid, stage))
                logs.event(log, f"stage {stage} done in {time.time() - t0:.1f}s", jid, stage)
            except SendBack as sb:
                loops = job(jid)["qc_loops"] + 1
                db.ex("UPDATE jobs SET qc_loops=? WHERE id=?", (loops, jid))
                db.ex("UPDATE stages SET status='failed', error=?, finished_at=? WHERE job_id=? AND stage=?",
                      (sb.reason[:2000], time.time(), jid, stage))
                logs.event(log, f"{stage} sent job back to {sb.stage}: {sb.reason}", jid, stage, level=30)
                if loops > a.max_qc_loops + MAX_TOPIC_SWAPS:
                    raise RuntimeError(f"too many send-backs ({loops}); last: {sb.reason}")
                if sb.feedback:
                    set_feedback(jid, sb.stage, sb.feedback)
                reset_from(jid, sb.stage)
                # a send-back is not a failed attempt of the target stage
                db.ex("UPDATE stages SET attempts=0 WHERE job_id=? AND stage IN (%s)" %
                      ",".join("?" * len(STAGES[STAGES.index(sb.stage):])), (jid, *STAGES[STAGES.index(sb.stage):]))
            except NON_RETRYABLE as e:
                db.ex("UPDATE stages SET status='pending', attempts=attempts-1, error=? WHERE job_id=? AND stage=?",
                      (str(e), jid, stage))
                release(jid, "paused", str(e))
                logs.event(log, f"job {jid} paused: {e}", jid, stage, level=30)
                return {"job": jid, "status": "paused", "reason": str(e)}
            except Exception as e:
                tb = traceback.format_exc()[-3000:]
                db.ex("UPDATE stages SET status='failed', error=?, finished_at=? WHERE job_id=? AND stage=?",
                      (f"{e}\n{tb}", time.time(), jid, stage))
                log.error("stage %s failed (attempt %d): %s", stage, pending[0]["attempts"] + 1, e,
                          extra={"job_id": jid, "stage": stage})
                time.sleep(min(60, 5 * 2 ** pending[0]["attempts"]))
    except Exception as e:
        release(jid, "failed", str(e)[:2000])
        log.error("job %s failed: %s", jid, e, extra={"job_id": jid})
        return {"job": jid, "status": "failed", "error": str(e)}


def retry_job(jid: int, from_stage: str | None = None) -> None:
    """Make a failed/paused job runnable again (optionally re-running from a given stage)."""
    if from_stage:
        reset_from(jid, from_stage)
    db.ex("UPDATE stages SET attempts=0 WHERE job_id=? AND status<>'done'", (jid,))
    db.ex("UPDATE jobs SET status='queued', error=NULL, qc_loops=0, locked_by=NULL WHERE id=?", (jid,))


def recover_stale() -> int:
    """After a crash: jobs left 'running' with an old lock become runnable again."""
    rows = db.q("SELECT id FROM jobs WHERE status='running' AND (locked_at IS NULL OR locked_at<?)",
                (time.time() - STALE_LOCK_S,))
    for r in rows:
        db.ex("UPDATE jobs SET status='queued', locked_by=NULL WHERE id=?", (r["id"],))
        db.ex("UPDATE stages SET status='pending' WHERE job_id=? AND status='running'", (r["id"],))
    return len(rows)


def recover_own_on_start() -> int:
    """Jobs marked running by a previous process of this worker are resumable immediately."""
    rows = db.q("SELECT id, locked_by FROM jobs WHERE status='running'")
    n = 0
    for r in rows:
        host = (r["locked_by"] or "").split(":")[0]
        if host == socket.gethostname():
            db.ex("UPDATE jobs SET status='queued', locked_by=NULL WHERE id=?", (r["id"],))
            db.ex("UPDATE stages SET status='pending' WHERE job_id=? AND status='running'", (r["id"],))
            n += 1
    return n


# ------------------------------------------------------------------ scheduling
def videos_done_today() -> int:
    d = datetime.now(timezone.utc)
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()
    return db.q1("SELECT COUNT(*) n FROM jobs WHERE created_at>=? AND status<>'cancelled'", (start,))["n"]


def slot_times(a) -> list[str]:
    """Configured run times; if fewer than videos_per_day, add evenly spaced slots after the first."""
    times = sorted(a.run_times_utc) or ["09:30"]
    n = min(a.videos_per_day, a.max_videos_per_day)
    if len(times) < n:
        hh, mm = map(int, times[0].split(":"))
        step = (24 * 60 - (hh * 60 + mm)) // n
        times = [f"{(hh * 60 + mm + i * step) // 60 % 24:02d}:{(hh * 60 + mm + i * step) % 60:02d}" for i in range(n)]
    return times[:n]


def schedule_due(now: datetime | None = None) -> bool:
    """True once per configured slot per day while automation is enabled and under the daily limit."""
    a = load_settings().automation
    if not a.enabled:
        return False
    now = now or datetime.now(timezone.utc)
    if videos_done_today() >= min(a.videos_per_day, a.max_videos_per_day):
        return False
    fired = db.kv_get("schedule_fired", [])
    for t in slot_times(a):
        hh, mm = map(int, t.split(":"))
        key = f"{now:%Y-%m-%d} {t}"
        if now >= now.replace(hour=hh, minute=mm, second=0, microsecond=0) and key not in fired:
            db.kv_set("schedule_fired", (fired + [key])[-100:])
            return True
    return False


def next_runnable() -> int | None:
    r = db.q1("SELECT id FROM jobs WHERE status='queued' ORDER BY id LIMIT 1")
    return r["id"] if r else None


def tick() -> dict:
    """One worker iteration: recover, schedule, run one job, refresh analytics."""
    recover_stale()
    a = load_settings().automation
    created = None
    if schedule_due():
        created = create_job()
    jid = next_runnable() if a.enabled or created else None
    res = run_job(jid) if jid else None
    last = db.kv_get("analytics_last", 0)
    if a.enabled and time.time() - last > a.analytics_refresh_hours * 3600 and publish.status()["authorized"]:
        try:
            analytics.fetch()
            analytics.learn()
        except Exception as e:
            log.warning("analytics refresh failed: %s", e)
        db.kv_set("analytics_last", time.time())
    return {"created": created, "ran": res}
