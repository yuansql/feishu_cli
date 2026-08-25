"""SQLite trigger dedup, run index, and worker leases for local Agent runtime."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

CN_TZ = timezone(timedelta(hours=8))
LEASE_TTL_SEC = 120
_TERMINAL = frozenset({"done", "failed", "cancelled"})


def db_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_RUNTIME_DB")
    if override:
        return Path(override).expanduser()
    tasks = os.environ.get("FEISHU_PARTNER_TASKS_DIR")
    if tasks:
        return Path(tasks).expanduser() / "runtime.db"
    return Path.home() / ".feishu-partner" / "runtime.db"


@contextmanager
def _connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


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
            CREATE TABLE IF NOT EXISTS runs (
                task_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                chat_id TEXT NOT NULL DEFAULT '',
                goal TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL,
                lease_owner TEXT NOT NULL DEFAULT '',
                lease_until INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, updated_at);
            """
        )
        conn.commit()


def _now() -> datetime:
    return datetime.now(CN_TZ)


def _iso(now: datetime | None = None) -> str:
    return (now or _now()).isoformat(timespec="seconds")


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
    now = _iso()
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


def upsert_run(task: dict[str, Any]) -> None:
    """Index a task payload. JSON files stay canonical; this is crash/lease index."""
    tid = str(task.get("id") or "").strip()
    if not tid:
        return
    init_db()
    status = str(task.get("status") or "").strip() or "pending"
    payload = json.dumps(task, ensure_ascii=False)
    stamp = _iso()
    clear_lease = status in _TERMINAL
    with _connect() as conn:
        if clear_lease:
            conn.execute(
                """
                INSERT INTO runs (
                    task_id, status, chat_id, goal, payload_json,
                    lease_owner, lease_until, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', 0, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status = excluded.status,
                    chat_id = excluded.chat_id,
                    goal = excluded.goal,
                    payload_json = excluded.payload_json,
                    lease_owner = '',
                    lease_until = 0,
                    updated_at = excluded.updated_at
                """,
                (
                    tid,
                    status,
                    str(task.get("chat_id") or ""),
                    str(task.get("goal") or "")[:300],
                    payload,
                    stamp,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO runs (
                    task_id, status, chat_id, goal, payload_json,
                    lease_owner, lease_until, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', 0, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status = excluded.status,
                    chat_id = excluded.chat_id,
                    goal = excluded.goal,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    tid,
                    status,
                    str(task.get("chat_id") or ""),
                    str(task.get("goal") or "")[:300],
                    payload,
                    stamp,
                ),
            )
        conn.commit()


def load_run(task_id: str) -> dict[str, Any] | None:
    tid = (task_id or "").strip()
    if not tid:
        return None
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload_json FROM runs WHERE task_id = ?",
            (tid,),
        ).fetchone()
    if row is None:
        return None
    try:
        blob = json.loads(row["payload_json"])
    except json.JSONDecodeError:
        return None
    return blob if isinstance(blob, dict) else None


def acquire_lease(
    task_id: str,
    owner: str,
    *,
    ttl_sec: int = LEASE_TTL_SEC,
    now: datetime | None = None,
) -> bool:
    tid = (task_id or "").strip()
    who = (owner or "").strip()
    if not tid or not who:
        return False
    init_db()
    stamp = now or _now()
    ts = int(stamp.timestamp())
    until = ts + max(30, int(ttl_sec))
    with _connect() as conn:
        row = conn.execute(
            "SELECT lease_owner, lease_until FROM runs WHERE task_id = ?",
            (tid,),
        ).fetchone()
        if row is None:
            return False
        current_owner = str(row["lease_owner"] or "")
        current_until = int(row["lease_until"] or 0)
        held = bool(current_owner) and current_until > ts and current_owner != who
        if held:
            return False
        conn.execute(
            """
            UPDATE runs
            SET lease_owner = ?, lease_until = ?, status = 'running', updated_at = ?
            WHERE task_id = ?
            """,
            (who, until, _iso(stamp), tid),
        )
        conn.commit()
    return True


def recover_expired_runs(*, now: datetime | None = None) -> list[dict[str, Any]]:
    """Expire stale running leases; return payloads that should be requeued."""
    init_db()
    stamp = now or _now()
    ts = int(stamp.timestamp())
    cutoff = _iso(stamp - timedelta(seconds=LEASE_TTL_SEC))
    out: list[dict[str, Any]] = []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT task_id, payload_json FROM runs
            WHERE status = 'running'
              AND (
                    (lease_until > 0 AND lease_until < ?)
                    OR (lease_until = 0 AND updated_at <= ?)
                  )
            """,
            (ts, cutoff),
        ).fetchall()
        for row in rows:
            try:
                blob = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                blob = {"id": str(row["task_id"])}
            if not isinstance(blob, dict):
                blob = {"id": str(row["task_id"])}
            blob["id"] = str(blob.get("id") or row["task_id"])
            blob["status"] = "queued"
            conn.execute(
                """
                UPDATE runs
                SET status = 'queued', lease_owner = '', lease_until = 0,
                    payload_json = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (json.dumps(blob, ensure_ascii=False), _iso(stamp), str(row["task_id"])),
            )
            out.append(blob)
        conn.commit()
    return out


def apply_recovered_leases(
    load: Callable[[str], dict[str, Any] | None],
    save: Callable[[dict[str, Any]], None],
    *,
    now: datetime | None = None,
) -> int:
    """Requeue JSON task files whose SQLite lease expired."""
    n = 0
    for payload in recover_expired_runs(now=now):
        tid = str(payload.get("id") or "")
        task = load(tid) or payload
        if not isinstance(task, dict):
            continue
        if str(task.get("status") or "") not in {"running", "queued"}:
            continue
        task["status"] = "queued"
        task["lease_recovered"] = True
        save(task)
        n += 1
    return n


def list_runs_text(*, limit: int = 12) -> str:
    init_db()
    cap = max(1, min(int(limit), 50))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT task_id, status, goal, chat_id, lease_owner, lease_until, updated_at
            FROM runs
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (cap,),
        ).fetchall()
    if not rows:
        return "还没有运行记录。"
    ts = int(_now().timestamp())
    lines = [f"最近运行 {len(rows)} 条（SQLite {db_path()}）：", ""]
    for row in rows:
        lease = ""
        until = int(row["lease_until"] or 0)
        owner = str(row["lease_owner"] or "")
        if owner and until > ts:
            lease = f" · 租约 {owner} 至 {until}"
        elif owner:
            lease = f" · 租约已过期 ({owner})"
        goal = str(row["goal"] or "").strip() or "(无目标)"
        if len(goal) > 40:
            goal = goal[:40] + "…"
        lines.append(
            f"- {row['task_id']} · {row['status']} · {goal}{lease}"
        )
        lines.append(f"  更新 {row['updated_at']}")
    return "\n".join(lines)
