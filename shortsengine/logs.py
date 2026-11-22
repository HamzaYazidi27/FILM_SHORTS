"""Structured logging: JSON lines to data/logs/engine.jsonl and WARNING+ (plus stage events)
into the events table so the dashboard can show errors per job/stage."""
from __future__ import annotations

import json
import logging
import time
from logging.handlers import RotatingFileHandler

from .config import data_dir

_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, r: logging.LogRecord) -> str:
        d = {"ts": round(r.created, 3), "level": r.levelname, "logger": r.name, "msg": r.getMessage()}
        for k in ("job_id", "stage", "data"):
            if hasattr(r, k):
                d[k] = getattr(r, k)
        if r.exc_info:
            d["exc"] = self.formatException(r.exc_info)
        return json.dumps(d, default=str)


class DBHandler(logging.Handler):
    def emit(self, r: logging.LogRecord) -> None:
        if r.levelno < logging.WARNING and not getattr(r, "event", False):
            return
        try:
            from . import db
            msg = r.getMessage()
            if r.exc_info:
                msg += "\n" + logging.Formatter().formatException(r.exc_info)[-2000:]
            db.ex("INSERT INTO events(ts,level,logger,job_id,stage,message,data) VALUES(?,?,?,?,?,?,?)",
                  (time.time(), r.levelname, r.name, getattr(r, "job_id", None), getattr(r, "stage", None),
                   msg[:4000], json.dumps(getattr(r, "data", None), default=str)))
        except Exception:  # never let logging crash the pipeline
            pass


def setup(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("shorts")
    root.setLevel(level)
    fh = RotatingFileHandler(d / "engine.jsonl", maxBytes=5_000_000, backupCount=3)
    fh.setFormatter(JsonFormatter())
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
    root.addHandler(sh)
    root.addHandler(DBHandler())
    _configured = True


def get(name: str) -> logging.Logger:
    setup()
    return logging.getLogger(f"shorts.{name}")


def event(log: logging.Logger, msg: str, job_id: int | None = None, stage: str | None = None,
          data: dict | None = None, level: int = logging.INFO) -> None:
    """Log a workflow event that is also persisted for the dashboard."""
    log.log(level, msg, extra={"job_id": job_id, "stage": stage, "data": data, "event": True})
