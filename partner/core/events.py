from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from ..routing.intents import looks_like_wake


@dataclass(frozen=True)
class CardAction:
    chat_id: str
    operator_id: str
    event_id: str
    act: str
    key: str
    token: str = ""
    task_id: str = ""
    message_id: str = ""
    open_message_id: str = ""
    card_content: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InboundMessage:
    chat_id: str
    chat_type: str
    text: str
    message_id: str
    sender_type: str = ""
    sender_id: str = ""
    mention_ids: tuple[str, ...] = field(default_factory=tuple)
    mentions: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    sender_name: str = ""
    chat_name: str = ""
    woke: bool = False
    msg_type: str = ""
    content: dict[str, Any] = field(default_factory=dict)


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
    # Feishu card.action.trigger may nest fields under "event" or "data".
    event = payload.get("event") if isinstance(payload.get("event"), dict) else None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return None
    merged = {**data, **(event or {})}
    if merged.get("action_tag") is None and merged.get("action_value") is None:
        return None
    raw = merged.get("action_value")
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
    raw_token = str(value.get("token") or merged.get("token") or "")
    # The card.action.trigger event's top-level `message_id` IS the id of the
    # card message that owns the clicked button. That's what we need to PATCH
    # back when disabling the button — there's no separate `open_message_id`
    # in the event payload.
    card_message_id = str(
        merged.get("open_message_id")
        or merged.get("message_id")
        or value.get("message_id")
        or ""
    )
    card_content_raw = merged.get("card_content")
    card_content: dict[str, Any] = {}
    if isinstance(card_content_raw, dict):
        card_content = card_content_raw
    elif isinstance(card_content_raw, str) and card_content_raw.strip():
        try:
            loaded = json.loads(card_content_raw)
            if isinstance(loaded, dict):
                card_content = loaded
        except json.JSONDecodeError:
            pass
    return CardAction(
        chat_id=str(merged.get("chat_id") or merged.get("open_chat_id") or ""),
        operator_id=str(merged.get("operator_id") or ""),
        event_id=str(merged.get("event_id") or card_message_id or ""),
        act=str(value.get("act") or ""),
        key=str(value.get("key") or ""),
        token=raw_token,
        task_id=str(value.get("task_id") or ""),
        message_id=card_message_id,
        open_message_id=card_message_id,
        card_content=card_content,
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
    msg_type = str(
        data.get("message_type") or data.get("msg_type") or data.get("type") or ""
    )
    content_dict: dict[str, Any] = content if isinstance(content, dict) else {}
    if not content_dict and isinstance(content, str) and content.strip().startswith("{"):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                content_dict = parsed
        except json.JSONDecodeError:
            pass
    mentions = data.get("mentions") or []
    mention_pairs: list[tuple[str, str]] = []
    mention_ids_list: list[str] = []
    for mention in mentions:
        if not isinstance(mention, dict):
            continue
        mid = _mention_open_id(mention)
        if not mid:
            continue
        mention_ids_list.append(mid)
        mention_pairs.append((mid, str(mention.get("name") or "")))
    mention_ids = tuple(mention_ids_list)
    woke = looks_like_wake(text)
    sender_type = str(data.get("sender_type") or data.get("senderType") or "")
    sender = data.get("sender")
    sender_name = str(data.get("sender_name") or "")
    if isinstance(sender, dict):
        sender_type = sender_type or str(sender.get("sender_type") or sender.get("type") or "")
        sender_id = _mention_open_id(
            {"id": sender.get("id") or sender.get("open_id") or data.get("sender_id")}
        )
        sender_name = sender_name or str(sender.get("name") or sender.get("sender_name") or "")
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
        mentions=tuple(mention_pairs),
        sender_name=sender_name,
        chat_name=str(data.get("chat_name") or data.get("chatName") or ""),
        woke=woke,
        msg_type=msg_type,
        content=content_dict,
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
