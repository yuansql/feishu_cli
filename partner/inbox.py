"""Local jsonl of messages about 吴梦晨. Source of truth for later digest."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CN_TZ = timezone(timedelta(hours=8))
DEFAULT_PATH = Path.home() / ".feishu-partner" / "inbox.jsonl"


def inbox_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    override = os.environ.get("FEISHU_PARTNER_INBOX")
    if override:
        return Path(override).expanduser()
    return DEFAULT_PATH


def append_item(item: dict[str, Any], *, path: Path | None = None) -> bool:
    """Append unless message_id already stored. True if written."""
    dest = inbox_path(path)
    mid = str(item.get("message_id") or "")
    if mid and any(str(old.get("message_id") or "") == mid for old in _read_all(dest)):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    row = dict(item)
    row.setdefault("ts", datetime.now(CN_TZ).isoformat(timespec="seconds"))
    with dest.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return True


def recent_items(*, days: int = 7, path: Path | None = None) -> list[dict[str, Any]]:
    dest = inbox_path(path)
    cutoff = datetime.now(CN_TZ) - timedelta(days=days)
    out: list[dict[str, Any]] = []
    for item in _read_all(dest):
        ts = _parse_ts(item.get("ts"))
        if ts is None or ts >= cutoff:
            out.append(item)
    return out


def _read_all(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            items.append(row)
    return items


def _parse_ts(raw: Any) -> datetime | None:
    text = str(raw or "")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
