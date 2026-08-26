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
APPROVAL_TIMEOUT_SEC = 300  # 5 minutes default approval window


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

            CREATE TABLE IF NOT EXISTS approvals (
                task_id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL UNIQUE,
                chat_id TEXT NOT NULL DEFAULT '',
                tool TEXT NOT NULL,
                args_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_at TEXT NOT NULL,
                resolved_at TEXT,
                expires_at TEXT NOT NULL DEFAULT '',
                operator_id TEXT,
                token TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES runs(task_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_approvals_message ON approvals(message_id);
            CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, requested_at);
            CREATE INDEX IF NOT EXISTS idx_approvals_expires ON approvals(status, expires_at);
            CREATE INDEX IF NOT EXISTS idx_approvals_chat ON approvals(chat_id, status);
            """
        )
        conn.commit()
        _migrate_approvals(conn)


def _now() -> datetime:
    return datetime.now(CN_TZ)


def _iso(now: datetime | None = None) -> str:
    return (now or _now()).isoformat(timespec="seconds")


def _migrate_approvals(conn: sqlite3.Connection) -> None:
    """Ensure the approvals table has expires_at and chat_id columns."""
    columns = {
        str(row[1]) for row in conn.execute(
            "PRAGMA table_info(approvals)"
        ).fetchall()
    }
    if "expires_at" not in columns:
        conn.execute(
            "ALTER TABLE approvals ADD COLUMN expires_at TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_approvals_expires ON approvals(status, expires_at)"
        )
        # Older rows without an explicit expiry are considered to expire one
        # day after they were requested.
        conn.execute(
            """
            UPDATE approvals
            SET expires_at = datetime(requested_at, '+1 day')
            WHERE expires_at = '' AND status = 'pending'
            """
        )
    if "chat_id" not in columns:
        conn.execute(
            "ALTER TABLE approvals ADD COLUMN chat_id TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_approvals_chat ON approvals(chat_id, status)"
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


def request_approval(
    *,
    task_id: str,
    message_id: str,
    chat_id: str = "",
    tool: str,
    args: dict[str, Any],
    operator_id: str = "",
    token: str = "",
    timeout_sec: int = APPROVAL_TIMEOUT_SEC,
    now: datetime | None = None,
) -> bool:
    """Record a pending approval. Returns True if newly created, False if already exists."""
    init_db()
    tid = (task_id or "").strip()
    mid = (message_id or "").strip()
    if not tid or not mid:
        return False
    stamp = now or _now()
    requested_at = _iso(stamp)
    timeout = int(timeout_sec)
    if timeout < 0:
        seconds = 0
    else:
        seconds = max(30, timeout)
    expires_at = _iso(stamp + timedelta(seconds=seconds))
    with _connect() as conn:
        row = conn.execute(
            "SELECT task_id FROM approvals WHERE message_id = ?",
            (mid,),
        ).fetchone()
        if row:
            return False
        conn.execute(
            """
            INSERT INTO approvals (
                task_id, message_id, chat_id, tool, args_json, status,
                requested_at, resolved_at, expires_at, operator_id, token
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                message_id = excluded.message_id,
                chat_id = excluded.chat_id,
                tool = excluded.tool,
                args_json = excluded.args_json,
                status = 'pending',
                requested_at = excluded.requested_at,
                resolved_at = NULL,
                operator_id = excluded.operator_id,
                token = excluded.token
            """,
            (
                tid,
                mid,
                (chat_id or "").strip(),
                (tool or "").strip(),
                json.dumps(args or {}, ensure_ascii=False),
                requested_at,
                None,
                expires_at,
                (operator_id or "").strip(),
                (token or "").strip(),
            ),
        )
        conn.commit()
    return True


def _resolve_approval(
    message_id: str,
    status: str,
    *,
    operator_id: str = "",
    token: str = "",
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Resolve an approval if it is pending and token matches. Returns resolved row or None."""
    init_db()
    mid = (message_id or "").strip()
    if not mid or status not in {"approved", "declined", "expired"}:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM approvals WHERE message_id = ?",
            (mid,),
        ).fetchone()
        if not row:
            return None
        current_status = str(row["status"] or "")
        if current_status == status:
            return dict(row)
        if current_status != "pending":
            return None
        stored_token = str(row["token"] or "")
        if stored_token and stored_token != (token or "").strip():
            return None
        ts = _iso(now or _now())
        conn.execute(
            """
            UPDATE approvals
            SET status = ?, resolved_at = ?, operator_id = ?
            WHERE message_id = ?
            """,
            (
                status,
                ts,
                (operator_id or "").strip(),
                mid,
            ),
        )
        conn.commit()
        refreshed = conn.execute(
            "SELECT * FROM approvals WHERE message_id = ?",
            (mid,),
        ).fetchone()
        return dict(refreshed) if refreshed else None


def approve_approval(
    message_id: str,
    *,
    operator_id: str = "",
    token: str = "",
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Approve a pending approval."""
    return _resolve_approval(
        message_id, "approved", operator_id=operator_id, token=token, now=now
    )


def decline_approval(
    message_id: str,
    *,
    operator_id: str = "",
    token: str = "",
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Decline a pending approval."""
    return _resolve_approval(
        message_id, "declined", operator_id=operator_id, token=token, now=now
    )


def get_approval(message_id: str) -> dict[str, Any] | None:
    init_db()
    mid = (message_id or "").strip()
    if not mid:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM approvals WHERE message_id = ?",
            (mid,),
        ).fetchone()
    return dict(row) if row else None


def get_approval_by_task(task_id: str) -> dict[str, Any] | None:
    init_db()
    tid = (task_id or "").strip()
    if not tid:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? ORDER BY requested_at DESC LIMIT 1",
            (tid,),
        ).fetchone()
    return dict(row) if row else None


def expire_stale_approvals(*, now: datetime | None = None) -> list[dict[str, Any]]:
    """Mark pending approvals past their expiry as expired. Returns affected rows."""
    init_db()
    cutoff = _iso(now or _now())
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM approvals
            WHERE status = 'pending' AND expires_at != '' AND expires_at <= ?
            """,
            (cutoff,),
        ).fetchall()
        out = [dict(r) for r in rows]
        conn.execute(
            """
            UPDATE approvals
            SET status = 'expired', resolved_at = ?
            WHERE status = 'pending' AND expires_at != '' AND expires_at <= ?
            """,
            (cutoff, cutoff),
        )
        conn.commit()
    return out


def list_pending_approvals_for_chat(
    chat_id: str, *, since_iso: str = ""
) -> list[dict[str, Any]]:
    """Return pending approvals for a chat, optionally newer than since_iso."""
    init_db()
    cid = (chat_id or "").strip()
    if not cid:
        return []
    since = since_iso.strip()
    with _connect() as conn:
        if since:
            rows = conn.execute(
                """
                SELECT * FROM approvals
                WHERE chat_id = ? AND status = 'pending' AND requested_at > ?
                ORDER BY requested_at DESC
                """,
                (cid, since),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM approvals
                WHERE chat_id = ? AND status = 'pending'
                ORDER BY requested_at DESC
                """,
                (cid,),
            ).fetchall()
    return [dict(r) for r in rows]


def list_runs_text(*, limit: int = 12, task_id: str = "") -> str:
    init_db()
    if task_id:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT task_id, status, goal, chat_id, lease_owner, lease_until, updated_at
                FROM runs WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        if not row:
            return f"找不到运行记录 {task_id}。"
        payload = load_run(task_id) or {}
        from ..runtime.agent.service import format_patches_text
        return format_patches_text(payload)
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
