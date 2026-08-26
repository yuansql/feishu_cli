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


def add_trigger(
    *,
    goal: str,
    schedule: str,
    title: str = "",
    chat_id: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    """Add a new trigger and return its spec."""
    cleaned_goal = (goal or "").strip()
    if not cleaned_goal:
        raise ValueError("goal 不能为空")
    spec = parse_schedule(schedule)
    now = _now()
    next_at = next_run(spec, after=now)
    if next_at is None and spec["type"] == "once":
        raise ValueError("一次性触发器必须设置在未来")
    trigger_id = _new_id()
    body: dict[str, Any] = {
        "id": trigger_id,
        "title": (title or cleaned_goal[:30]).strip(),
        "goal": cleaned_goal,
        "chat_id": (chat_id or "").strip(),
        "schedule": schedule,
        "schedule_spec": spec,
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
        if enabled:
            # Recompute next run from now when enabling.
            schedule = spec.get("schedule_spec") or parse_schedule(
                spec.get("schedule", "")
            )
            nxt = next_run(schedule, after=now)
            next_at = _iso(nxt) if nxt else ""
        else:
            next_at = ""
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
    schedule = spec.get("schedule_spec") or parse_schedule(
        spec.get("schedule", "")
    )
    if schedule["type"] == "once":
        next_at = ""
        enabled = 0
    else:
        nxt = next_run(schedule, after=now)
        next_at = _iso(nxt) if nxt else ""
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


def format_triggers_text(triggers: list[dict[str, Any]]) -> str:
    if not triggers:
        return "暂无触发器。使用 `feishu triggers add --help` 创建。"
    lines = [f"触发器 {len(triggers)} 条：", ""]
    for t in triggers:
        status = "启用" if t.get("enabled") else "停用"
        title = str(t.get("title") or t.get("goal") or "(无标题)")[:30]
        goal = str(t.get("goal") or "")[:40]
        next_at = t.get("next_run_at") or "—"
        last_at = t.get("last_run_at") or "从未"
        lines.append(
            f"- {t.get('id')} · [{status}] {title}"
        )
        lines.append(
            f"  计划：{t.get('schedule')} · 下次：{next_at} · 上次：{last_at} · 已运行 {t.get('run_count', 0)} 次"
        )
        if goal:
            lines.append(f"  目标：{goal}")
    return "\n".join(lines)
