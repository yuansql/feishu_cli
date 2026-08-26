"""Declarative scheduled triggers for Agent v2 tasks.

Supports:
- daily@HH:MM
- weekly@DowHH:MM  (Dow: mon/tue/wed/thu/fri/sat/sun, case-insensitive)
- cron@<5 standard cron fields>
- once@YYYY-MM-DDTHH:MM

Triggers live in the same SQLite database as runs / approvals so the runtime
can poll them cheaply without extra files.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .run_store import _connect, _iso, _now

CN_TZ = timezone(timedelta(hours=8))

_MAX_AHEAD_DAYS = 366

_DOW_MAP = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_DAILY_RE = re.compile(r"^daily@(\d{1,2}):(\d{1,2})$", re.IGNORECASE)
_WEEKLY_RE = re.compile(
    r"^weekly@([a-z]+)[-\s]?(\d{1,2}):(\d{1,2})$", re.IGNORECASE
)
_ONCE_RE = re.compile(r"^once@(.+)$", re.IGNORECASE)
_CRON_RE = re.compile(r"^cron@(.+)$", re.IGNORECASE)


def _ensure_table(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS triggers (
            id TEXT PRIMARY KEY,
            spec_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            next_run_at TEXT NOT NULL DEFAULT '',
            last_run_at TEXT NOT NULL DEFAULT '',
            run_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_triggers_next ON triggers(enabled, next_run_at)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_triggers_id ON triggers(id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trigger_event_log (
            id TEXT PRIMARY KEY,
            trigger_id TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL,
            external_id TEXT NOT NULL,
            task_id TEXT NOT NULL DEFAULT '',
            matched_condition_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'fired',
            fired_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_trigger_log_trigger ON trigger_event_log(trigger_id, fired_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_trigger_log_external ON trigger_event_log(source, external_id)"
    )


def _parse_time(hour: int, minute: int) -> tuple[int, int]:
    if not (0 <= hour <= 23):
        raise ValueError(f"小时必须在 0-23 之间， got {hour}")
    if not (0 <= minute <= 59):
        raise ValueError(f"分钟必须在 0-59 之间， got {minute}")
    return hour, minute


def _parse_day_name(name: str) -> int:
    key = name.strip().lower()
    if key in _DOW_MAP:
        return _DOW_MAP[key]
    raise ValueError(
        f"未知的星期名称：{name}。支持 mon/tue/wed/thu/fri/sat/sun"
    )


def _parse_iso(value: str) -> datetime:
    value = value.strip().replace(" ", "T")
    # Allow YYYY-MM-DDTHH:MM without seconds/timezone.
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CN_TZ)
            return dt.astimezone(CN_TZ)
        except ValueError:
            continue
    raise ValueError(
        f"无法解析时间：{value}。支持 ISO 格式如 2026-08-27T09:00"
    )


def _field_match(value: int, expr: str, lo: int, hi: int) -> bool:
    """Match a single cron field expression."""
    expr = expr.strip()
    if expr == "*":
        return True
    parts = expr.split(",")
    for part in parts:
        part = part.strip()
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            try:
                step = int(step_s)
            except ValueError as exc:
                raise ValueError(f"非法 cron step：{part}") from exc
            part = base
        else:
            base = part
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-", 1)
            try:
                start = int(a)
                end = int(b)
            except ValueError as exc:
                raise ValueError(f"非法 cron range：{part}") from exc
        else:
            try:
                start = end = int(base)
            except ValueError as exc:
                raise ValueError(f"非法 cron value：{part}") from exc
        if step < 1:
            raise ValueError(f"cron step 必须 ≥1：{part}")
        if not (lo <= start <= hi and lo <= end <= hi and start <= end):
            raise ValueError(f"cron value 超出范围 [{lo},{hi}]：{part}")
        if value in range(start, end + 1, step):
            return True
    return False


def _python_to_cron_weekday(python_wd: int) -> int:
    """Map python weekday (0=Mon..6=Sun) to cron weekday (0=Sun,1=Mon..6=Sat).

    Cron allows both 0 and 7 for Sunday; we consistently return 0.
    """
    if python_wd == 6:
        return 0
    return python_wd + 1


def parse_schedule(expr: str) -> dict[str, Any]:
    """Parse a schedule expression into a specification dict.

    Raises ValueError with a Chinese message on bad input.
    """
    expr = (expr or "").strip()
    if not expr:
        raise ValueError("schedule 不能为空")

    daily = _DAILY_RE.match(expr)
    if daily:
        hour, minute = _parse_time(int(daily.group(1)), int(daily.group(2)))
        return {"type": "daily", "hour": hour, "minute": minute}

    weekly = _WEEKLY_RE.match(expr)
    if weekly:
        weekday = _parse_day_name(weekly.group(1))
        hour, minute = _parse_time(
            int(weekly.group(2)), int(weekly.group(3))
        )
        return {
            "type": "weekly",
            "weekday": weekday,
            "hour": hour,
            "minute": minute,
        }

    once = _ONCE_RE.match(expr)
    if once:
        dt = _parse_iso(once.group(1))
        if dt <= _now():
            raise ValueError("一次性触发器的时间必须在未来")
        return {"type": "once", "dt": dt}

    cron = _CRON_RE.match(expr)
    if cron:
        fields = cron.group(1).strip().split()
        if len(fields) != 5:
            raise ValueError(
                "cron 表达式需要 5 个字段：分 时 日 月 周"
            )
        minute_f, hour_f, day_f, month_f, weekday_f = fields
        # Validate by trying to match one sample value in each range.
        for field, lo, hi in (
            (minute_f, 0, 59),
            (hour_f, 0, 23),
            (day_f, 1, 31),
            (month_f, 1, 12),
            (weekday_f, 0, 7),
        ):
            _field_match(lo, field, lo, hi)
        return {
            "type": "cron",
            "minute": minute_f,
            "hour": hour_f,
            "day": day_f,
            "month": month_f,
            "weekday": weekday_f,
        }

    raise ValueError(
        f"不支持的 schedule 格式：{expr}。"
        "示例：daily@09:00 / weekly@Mon09:00 / cron@*/15 9 * * 1-5 / once@2026-08-27T09:00"
    )


def _time_for_candidate(
    dt: datetime, hour: int, minute: int
) -> datetime:
    """Return a candidate datetime on dt's calendar day with given time."""
    return dt.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _next_daily(
    schedule: dict[str, Any], after: datetime
) -> datetime:
    cand = _time_for_candidate(after, schedule["hour"], schedule["minute"])
    if cand > after:
        return cand
    return cand + timedelta(days=1)


def _next_weekly(
    schedule: dict[str, Any], after: datetime
) -> datetime:
    target_weekday = schedule["weekday"]
    cand = _time_for_candidate(after, schedule["hour"], schedule["minute"])
    delta = (target_weekday - cand.weekday()) % 7
    cand = cand + timedelta(days=delta)
    if cand > after:
        return cand
    return cand + timedelta(days=7)


def _next_cron(
    schedule: dict[str, Any], after: datetime
) -> datetime | None:
    minute_f = schedule["minute"]
    hour_f = schedule["hour"]
    day_f = schedule["day"]
    month_f = schedule["month"]
    weekday_f = schedule["weekday"]
    cur = after + timedelta(minutes=1)
    end = after + timedelta(days=_MAX_AHEAD_DAYS)
    while cur <= end:
        if (
            _field_match(cur.minute, minute_f, 0, 59)
            and _field_match(cur.hour, hour_f, 0, 23)
            and _field_match(cur.day, day_f, 1, 31)
            and _field_match(cur.month, month_f, 1, 12)
            and _field_match(
                _python_to_cron_weekday(cur.weekday()),
                weekday_f,
                0,
                7,
            )
        ):
            return cur.replace(second=0, microsecond=0)
        cur += timedelta(minutes=1)
    return None


def next_run(
    schedule: dict[str, Any], after: datetime | None = None
) -> datetime | None:
    """Return the next run time (CN_TZ) strictly after *after*."""
    after = (after or _now()).astimezone(CN_TZ)
    stype = schedule.get("type")
    if stype == "daily":
        return _next_daily(schedule, after)
    if stype == "weekly":
        return _next_weekly(schedule, after)
    if stype == "once":
        dt = schedule["dt"].astimezone(CN_TZ)
        return dt if dt > after else None
    if stype == "cron":
        return _next_cron(schedule, after)
    raise ValueError(f"未知的 schedule 类型：{stype}")


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _row_to_spec(row: Any) -> dict[str, Any]:
    spec = json.loads(row["spec_json"])
    spec["enabled"] = bool(row["enabled"])
    spec["next_run_at"] = str(row["next_run_at"] or "")
    spec["last_run_at"] = str(row["last_run_at"] or "")
    spec["run_count"] = int(row["run_count"] or 0)
    spec["created_at"] = str(row["created_at"] or "")
    spec["updated_at"] = str(row["updated_at"] or "")
    return spec


def _validate_spec_fields(
    source: str,
    condition: dict[str, Any],
    schedule: str,
) -> None:
    source = (source or "schedule").strip().lower()
    if source not in {"schedule", "message", "webhook"}:
        raise ValueError("source 必须是 schedule、message 或 webhook")
    if source == "schedule":
        if not schedule:
            raise ValueError("schedule 触发器必须提供 --schedule")
    else:
        # Message/webhook triggers do not have a next_run_at schedule.
        if schedule:
            raise ValueError(f"{source} 触发器不需要 --schedule")
        if source == "message":
            keywords = condition.get("keywords") or []
            if not keywords:
                raise ValueError("message 触发器至少需要 --condition-keywords")
        elif source == "webhook":
            path = (condition.get("webhook_path") or "").strip()
            if not path:
                raise ValueError("webhook 触发器必须提供 --condition-webhook-path")


def add_trigger(
    *,
    goal: str,
    schedule: str = "",
    source: str = "schedule",
    condition: dict[str, Any] | None = None,
    title: str = "",
    chat_id: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    """Add a new trigger and return its spec.

    ``source`` may be schedule, message, or webhook. ``condition`` holds
    source-specific matching rules (keywords, chat_type, webhook_path, ...).
    """
    cleaned_goal = (goal or "").strip()
    if not cleaned_goal:
        raise ValueError("goal 不能为空")
    cond = dict(condition) if condition else {}
    source = (source or "schedule").strip().lower()
    _validate_spec_fields(source, cond, schedule)
    now = _now()
    schedule_spec: dict[str, Any] | None = None
    next_at: datetime | None = None
    if source == "schedule":
        schedule_spec = parse_schedule(schedule)
        next_at = next_run(schedule_spec, after=now)
        if next_at is None and schedule_spec["type"] == "once":
            raise ValueError("一次性触发器必须设置在未来")
    trigger_id = _new_id()
    body: dict[str, Any] = {
        "id": trigger_id,
        "title": (title or cleaned_goal[:30]).strip(),
        "goal": cleaned_goal,
        "chat_id": (chat_id or "").strip(),
        "source": source,
        "condition": cond,
        "schedule": schedule,
        "schedule_spec": schedule_spec,
    }
    with _connect() as conn:
        _ensure_table(conn)
        conn.execute(
            """
            INSERT INTO triggers
                (id, spec_json, enabled, next_run_at, last_run_at,
                 run_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trigger_id,
                json.dumps(body, ensure_ascii=False, default=str),
                1 if enabled else 0,
                _iso(next_at) if next_at else "",
                "",
                0,
                _iso(now),
                _iso(now),
            ),
        )
    body.update(
        {
            "enabled": enabled,
            "next_run_at": _iso(next_at) if next_at else "",
            "last_run_at": "",
            "run_count": 0,
            "created_at": _iso(now),
            "updated_at": _iso(now),
        }
    )
    return body


def list_triggers(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    """Return all triggers ordered by next_run_at (empty last)."""
    with _connect() as conn:
        _ensure_table(conn)
        sql = "SELECT * FROM triggers"
        params: tuple[Any, ...] = ()
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY CASE WHEN next_run_at = '' THEN 1 ELSE 0 END, next_run_at, created_at"
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_spec(row) for row in rows]


def get_trigger(trigger_id: str) -> dict[str, Any] | None:
    tid = (trigger_id or "").strip()
    if not tid:
        return None
    with _connect() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM triggers WHERE id = ?", (tid,)
        ).fetchone()
    return _row_to_spec(row) if row else None


def delete_trigger(trigger_id: str) -> bool:
    tid = (trigger_id or "").strip()
    if not tid:
        return False
    with _connect() as conn:
        _ensure_table(conn)
        cur = conn.execute("DELETE FROM triggers WHERE id = ?", (tid,))
    return cur.rowcount > 0


def _recompute_next_run(spec: dict[str, Any], now: datetime) -> str:
    schedule = spec.get("schedule_spec")
    if not schedule and spec.get("source") == "schedule":
        schedule = parse_schedule(spec.get("schedule", ""))
    if schedule:
        nxt = next_run(schedule, after=now)
        return _iso(nxt) if nxt else ""
    return ""


def toggle_trigger(trigger_id: str, enabled: bool) -> dict[str, Any] | None:
    tid = (trigger_id or "").strip()
    if not tid:
        return None
    now = _now()
    with _connect() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM triggers WHERE id = ?", (tid,)
        ).fetchone()
        if not row:
            return None
        spec = _row_to_spec(row)
        next_at = ""
        if enabled:
            next_at = _recompute_next_run(spec, now)
        conn.execute(
            """
            UPDATE triggers
            SET enabled = ?, next_run_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (1 if enabled else 0, next_at, _iso(now), tid),
        )
    return get_trigger(tid)


def poll_due_triggers(
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return enabled triggers whose next_run_at <= now."""
    now = (now or _now()).astimezone(CN_TZ)
    iso = _iso(now)
    with _connect() as conn:
        _ensure_table(conn)
        rows = conn.execute(
            """
            SELECT * FROM triggers
            WHERE enabled = 1 AND next_run_at != '' AND next_run_at <= ?
            ORDER BY next_run_at
            """,
            (iso,),
        ).fetchall()
    return [_row_to_spec(row) for row in rows]


def mark_trigger_run(
    trigger_id: str, now: datetime | None = None
) -> dict[str, Any] | None:
    """Update last_run_at, run_count, and recompute next_run_at."""
    tid = (trigger_id or "").strip()
    if not tid:
        return None
    now = (now or _now()).astimezone(CN_TZ)
    spec = get_trigger(tid)
    if not spec:
        return None
    schedule = spec.get("schedule_spec")
    if not schedule and spec.get("source") == "schedule":
        schedule = parse_schedule(spec.get("schedule", ""))
    if schedule and schedule.get("type") == "once":
        next_at = ""
        enabled = 0
    elif schedule:
        nxt = next_run(schedule, after=now)
        next_at = _iso(nxt) if nxt else ""
        enabled = 1 if spec.get("enabled") else 0
    else:
        next_at = ""
        enabled = 1 if spec.get("enabled") else 0
    run_count = int(spec.get("run_count") or 0) + 1
    with _connect() as conn:
        _ensure_table(conn)
        conn.execute(
            """
            UPDATE triggers
            SET enabled = ?, next_run_at = ?, last_run_at = ?, run_count = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                enabled,
                next_at,
                _iso(now),
                run_count,
                _iso(now),
                tid,
            ),
        )
    return get_trigger(tid)


def run_trigger(
    spec: dict[str, Any],
    *,
    source: str = "schedule",
    external_id: str = "",
    matched_condition: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Start an Agent v2 task for a trigger and log the event.

    Returns (task, message). Idempotent by (source, external_id).
    """
    from ..runtime.agent.service import start_agent_task

    goal = str(spec.get("goal") or "").strip()
    chat_id = (spec.get("chat_id") or "").strip()
    title = str(spec.get("title") or goal or "触发器").strip()
    eid = (external_id or "").strip()
    if eid:
        existing = lookup_trigger_event(source=source, external_id=eid)
        if existing:
            return None, f"触发事件已存在（{eid}），跳过。"
    message = start_agent_task(goal, chat_id, background=True)
    task_id = ""
    for token in message.replace("）", " ").replace("（", " ").split():
        if len(token) == 12 and all(c in "0123456789abcdef" for c in token):
            task_id = token
            break
    record_trigger_event(
        trigger_id=spec.get("id") or "",
        source=source,
        external_id=eid,
        task_id=task_id,
        matched_condition=matched_condition or {},
        status="fired",
    )
    # Update run_count / last_run_at even for event triggers.
    mark_trigger_run(spec.get("id") or "")
    return {"goal": goal, "chat_id": chat_id, "task_id": task_id}, message


def record_trigger_event(
    *,
    trigger_id: str,
    source: str,
    external_id: str,
    task_id: str,
    matched_condition: dict[str, Any] | None = None,
    status: str = "fired",
) -> None:
    """Write a trigger event to the audit log."""
    src = (source or "").strip()
    eid = (external_id or "").strip()
    if not src or not eid:
        return
    now = _now()
    with _connect() as conn:
        _ensure_table(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO trigger_event_log
                (id, trigger_id, source, external_id, task_id,
                 matched_condition_json, status, fired_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                trigger_id or "",
                src,
                eid,
                task_id or "",
                json.dumps(matched_condition or {}, ensure_ascii=False),
                status,
                _iso(now),
                _iso(now),
            ),
        )


def lookup_trigger_event(*, source: str, external_id: str) -> dict[str, Any] | None:
    src = (source or "").strip()
    eid = (external_id or "").strip()
    if not src or not eid:
        return None
    with _connect() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM trigger_event_log WHERE source = ? AND external_id = ?",
            (src, eid),
        ).fetchone()
    if not row:
        return None
    return {
        "id": str(row["id"]),
        "trigger_id": str(row["trigger_id"]),
        "source": str(row["source"]),
        "external_id": str(row["external_id"]),
        "task_id": str(row["task_id"]),
        "status": str(row["status"]),
        "fired_at": str(row["fired_at"]),
    }


def list_trigger_events(
    *, trigger_id: str = "", limit: int = 20
) -> list[dict[str, Any]]:
    tid = (trigger_id or "").strip()
    cap = max(1, min(int(limit), 100))
    with _connect() as conn:
        _ensure_table(conn)
        if tid:
            rows = conn.execute(
                """
                SELECT * FROM trigger_event_log
                WHERE trigger_id = ?
                ORDER BY fired_at DESC, created_at DESC, id DESC
                LIMIT ?
                """,
                (tid, cap),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM trigger_event_log
                ORDER BY fired_at DESC, created_at DESC, id DESC
                LIMIT ?
                """,
                (cap,),
            ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "trigger_id": str(row["trigger_id"]),
            "source": str(row["source"]),
            "external_id": str(row["external_id"]),
            "task_id": str(row["task_id"]),
            "status": str(row["status"]),
            "fired_at": str(row["fired_at"]),
        }
        for row in rows
    ]


def format_trigger_events_text(events: list[dict[str, Any]]) -> str:
    if not events:
        return "暂无触发事件日志。"
    lines = [f"最近触发事件 {len(events)} 条：", ""]
    for e in events:
        lines.append(
            f"- {e['fired_at']} · [{e['source']}] trigger={e['trigger_id']} task={e['task_id']}"
        )
    return "\n".join(lines)


def match_message_trigger(
    spec: dict[str, Any], msg: Any
) -> dict[str, Any] | None:
    """Return matched condition dict if the message satisfies the trigger."""
    if spec.get("source") != "message":
        return None
    if not spec.get("enabled"):
        return None
    condition = spec.get("condition") or {}
    text = str(getattr(msg, "text", "") or "").strip()
    chat_type = str(getattr(msg, "chat_type", "") or "").strip()
    sender_id = str(getattr(msg, "sender_id", "") or "").strip()
    sender_type = str(getattr(msg, "sender_type", "") or "").strip()
    if sender_type in {"app", "bot"}:
        return None
    allowed_chat = (condition.get("chat_type") or "*").strip().lower()
    if allowed_chat and allowed_chat != "*" and chat_type != allowed_chat:
        return None
    allowed_senders = condition.get("sender_id") or []
    if isinstance(allowed_senders, str):
        allowed_senders = [allowed_senders]
    if allowed_senders and sender_id not in allowed_senders:
        return None
    keywords = condition.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    lowered = text.lower()
    matched: list[str] = []
    for kw in keywords:
        kw = str(kw).strip().lower()
        if not kw:
            continue
        if kw in lowered:
            matched.append(kw)
    if not matched:
        return None
    return {"keywords": matched, "chat_type": chat_type, "sender_id": sender_id}


def match_webhook_trigger(
    spec: dict[str, Any], path: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """Return matched condition if the webhook payload satisfies the trigger."""
    if spec.get("source") != "webhook":
        return None
    if not spec.get("enabled"):
        return None
    condition = spec.get("condition") or {}
    expected = (condition.get("webhook_path") or "").strip().lstrip("/")
    req_path = (path or "").strip().lstrip("/")
    if expected and expected != req_path:
        return None
    return {"webhook_path": req_path}


def list_enabled_message_triggers() -> list[dict[str, Any]]:
    return [
        t for t in list_triggers(enabled_only=True) if t.get("source") == "message"
    ]


def list_enabled_webhook_triggers(
    path: str = "",
) -> list[dict[str, Any]]:
    specs = [
        t for t in list_triggers(enabled_only=True) if t.get("source") == "webhook"
    ]
    if not path:
        return specs
    req_path = path.strip().lstrip("/")
    out: list[dict[str, Any]] = []
    for spec in specs:
        cond = spec.get("condition") or {}
        expected = (cond.get("webhook_path") or "").strip().lstrip("/")
        if not expected or expected == req_path:
            out.append(spec)
    return out


def format_triggers_text(triggers: list[dict[str, Any]]) -> str:
    if not triggers:
        return "暂无触发器。使用 `feishu triggers add --help` 创建。"
    lines = [f"触发器 {len(triggers)} 条：", ""]
    for t in triggers:
        status = "启用" if t.get("enabled") else "停用"
        title = str(t.get("title") or t.get("goal") or "(无标题)")[:30]
        goal = str(t.get("goal") or "")[:40]
        source = t.get("source") or "schedule"
        next_at = t.get("next_run_at") or "—"
        last_at = t.get("last_run_at") or "从未"
        cond = t.get("condition") or {}
        cond_text = ""
        if source == "message" and cond.get("keywords"):
            cond_text = f" · 关键词：{','.join(str(k) for k in cond['keywords'])}"
        elif source == "webhook" and cond.get("webhook_path"):
            cond_text = f" · 路径：/{cond['webhook_path']}"
        lines.append(
            f"- {t.get('id')} · [{status}] [{source}] {title}{cond_text}"
        )
        if source == "schedule":
            lines.append(
                f"  计划：{t.get('schedule')} · 下次：{next_at} · 上次：{last_at} · 已运行 {t.get('run_count', 0)} 次"
            )
        else:
            lines.append(
                f"  上次：{last_at} · 已运行 {t.get('run_count', 0)} 次"
            )
        if goal:
            lines.append(f"  目标：{goal}")
    return "\n".join(lines)
