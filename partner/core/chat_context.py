"""Ambient chat context: lightweight, local, TTL-bound memory for group/p2p chats.

Stores only metadata (sender, text, @, timestamp) on the local machine. No cloud
upload. Designed to let the Agent/Hermes infer "who said what" and "which topic
was just decided" without full chat history.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CN_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class ChatSignal:
    """One lightweight record extracted from an inbound message."""

    chat_id: str
    message_id: str
    sender_id: str
    sender_name: str = ""
    text: str = ""
    mentions: tuple[str, ...] = field(default_factory=tuple)
    ts: str = ""


_CHAT_CONTEXT_FILE = "chat_context.jsonl"
_DEFAULT_TTL_HOURS = 24
_MAX_ITEMS = 50


def _data_dir() -> Path:
    override = os.environ.get("FEISHU_PARTNER_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner"


def _context_path() -> Path:
    return _data_dir() / _CHAT_CONTEXT_FILE


def ambient_context_enabled() -> bool:
    """Check whether ambient context collection is enabled.

    Order of precedence:
    1. FEISHU_PARTNER_AMBIENT_CONTEXT env ("1"/"true"/"on" vs "0"/"false"/"off")
    2. ~/.feishu-partner/config.json 'ambient_context' key ("on"/"off")
    3. Default True
    """
    env = (os.environ.get("FEISHU_PARTNER_AMBIENT_CONTEXT") or "").strip().lower()
    if env in {"1", "true", "on"}:
        return True
    if env in {"0", "false", "off"}:
        return False
    try:
        cfg_path = _data_dir() / "config.json"
        if cfg_path.is_file():
            blob = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(blob, dict):
                val = str(blob.get("ambient_context") or "").strip().lower()
                if val == "on":
                    return True
                if val == "off":
                    return False
    except (OSError, json.JSONDecodeError):
        pass
    return True


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _parse_iso(value: str) -> datetime:
    try:
        # Python 3.11+ can parse the '+08:00' suffix.
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(CN_TZ)


def ingest(
    *,
    chat_id: str,
    message_id: str,
    sender_id: str,
    sender_name: str = "",
    text: str = "",
    mentions: tuple[str, ...] | None = None,
    ts: str | None = None,
) -> None:
    """Append a chat signal if ambient context is enabled."""
    if not ambient_context_enabled():
        return
    cid = (chat_id or "").strip()
    mid = (message_id or "").strip()
    sid = (sender_id or "").strip()
    if not cid or not mid or not sid:
        return
    path = _context_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "chat_id": cid,
        "message_id": mid,
        "sender_id": sid,
        "sender_name": (sender_name or "").strip() or "某人",
        "text": (text or "").strip()[:500],
        "mentions": list(mentions or ()),
        "ts": (ts or "").strip() or _now_iso(),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def ingest_inbound_message(msg: Any) -> None:
    """Convenience wrapper for InboundMessage-shaped objects."""
    if msg is None:
        return
    mentions = getattr(msg, "mention_ids", None) or ()
    ingest(
        chat_id=str(getattr(msg, "chat_id", "") or ""),
        message_id=str(getattr(msg, "message_id", "") or ""),
        sender_id=str(getattr(msg, "sender_id", "") or ""),
        sender_name=str(getattr(msg, "sender_name", "") or ""),
        text=str(getattr(msg, "text", "") or ""),
        mentions=tuple(mentions) if isinstance(mentions, (list, tuple)) else (),
    )


def load_signals(
    chat_id: str | None = None,
    *,
    hours: int = _DEFAULT_TTL_HOURS,
    max_items: int = _MAX_ITEMS,
    now: datetime | None = None,
) -> list[ChatSignal]:
    """Load recent signals, optionally filtered by chat_id."""
    path = _context_path()
    if not path.is_file():
        return []
    cutoff = (now or datetime.now(CN_TZ)) - timedelta(hours=max(1, int(hours)))
    rows: list[ChatSignal] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    blob = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(blob, dict):
                    continue
                ts = str(blob.get("ts") or "")
                try:
                    dt = _parse_iso(ts)
                except Exception:
                    dt = datetime.now(CN_TZ)
                if dt < cutoff:
                    continue
                cid = str(blob.get("chat_id") or "")
                if chat_id and cid != chat_id:
                    continue
                rows.append(
                    ChatSignal(
                        chat_id=cid,
                        message_id=str(blob.get("message_id") or ""),
                        sender_id=str(blob.get("sender_id") or ""),
                        sender_name=str(blob.get("sender_name") or ""),
                        text=str(blob.get("text") or ""),
                        mentions=tuple(blob.get("mentions") or ()),
                        ts=ts,
                    )
                )
    except OSError:
        return []
    rows.sort(key=lambda s: s.ts)
    return rows[-max_items:]


def recent_context(
    chat_id: str,
    *,
    hours: int = _DEFAULT_TTL_HOURS,
    max_items: int = _MAX_ITEMS,
) -> str:
    """Render a short Markdown summary of recent chat context for prompts."""
    signals = load_signals(chat_id, hours=hours, max_items=max_items)
    if not signals:
        return ""
    lines: list[str] = ["最近群聊上下文："]
    seen: set[str] = set()
    for sig in signals:
        key = f"{sig.ts}:{sig.message_id}"
        if key in seen:
            continue
        seen.add(key)
        who = sig.sender_name or "某人"
        text = sig.text or "(无文本)"
        mention_hint = ""
        if sig.mentions:
            mention_hint = f" [@{','.join(sig.mentions)}]"
        lines.append(f"- {who}{mention_hint}: {text}")
    return "\n".join(lines)


def clear(chat_id: str | None = None) -> int:
    """Clear all chat context, or only for a specific chat."""
    path = _context_path()
    if not path.is_file():
        return 0
    if chat_id is None:
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        return size
    removed = 0
    try:
        keep: list[str] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    blob = json.loads(line)
                except json.JSONDecodeError:
                    keep.append(line)
                    continue
                if isinstance(blob, dict) and str(blob.get("chat_id") or "") == chat_id:
                    removed += 1
                    continue
                keep.append(line)
        path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    except OSError:
        return removed
    return removed


def decay(*, hours: int = _DEFAULT_TTL_HOURS, now: datetime | None = None) -> int:
    """Remove signals older than hours. Returns number of removed records."""
    path = _context_path()
    if not path.is_file():
        return 0
    cutoff = (now or datetime.now(CN_TZ)) - timedelta(hours=max(1, int(hours)))
    removed = 0
    try:
        keep: list[str] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    blob = json.loads(line)
                except json.JSONDecodeError:
                    keep.append(line)
                    continue
                if not isinstance(blob, dict):
                    keep.append(line)
                    continue
                ts = str(blob.get("ts") or "")
                try:
                    dt = _parse_iso(ts)
                except Exception:
                    dt = datetime.now(CN_TZ)
                if dt < cutoff:
                    removed += 1
                    continue
                keep.append(line)
        path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    except OSError:
        return removed
    return removed
