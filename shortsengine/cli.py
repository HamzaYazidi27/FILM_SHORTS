"""Command line interface: python -m shortsengine <command>"""
from __future__ import annotations

import argparse
import json
import sys

from . import db, logs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shortsengine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create data dir, settings and database")
    d = sub.add_parser("discover", help="discover and score new topics")
    d.add_argument("--n", type=int, default=8)
    d.add_argument("--no-trends", action="store_true")
    r = sub.add_parser("run", help="create a job and run the full pipeline once")
    r.add_argument("--topic", type=int)
    r.add_argument("--until", help="stop after this stage")
    rj = sub.add_parser("resume", help="resume/run an existing job")
    rj.add_argument("job", type=int)
    rj.add_argument("--until")
    rt = sub.add_parser("retry", help="make a failed job runnable again")
    rt.add_argument("job", type=int)
    rt.add_argument("--from-stage")
    w = sub.add_parser("worker", help="run the scheduler/queue worker")
    w.add_argument("--poll", type=float, default=30)
    w.add_argument("--once", action="store_true")
    s = sub.add_parser("serve", help="run the dashboard (and the worker in the background)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--no-worker", action="store_true")
    sub.add_parser("status", help="print queue status")
    sub.add_parser("youtube-auth", help="authorise YouTube upload via OAuth (one-time)")
    p = sub.add_parser("publish", help="upload a rendered video")
    p.add_argument("video", type=int)
    p.add_argument("--privacy", choices=["private", "unlisted", "public"])
    sub.add_parser("analytics", help="fetch YouTube stats and update the strategy")
    a = ap.parse_args(argv)
    logs.setup()
    db.connect()
    from .pipeline import orchestrator as o
    if a.cmd == "init":
        from .config import load_settings, settings_path
        load_settings()
        print(f"settings: {settings_path()}\ndatabase: {db.db_path()}")
    elif a.cmd == "discover":
        from .modules import topics
        for t in topics.discover(a.n, use_trends=not a.no_trends):
            print(f"{t['score']:.3f}  [{t['theme']}/{t['setting']}]  {t['moral']}")
    elif a.cmd == "run":
        jid = o.create_job(a.topic)
        print(json.dumps(o.run_job(jid, a.until), indent=1))
    elif a.cmd == "resume":
        db.ex("UPDATE jobs SET status='queued' WHERE id=? AND status IN ('failed','paused','queued')", (a.job,))
        print(json.dumps(o.run_job(a.job, a.until), indent=1))
    elif a.cmd == "retry":
        o.retry_job(a.job, a.from_stage)
        print(f"job {a.job} queued")
    elif a.cmd == "worker":
        from .pipeline import worker
        worker.loop(a.poll, a.once)
    elif a.cmd == "serve":
        import uvicorn
        from .web.app import create_app
        uvicorn.run(create_app(start_worker=not a.no_worker), host=a.host, port=a.port)
    elif a.cmd == "status":
        for j in db.q("SELECT id, status, current_stage, quality_score, error FROM jobs ORDER BY id DESC LIMIT 20"):
            print(j)
    elif a.cmd == "youtube-auth":
        from .modules import publish
        publish.authorize()
        print("authorised")
    elif a.cmd == "publish":
        from .modules import publish
        print(json.dumps(publish.upload(a.video, a.privacy), indent=1))
    elif a.cmd == "analytics":
        from .modules import analytics
        print("snapshots:", analytics.fetch())
        print(json.dumps(analytics.learn(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
