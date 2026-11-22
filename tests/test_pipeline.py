"""Integration tests: the full pipeline with deterministic providers, recovery, send-backs, dashboard."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from shortsengine import db
from shortsengine.pipeline import orchestrator as o


def test_full_pipeline_renders_and_passes_qc(fake_providers):
    jid = o.create_job()
    res = o.run_job(jid)
    assert res["status"] == "done", res
    j = o.job(jid)
    wd = Path(j["workdir"])
    final = wd / "final.mp4"
    assert final.exists()
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(final)],
                                      capture_output=True, text=True).stdout)
    v = next(s for s in probe["streams"] if s["codec_type"] == "video")
    a = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    assert (v["width"], v["height"], v["codec_name"], a["codec_name"]) == (1080, 1920, "h264", "aac")
    qcr = json.loads((wd / "qc.json").read_text())
    assert qcr["passed"], qcr["rejections"]
    # every stage persisted, captions and sources exported, metadata registered
    for s in o.STAGES:
        assert (wd / f"{s}.json").exists()
    assert (wd / "captions.srt").read_text().count("-->") > 5
    r = json.loads((wd / "research.json").read_text())
    assert [c["id"] for c in r["verified_claims"]] == ["C1"]  # hallucinated-number claim rejected
    vrow = db.q1("SELECT * FROM videos WHERE job_id=?", (jid,))
    assert vrow and "Source: Air Carrier Access Act" in vrow["description"]
    assert db.q1("SELECT status FROM topics WHERE id=?", (j["topic_id"],))["status"] == "used"
    assert all(r["license"].startswith("Original") for r in db.q("SELECT license FROM assets WHERE job_id=?", (jid,)))


def test_resume_after_interruption_does_not_redo_finished_stages(fake_providers, monkeypatch):
    jid = o.create_job()
    assert o.run_job(jid, until="script")["status"] == "queued"
    calls_before = len(fake_providers.calls)
    # simulate a crash during storyboard: the job is left 'running' with a stale lock
    db.ex("UPDATE jobs SET status='running', locked_by='otherhost:1', locked_at=0 WHERE id=?", (jid,))
    assert o.recover_stale() == 1
    assert o.run_job(jid, until="storyboard")["status"] == "queued"
    assert len(fake_providers.calls) == calls_before  # topic/research/script were not regenerated
    assert {r["stage"]: r["status"] for r in o.stage_rows(jid)}["storyboard"] == "done"


def test_transient_failure_is_retried(fake_providers, monkeypatch):
    from shortsengine.modules import storyboard
    real = storyboard.build
    state = {"n": 0}

    def flaky(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("transient")
        return real(*a, **k)

    monkeypatch.setattr(storyboard, "build", flaky)
    monkeypatch.setattr(o.time, "sleep", lambda s: None)
    jid = o.create_job()
    assert o.run_job(jid, until="storyboard")["status"] == "queued"
    row = {r["stage"]: r for r in o.stage_rows(jid)}["storyboard"]
    assert row["status"] == "done" and row["attempts"] == 2


def test_research_rejection_sends_back_to_new_topic(fake_providers, monkeypatch):
    from shortsengine.modules import research
    real = research.run
    state = {"n": 0}

    def reject_first(t, jid=None):
        state["n"] += 1
        if state["n"] == 1:
            raise research.ResearchRejected("unverifiable")
        return real(t, jid)

    monkeypatch.setattr(research, "run", reject_first)
    jid = o.create_job()
    assert o.run_job(jid, until="research")["status"] == "queued"
    rejected = db.q("SELECT id FROM topics WHERE status='rejected'")
    assert len(rejected) == 1 and o.job(jid)["topic_id"] != rejected[0]["id"]


def test_budget_pauses_job(fake_providers):
    from shortsengine import costs
    from shortsengine.config import update_settings
    update_settings({"automation": {"daily_budget_usd": 0.001}})
    costs.record("anthropic", "llm", 1, "tokens", 0.01)
    jid = o.create_job()
    assert o.run_job(jid)["status"] == "paused"
    assert o.job(jid)["status"] == "paused"


def test_scheduler_respects_enable_and_daily_limit(fake_providers):
    from datetime import datetime, timezone
    from shortsengine.config import update_settings
    now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert not o.schedule_due(now)  # automation disabled by default
    update_settings({"automation": {"enabled": True, "videos_per_day": 2, "run_times_utc": ["09:00"]}})
    assert o.slot_times(o.load_settings().automation)[0] == "09:00" and len(o.slot_times(o.load_settings().automation)) == 2
    assert o.schedule_due(now)
    assert not o.schedule_due(now)  # same slot does not fire twice


def test_dashboard_endpoints(fake_providers):
    from fastapi.testclient import TestClient
    from shortsengine.web.app import create_app
    c = TestClient(create_app(start_worker=False))
    assert c.get("/").status_code == 200
    ov = c.get("/api/overview").json()
    assert ov["automation_enabled"] is False
    ch = c.get("/api/channel").json()
    assert len(ch["dataset"]) == 91 and ch["dna"]["active_format"] == "moral_reversal_drama"
    assert c.get("/api/channel/report/channel_audit.md").status_code == 200
    assert c.get("/api/channel/report/../README.md").status_code == 404
    assert c.post("/api/automation", json={"enabled": True}).json()["enabled"] is True
    assert c.get("/api/settings").json()["automation"]["enabled"] is True
    assert c.put("/api/settings", json={"video": {"width": 1081}}).status_code == 400  # validation
    jid = o.create_job()
    o.run_job(jid, until="storyboard")
    assert c.get(f"/api/jobs/{jid}/stage/script").json()["lines"]
    r = c.put(f"/api/jobs/{jid}/storyboard", json={"shots": [{"n": 1, "camera": "pan_left"}]}).json()
    assert r["rerun_from"] == "edit"
    assert c.get(f"/api/jobs/{jid}").json()["stages"][-4]["status"] == "pending"
    assert c.get("/api/costs").json()["today_usd"] == 0
    assert c.get("/api/providers").json()["secrets"]["ANTHROPIC_API_KEY"]["configured"] is False
    assert c.get("/media/999/final.mp4").status_code in (404, 500)
