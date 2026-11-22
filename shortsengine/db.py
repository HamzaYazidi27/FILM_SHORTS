"""SQLite persistence with numbered migrations. All workflow state lives here so the
pipeline can resume after interruption and the dashboard can show real status."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import data_dir

MIGRATIONS: list[str] = [
    # 1: core tables
    """
    CREATE TABLE topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        premise TEXT NOT NULL,
        theme TEXT, setting TEXT, moral TEXT,
        fact_angle TEXT,
        score REAL, score_breakdown TEXT,
        sources TEXT,
        status TEXT NOT NULL DEFAULT 'candidate',   -- candidate|selected|used|rejected
        reject_reason TEXT,
        created_at REAL NOT NULL
    );
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic_id INTEGER REFERENCES topics(id),
        status TEXT NOT NULL DEFAULT 'queued',       -- queued|running|done|failed|paused|cancelled
        current_stage TEXT,
        qc_loops INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        workdir TEXT NOT NULL,
        output_path TEXT,
        quality_score REAL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        locked_by TEXT, locked_at REAL
    );
    CREATE TABLE stages (
        job_id INTEGER NOT NULL REFERENCES jobs(id),
        stage TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',      -- pending|running|done|failed|skipped
        attempts INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        output TEXT,
        started_at REAL, finished_at REAL,
        PRIMARY KEY (job_id, stage)
    );
    CREATE TABLE assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER,
        asset_key TEXT NOT NULL,
        kind TEXT NOT NULL,                          -- image|video|audio|music|sfx|font
        path TEXT NOT NULL,
        source TEXT NOT NULL,                        -- provider / user / procedural
        license TEXT NOT NULL,
        license_url TEXT,
        usage_restrictions TEXT,
        prompt TEXT,
        scene TEXT,
        sha256 TEXT,
        created_at REAL NOT NULL
    );
    CREATE TABLE costs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER,
        stage TEXT, provider TEXT, kind TEXT,
        units REAL, unit TEXT, usd REAL NOT NULL,
        meta TEXT,
        created_at REAL NOT NULL
    );
    CREATE TABLE videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER UNIQUE REFERENCES jobs(id),
        title TEXT, description TEXT, tags TEXT,
        path TEXT NOT NULL,
        duration_s REAL,
        youtube_id TEXT,
        privacy TEXT,
        publish_status TEXT NOT NULL DEFAULT 'not_published',  -- not_published|uploading|uploaded|scheduled|failed
        publish_error TEXT,
        published_at REAL,
        features TEXT,                               -- JSON: topic/hook/duration/editing features for learning
        created_at REAL NOT NULL
    );
    CREATE TABLE analytics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER REFERENCES videos(id),
        fetched_at REAL NOT NULL,
        views INTEGER, likes INTEGER, comments INTEGER,
        avg_view_duration_s REAL, avg_view_pct REAL,
        raw TEXT
    );
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL, level TEXT NOT NULL, logger TEXT,
        job_id INTEGER, stage TEXT, message TEXT NOT NULL, data TEXT
    );
    CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
    CREATE INDEX idx_events_ts ON events(ts);
    CREATE INDEX idx_costs_ts ON costs(created_at);
    """,
    # 2: script fingerprints for duplicate detection
    """
    CREATE TABLE scripts (
        job_id INTEGER PRIMARY KEY,
        title TEXT, moral TEXT, hook TEXT,
        text TEXT NOT NULL,
        structure_sig TEXT,
        created_at REAL NOT NULL
    );
    """,
]

_local = threading.local()
_init_lock = threading.Lock()


def db_path() -> Path:
    return data_dir() / "shorts.db"


def connect() -> sqlite3.Connection:
    path = str(db_path())
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == path:
        return conn
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    with _init_lock:
        migrate(conn)
    _local.conn, _local.path = conn, path
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (v INTEGER NOT NULL)")
    row = conn.execute("SELECT v FROM schema_version").fetchone()
    v = row[0] if row else 0
    if not row:
        conn.execute("INSERT INTO schema_version VALUES (0)")
    for i, sql in enumerate(MIGRATIONS[v:], start=v + 1):
        conn.executescript("BEGIN;" + sql + f";UPDATE schema_version SET v={i};COMMIT;")
    return len(MIGRATIONS)


def reset_connection() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
    _local.conn = None


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    c = connect()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise


def q(sql: str, args: tuple | list = ()) -> list[dict]:
    return [dict(r) for r in connect().execute(sql, args).fetchall()]


def q1(sql: str, args: tuple | list = ()) -> dict | None:
    r = connect().execute(sql, args).fetchone()
    return dict(r) if r else None


def ex(sql: str, args: tuple | list = ()) -> int:
    cur = connect().execute(sql, args)
    return cur.lastrowid


def kv_get(k: str, default: Any = None) -> Any:
    r = q1("SELECT v FROM kv WHERE k=?", (k,))
    return json.loads(r["v"]) if r else default


def kv_set(k: str, v: Any) -> None:
    ex("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))


def now() -> float:
    return time.time()
