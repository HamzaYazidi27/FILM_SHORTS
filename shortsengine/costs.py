"""Cost tracking and budget enforcement.

Prices are per-provider estimates kept in one table; every provider call records units and
USD so the dashboard can show spend per day/job/stage, and the orchestrator can refuse work
that would exceed the configured daily or per-video budget."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from . import db
from .config import load_settings

# USD per unit. LLM prices per 1M tokens (input, output) from the provider's public pricing.
LLM_PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-2": (8.00, 40.00),
}
IMAGE_PRICES = {"pollinations": 0.0, "openai": 0.04, "procedural": 0.0}
TTS_PRICES_PER_1K_CHARS = {"edge": 0.0, "espeak": 0.0, "elevenlabs": 0.30, "openai": 0.015}


class BudgetExceeded(RuntimeError):
    pass


def record(provider: str, kind: str, units: float, unit: str, usd: float, job_id: int | None = None,
           stage: str | None = None, meta: dict | None = None) -> None:
    db.ex("INSERT INTO costs(job_id,stage,provider,kind,units,unit,usd,meta,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
          (job_id, stage, provider, kind, units, unit, round(usd, 6), json.dumps(meta or {}), time.time()))


def llm_cost(model: str, in_tok: int, out_tok: int) -> float:
    pin, pout = LLM_PRICES.get(model, (0.0, 0.0))
    return in_tok / 1e6 * pin + out_tok / 1e6 * pout


def _day_start(ts: float | None = None) -> float:
    d = datetime.fromtimestamp(ts or time.time(), timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()


def spent_today() -> float:
    r = db.q1("SELECT COALESCE(SUM(usd),0) s FROM costs WHERE created_at>=?", (_day_start(),))
    return r["s"] if r else 0.0


def spent_job(job_id: int) -> float:
    r = db.q1("SELECT COALESCE(SUM(usd),0) s FROM costs WHERE job_id=?", (job_id,))
    return r["s"] if r else 0.0


def check_budget(job_id: int | None = None, upcoming_usd: float = 0.0) -> None:
    a = load_settings().automation
    if spent_today() + upcoming_usd > a.daily_budget_usd:
        raise BudgetExceeded(f"daily budget ${a.daily_budget_usd:.2f} reached (spent ${spent_today():.4f})")
    if job_id is not None and spent_job(job_id) + upcoming_usd > a.per_video_budget_usd:
        raise BudgetExceeded(f"per-video budget ${a.per_video_budget_usd:.2f} reached for job {job_id}")


def summary(days: int = 30) -> dict:
    since = _day_start() - (days - 1) * 86400
    by_day = db.q("SELECT date(created_at,'unixepoch') d, ROUND(SUM(usd),4) usd, COUNT(*) n FROM costs "
                  "WHERE created_at>=? GROUP BY d ORDER BY d", (since,))
    by_provider = db.q("SELECT provider, kind, ROUND(SUM(usd),4) usd, ROUND(SUM(units),1) units, unit, COUNT(*) calls "
                       "FROM costs WHERE created_at>=? GROUP BY provider, kind, unit ORDER BY usd DESC", (since,))
    by_job = db.q("SELECT job_id, ROUND(SUM(usd),4) usd, COUNT(*) calls FROM costs WHERE job_id IS NOT NULL "
                  "GROUP BY job_id ORDER BY job_id DESC LIMIT 50")
    a = load_settings().automation
    return {"today_usd": round(spent_today(), 4), "daily_budget_usd": a.daily_budget_usd,
            "per_video_budget_usd": a.per_video_budget_usd, "by_day": by_day, "by_provider": by_provider,
            "by_job": by_job}
