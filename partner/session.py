"""Last P2P turn. Follow-ups reuse it — do not search the follow-up sentence."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CN_TZ = timezone(timedelta(hours=8))
_DEFAULT = Path.home() / ".feishu-partner" / "session.json"
_TTL = timedelta(hours=2)
_FOLLOW = (
    "详细",
    "展开",
    "具体",
    "刚才",
    "你说的",
    "哪件",
    "什么意思",
    "不知道你在说",
    "听不懂",
    "接着",
    "继续说",
    "再说清楚",
)
_INDEX_RE = re.compile(r"^(?:第\s*)?([1-4])(?:\s*份)?$")


def session_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_SESSION")
    if override:
        return Path(override).expanduser()
    return _DEFAULT


def looks_like_followup(raw: str) -> bool:
    text = (raw or "").strip()
    if not text:
        return False
    if pick_index(text) is not None:
        return True
    if text in {"继续", "那个", "这个", "上面的", "刚才那个"}:
        return True
    return any(word in text for word in _FOLLOW)


def pick_index(raw: str) -> int | None:
    text = re.sub(r"[？?。！!，,、\s]+", "", (raw or "").strip())
    match = _INDEX_RE.match(text)
    if not match:
        return None
    return int(match.group(1))


def load_turn(chat_id: str) -> dict[str, Any] | None:
    cid = (chat_id or "").strip()
    if not cid:
        return None
    path = session_path()
    if not path.exists():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(blob, dict):
        return None
    row = blob.get(cid)
    if not isinstance(row, dict):
        return None
    ts = str(row.get("ts") or "")
    try:
        when = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=CN_TZ)
    if datetime.now(CN_TZ) - when.astimezone(CN_TZ) > _TTL:
        return None
    return row


def save_turn(
    chat_id: str,
    *,
    kind: str,
    query: str = "",
    action: str = "",
    pairs: list[tuple[str, str]] | list[list[str]] | None = None,
    items: list[dict[str, Any]] | None = None,
) -> None:
    cid = (chat_id or "").strip()
    if not cid:
        return
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    blob: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            blob = loaded
    clean_pairs: list[list[str]] = []
    for item in pairs or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            clean_pairs.append([str(item[0]), str(item[1])])
    clean_items: list[dict[str, str]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("summary") or "")
        guid = str(item.get("guid") or item.get("id") or "")
        if title or guid:
            clean_items.append({"title": title, "guid": guid})
    blob[cid] = {
        "ts": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "kind": kind,
        "query": query,
        "action": action,
        "pairs": clean_pairs,
        "items": clean_items,
    }
    path.write_text(json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8")
