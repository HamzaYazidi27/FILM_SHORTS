"""Long-running worker: scheduling + queue processing + periodic analytics.
Safe to stop at any time (SIGTERM/SIGINT): the current stage's output is persisted per stage
and the job resumes from its first unfinished stage on next start."""
from __future__ import annotations

import signal
import threading
import time

from .. import db, logs
from . import orchestrator

log = logs.get("worker")
_stop = threading.Event()


def stop(*_a) -> None:
    _stop.set()


def heartbeat() -> None:
    db.kv_set("worker_heartbeat", {"ts": time.time(), "id": orchestrator.WORKER_ID})


def loop(poll_s: float = 30.0, once: bool = False) -> None:
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
    n = orchestrator.recover_own_on_start()
    if n:
        log.info("recovered %d interrupted job(s)", n)
    while not _stop.is_set():
        heartbeat()
        try:
            res = orchestrator.tick()
            if res["ran"]:
                log.info("tick: %s", res)
                continue  # look for more work immediately
        except Exception as e:  # keep the worker alive
            log.exception("worker tick failed: %s", e)
        if once:
            break
        _stop.wait(poll_s)


def start_background(poll_s: float = 30.0) -> threading.Thread:
    t = threading.Thread(target=loop, args=(poll_s,), daemon=True, name="shorts-worker")
    t.start()
    return t
