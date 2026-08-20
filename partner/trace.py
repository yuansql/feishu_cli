"""Structured Agent runtime traces with conservative redaction."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CN_TZ = timezone(timedelta(hours=8))
_LOCK = threading.Lock()
_SENSITIVE = ("token", "secret", "password", "authorization", "cookie")


def traces_dir() -> Path:
    override = os.environ.get("FEISHU_PARTNER_TRACES_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner" / "traces"


def _safe(value: Any, *, limit: int = 4000) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if any(part in str(key).lower() for part in _SENSITIVE)
                else _safe(item, limit=limit)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe(item, limit=limit) for item in value[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        text = value if not isinstance(value, str) else value[:limit]
        return text
    return str(value)[:limit]


def emit_trace(task_id: str, event: str, **fields: Any) -> None:
    """Append one redacted JSONL event; tracing must never break task execution."""
    tid = (task_id or "").strip()
    name = (event or "").strip()
    if not tid or not name:
        return
    row = {
        "at": datetime.now(CN_TZ).isoformat(timespec="milliseconds"),
        "task_id": tid,
        "event": name,
        **{str(key): _safe(value) for key, value in fields.items()},
    }
    try:
        root = traces_dir()
        root.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with (root / f"{tid}.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return


def read_traces(task_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    path = traces_dir() / f"{(task_id or '').strip()}.jsonl"
    if not path.is_file():
        return []
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for raw in raw_lines[-max(1, limit) :]:
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows
