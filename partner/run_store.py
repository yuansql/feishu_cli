"""SQLite trigger dedup and run index for local Agent runtime."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CN_TZ = timezone(timedelta(hours=8))


def db_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_RUNTIME_DB")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner" / "runtime.db"


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trigger_events (
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (source, external_id)
            );
            CREATE INDEX IF NOT EXISTS idx_trigger_task ON trigger_events(task_id);
            """
        )
        conn.commit()


def record_trigger(
    *,
    source: str,
    external_id: str,
    task_id: str,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    """Insert trigger; return (created, task_id). Duplicate returns existing task_id."""
    init_db()
    src = (source or "").strip()
    eid = (external_id or "").strip()
    tid = (task_id or "").strip()
    if not src or not eid or not tid:
        raise ValueError("source, external_id, task_id required")
    now = datetime.now(CN_TZ).isoformat(timespec="seconds")
    with _connect() as conn:
        row = conn.execute(
            "SELECT task_id FROM trigger_events WHERE source = ? AND external_id = ?",
            (src, eid),
        ).fetchone()
        if row:
            return False, str(row["task_id"])
        conn.execute(
            """
            INSERT INTO trigger_events (source, external_id, task_id, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (src, eid, tid, json.dumps(payload, ensure_ascii=False), now),
        )
        conn.commit()
    return True, tid


def lookup_trigger(*, source: str, external_id: str) -> str | None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT task_id FROM trigger_events WHERE source = ? AND external_id = ?",
            ((source or "").strip(), (external_id or "").strip()),
        ).fetchone()
    return str(row["task_id"]) if row else None
