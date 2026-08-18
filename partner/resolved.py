"""Local ledger: P2P/card 「已处理」→ 明早简报不再列。飞书权威失败不假装销账。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CN_TZ = timezone(timedelta(hours=8))
_DEFAULT_RESOLVED = Path.home() / ".feishu-partner" / "resolved.jsonl"
_DEFAULT_PENDING = Path.home() / ".feishu-partner" / "pending.json"

_DONE = (
    "已经解决了",
    "已经处理",
    "已解决",
    "解决了",
    "已处理",
    "搞定了",
    "搞定",
    "不用催了",
    "不用催",
    "处理完了",
    "处理掉了",
    "销了",
)
_HINT_NOISE = (
    "已经解决了",
    "已经处理",
    "已解决",
    "解决了",
    "这块",
    "这个我",
    "这个",
    "回 ",
    "那条",
    "那件事",
    "已处理",
    "搞定了",
    "搞定",
    "不用催了",
    "不用催",
    "处理完了",
    "处理掉了",
    "销了",
    "进行中",
    "未完成",
    "未回复",
    "已追问未答完",
    "待处理",
    "（",
    "）",
    "(",
    ")",
    "·",
)


def assign_reply_body(raw: str) -> str:
    """Extract the new sentence from lark-cli's flattened reply payload."""
    head, separator, body = (raw or "").partition("\n\n")
    if (
        not separator
        or not head.startswith("刚记下")
        or "派你的活：\n" not in head
    ):
        return ""
    return body.strip()


def looks_like_resolve(raw: str) -> bool:
    text = (raw or "").strip()
    if not text:
        return False
    if any(mark in text for mark in ("吗", "？", "?", "有没有", "是不是")):
        return False
    return any(word in text for word in _DONE)


def resolve_hint(raw: str) -> str:
    q = raw or ""
    for noise in _HINT_NOISE:
        q = q.replace(noise, " ")
    return re.sub(r"\s+", " ", q).strip()


def pending_key(*, message_id: str = "", chat_id: str = "", text: str = "") -> str:
    mid = (message_id or "").strip()
    if mid:
        return "om:" + mid
    basis = f"{chat_id}|{(text or '').strip()[:80]}"
    return "fp:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def resolved_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_RESOLVED")
    if override:
        return Path(override).expanduser()
    return _DEFAULT_RESOLVED


def pending_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_PENDING")
    if override:
        return Path(override).expanduser()
    return _DEFAULT_PENDING


def is_resolved(key: str) -> bool:
    token = (key or "").strip()
    if not token:
        return False
    path = resolved_path()
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and str(row.get("key") or "") == token:
            return True
    return False


def mark_resolved(
    key: str,
    *,
    source: str,
    chat_name: str = "",
    snippet: str = "",
) -> bool:
    token = (key or "").strip()
    if not token:
        return False
    if is_resolved(token):
        return True
    dest = resolved_path()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "key": token,
            "source": source,
            "chat_name": chat_name,
            "snippet": snippet[:80],
            "ts": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        }
        with dest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return False
    return is_resolved(token)


def save_pending(items: list[dict[str, Any]]) -> None:
    dest = pending_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "items": items,
    }
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_pending() -> list[dict[str, Any]]:
    dest = pending_path()
    if not dest.exists():
        return []
    try:
        payload = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        items = payload.get("items") or []
        if isinstance(items, list):
            return [row for row in items if isinstance(row, dict)]
    return []


def match_pending(hint: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    open_items = [
        item
        for item in items
        if item.get("key") and not is_resolved(str(item.get("key") or ""))
    ]
    needle = (hint or "").strip()
    if not needle:
        return open_items
    hits: list[dict[str, Any]] = []
    for item in open_items:
        name = str(item.get("chat_name") or "")
        text = str(item.get("text") or "")
        if needle in name or (name and name in needle) or needle in text:
            hits.append(item)
    return hits


def pending_line(item: dict[str, Any]) -> str:
    where = str(item.get("chat_name") or "群")
    text = str(item.get("text") or "")
    tag = str(item.get("tag") or "未完成")
    line = f"{where}：{text}（{tag}）"
    link = str(item.get("link") or "").strip()
    if link:
        line += f" {link}"
    return line


def _list_items(items: list[dict[str, Any]]) -> str:
    lines = []
    for item in items[:8]:
        lines.append("- " + pending_line(item))
    return "\n".join(lines)


def inbox_as_pending() -> list[dict[str, Any]]:
    from .inbox import recent_items

    out: list[dict[str, Any]] = []
    for item in recent_items(days=7):
        raw = (item.get("text") or "").replace("\n", " ").strip()
        if not raw:
            continue
        text = raw if len(raw) <= 72 else raw[:72] + "…"
        out.append(
            {
                "key": pending_key(
                    message_id=str(item.get("message_id") or ""),
                    chat_id=str(item.get("chat_id") or ""),
                    text=text,
                ),
                "chat_id": str(item.get("chat_id") or ""),
                "chat_name": str(item.get("chat_name") or "群"),
                "text": text,
                "tag": "inbox",
            }
        )
    return out


def ensure_pending_snapshot() -> None:
    if load_pending():
        return
    # tests point these env vars at temp files; never hit live Feishu.
    if os.environ.get("FEISHU_PARTNER_PENDING") or os.environ.get("FEISHU_PARTNER_RESOLVED"):
        return
    from .brief import snapshot_pending

    snapshot_pending()


def resolve_text(raw: str) -> str:
    from .followup import apply_action, format_assign_push, open_followups_as_pending

    items = load_pending()
    hint = resolve_hint(raw)
    followups = open_followups_as_pending()
    reply = assign_reply_body(raw)
    if reply:
        quoted = (raw or "").partition("\n\n")[0]
        hits = [
            item
            for item in followups
            if format_assign_push(
                {
                    "asker_name": item.get("chat_name"),
                    "text": item.get("text"),
                }
            )
            == quoted
        ]
        if len(hits) == 1:
            item = hits[0]
            result = apply_action("fu_done", str(item.get("key") or ""))
            if not result.startswith("已记下"):
                return result
            who = str(item.get("chat_name") or "对方").strip()
            task = str(item.get("text") or "").replace("\n", " ").strip()
            if len(task) > 72:
                task = task[:72] + "…"
            return f"已读回引，{who}「{task}」这条已解决，不再催。"
        if len(hits) > 1:
            return "回引内容对上多条重复任务，先不销账：\n" + _list_items(hits)
        return "我读到了你回复的原消息，但它已经不在待处理里；没有动其他条。"
    weak = not hint
    if weak:
        hits = match_pending("", followups)
        if not hits:
            hits = match_pending("", items) or match_pending("", inbox_as_pending())
    else:
        hits = match_pending(hint, items)
        if not hits:
            hits = match_pending(hint, inbox_as_pending())
        if not hits:
            hits = match_pending(hint, followups)
    if not hits:
        leftover = followups or match_pending("", items) or match_pending("", inbox_as_pending())
        if leftover:
            return "没对上待处理。当前还有：\n" + _list_items(leftover)
        return "没对上待处理，本地也没有今早那批待办。先说「简报」我再列一次。"
    if len(hits) > 1:
        return "对上好几条，再说清楚点（人名、群名或原文几个字）：\n" + _list_items(hits)
    item = hits[0]
    key = str(item.get("key") or "")
    name = str(item.get("chat_name") or "那条")
    if key.startswith("fu:"):
        return apply_action("fu_done", key)
    if not mark_resolved(
        key,
        source="text",
        chat_name=name,
        snippet=str(item.get("text") or ""),
    ):
        return "没记下，请再说一遍「已处理」。"
    return f"已记下，明早简报不再列 {name} 那条。"


def confirm_card(key: str) -> str:
    items = load_pending()
    name = "那条"
    for item in items:
        if str(item.get("key") or "") == key:
            name = str(item.get("chat_name") or name)
            break
    if not mark_resolved(key, source="card", chat_name=name):
        return "没记下，请再说一遍「已处理」。"
    return f"已记下，明早简报不再列 {name} 那条。"


def pending_card(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Interactive card: 已处理 buttons only. Not aily 今日/未结束 filters."""
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": "点「已处理」或在单聊说「某群那条已处理」，明早简报不再催。",
            },
        }
    ]
    for item in items[:5]:
        key = str(item.get("key") or "")
        title = str(item.get("chat_name") or "群")
        body = str(item.get("text") or "")[:80]
        tag = str(item.get("tag") or "")
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{title}**\n{body}" + (f"\n{tag}" if tag else ""),
                },
            }
        )
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "已处理"},
                        "type": "primary",
                        "value": {"act": "done", "key": key},
                    }
                ],
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "待处理 · 点已处理明早不催"},
        },
        "elements": elements,
    }

