from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from .intents import looks_like_wake


@dataclass(frozen=True)
class CardAction:
    chat_id: str
    operator_id: str
    event_id: str
    act: str
    key: str
    token: str = ""


@dataclass(frozen=True)
class InboundMessage:
    chat_id: str
    chat_type: str
    text: str
    message_id: str
    sender_type: str = ""
    sender_id: str = ""
    mention_ids: tuple[str, ...] = field(default_factory=tuple)
    chat_name: str = ""
    woke: bool = False


def _mention_open_id(mention: dict[str, Any]) -> str:
    mid = mention.get("id")
    if isinstance(mid, dict):
        return str(mid.get("open_id") or mid.get("user_id") or "")
    return str(mid or "")


def _as_dict(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if payload.get("ok") is False:
        return None
    inner = payload.get("data")
    if isinstance(inner, dict) and (
        "chat_id" in inner or "message_id" in inner or "content" in inner
    ):
        return inner
    if "chat_id" in payload or "message_id" in payload or "content" in payload:
        return payload
    return None


def extract_card_action(payload: Any) -> CardAction | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return None
    if data.get("action_tag") is None and data.get("action_value") is None:
        return None
    raw = data.get("action_value")
    value: dict[str, Any] = {}
    if isinstance(raw, dict):
        value = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            value = loaded
    return CardAction(
        chat_id=str(data.get("chat_id") or ""),
        operator_id=str(data.get("operator_id") or ""),
        event_id=str(data.get("event_id") or data.get("message_id") or ""),
        act=str(value.get("act") or ""),
        key=str(value.get("key") or ""),
        token=str(data.get("token") or ""),
    )


def extract_inbound_message(payload: Any) -> InboundMessage | None:
    if extract_card_action(payload) is not None:
        return None
    data = _as_dict(payload)
    if not data:
        return None
    if data.get("action_tag") is not None or data.get("action_value") is not None:
        return None
    chat_id = str(data.get("chat_id") or "")
    if not chat_id:
        return None
    content = data.get("content")
    if isinstance(content, dict):
        text = str(content.get("text") or "")
    else:
        text = str(content or "")
    mentions = data.get("mentions") or []
    mention_ids = tuple(
        mid
        for m in mentions
        if isinstance(m, dict)
        for mid in (_mention_open_id(m),)
        if mid
    )
    woke = looks_like_wake(text)
    sender_type = str(data.get("sender_type") or data.get("senderType") or "")
    sender = data.get("sender")
    if isinstance(sender, dict):
        sender_type = sender_type or str(sender.get("sender_type") or sender.get("type") or "")
        sender_id = _mention_open_id(
            {"id": sender.get("id") or sender.get("open_id") or data.get("sender_id")}
        )
    else:
        sender_id = str(data.get("sender_id") or "")
    return InboundMessage(
        chat_id=chat_id,
        chat_type=str(data.get("chat_type") or "p2p"),
        text=text,
        message_id=str(data.get("message_id") or data.get("id") or ""),
        sender_type=sender_type,
        sender_id=sender_id,
        mention_ids=mention_ids,
        chat_name=str(data.get("chat_name") or data.get("chatName") or ""),
        woke=woke,
    )


def should_reply(msg: InboundMessage, bot_open_id: str = "") -> bool:
    if msg.sender_type in {"app", "bot"}:
        return False
    if bot_open_id and msg.sender_id and msg.sender_id == bot_open_id:
        return False
    if msg.chat_type != "group":
        return True
    if bot_open_id and bot_open_id in msg.mention_ids:
        return True
    return msg.woke
