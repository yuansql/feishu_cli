"""Decide whether a group message is about the deployer, and whether to push it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .events import InboundMessage, should_reply
from .ids import USER_NAMES

# ponytail: verb list, not an LLM judge. Add verbs when false-negatives show up.
_ASSIGN_HINTS = (
    "请",
    "麻烦",
    "需要",
    "帮忙",
    "跟进",
    "处理",
    "看一下",
    "确认",
    "尽快",
    "发给",
    "同步",
    "负责",
    "安排",
)


@dataclass(frozen=True)
class WatchDecision:
    notify: bool
    reason: str
    item: dict[str, Any]


def _text_names_me(text: str, names: tuple[str, ...] = USER_NAMES) -> bool:
    return any(name and name in (text or "") for name in names)


def should_watch(
    msg: InboundMessage,
    *,
    user_open_id: str,
    bot_open_id: str = "",
) -> bool:
    if msg.sender_type in {"app", "bot"}:
        return False
    if user_open_id and msg.sender_id == user_open_id:
        return False
    if msg.chat_type != "group":
        return False
    if should_reply(msg, bot_open_id):
        return False
    if user_open_id and user_open_id in msg.mention_ids:
        return True
    return _text_names_me(msg.text)


def consider(
    msg: InboundMessage,
    *,
    user_open_id: str,
    bot_open_id: str = "",
    names: tuple[str, ...] = USER_NAMES,
) -> WatchDecision | None:
    if not should_watch(msg, user_open_id=user_open_id, bot_open_id=bot_open_id):
        return None
    mentioned = bool(user_open_id and user_open_id in msg.mention_ids)
    if mentioned:
        reason = "mention"
        notify = True
    elif any(hint in (msg.text or "") for hint in _ASSIGN_HINTS):
        reason = "assign"
        notify = True
    else:
        reason = "name"
        notify = False
    item = {
        "message_id": msg.message_id,
        "chat_id": msg.chat_id,
        "chat_name": msg.chat_name,
        "sender_id": msg.sender_id,
        "text": (msg.text or "").strip(),
        "mentioned": mentioned,
        "notify": notify,
        "reason": reason,
    }
    return WatchDecision(notify=notify, reason=reason, item=item)


def format_watch_push(item: dict[str, Any]) -> str:
    where = item.get("chat_name") or item.get("chat_id") or "某个群"
    text = (item.get("text") or "").strip() or "(无正文)"
    if len(text) > 240:
        text = text[:240] + "…"
    return (
        f"有人在「{where}」找你，已记下，之后可说「谁找我」汇总。\n"
        f"{text}"
    )


def format_inbox_digest(items: list[dict[str, Any]]) -> str:
    if not items:
        return "这几天没记下谁找你。机器人必须在那个群里，电脑还得开着 feishu serve。"
    lines = [f"这几天有人找你 {len(items)} 条："]
    for item in items[-20:]:
        where = item.get("chat_name") or item.get("chat_id") or "群"
        text = (item.get("text") or "").replace("\n", " ").strip() or "(无正文)"
        if len(text) > 80:
            text = text[:80] + "…"
        flag = "已推" if item.get("notify") else "只记"
        lines.append(f"- [{flag}] {where}：{text}")
    return "\n".join(lines)
