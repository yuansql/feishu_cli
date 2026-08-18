"""Compact schema 2.0 card for 今日待跟进. No 3-column layout, no note tag."""

from __future__ import annotations

from datetime import date
from typing import Any

from .formatters import plain_im_text


def _md(content: str) -> dict[str, Any]:
    return {"tag": "markdown", "content": content}


def _hr() -> dict[str, Any]:
    return {"tag": "hr"}


def _buttons(key: str) -> dict[str, Any]:
    actions = []
    for label, act, kind in (
        ("已完成", "fu_done", "primary"),
        ("明天再说", "fu_snooze", "default"),
        ("忽略", "fu_ignore", "default"),
    ):
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": label},
                "type": kind,
                "size": "small",
                "value": {"act": act, "key": key},
                "behaviors": [{"type": "callback", "value": {"act": act, "key": key}}],
            }
        )
    return {"tag": "action", "actions": actions}


def _block(item: dict[str, Any]) -> list[dict[str, Any]]:
    who = str(item.get("assignee_name") or item.get("asker_name") or "对方")
    where = str(item.get("chat_name") or "群")
    text = plain_im_text(str(item.get("text") or ""))
    if len(text) > 80:
        text = text[:80] + "…"
    key = str(item.get("id") or "")
    out = [_md(f"**{who}** · {where}\n{text}")]
    if key:
        out.append(_buttons(key))
    return out


def digest_card(
    *,
    due: list[dict[str, Any]],
    chase: list[dict[str, Any]],
    other: list[dict[str, Any]],
    today: date,
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = []
    if due:
        elements.append(_md(f"**今天要去问谁为什么没给答复** · {today.isoformat()}"))
        for item in due[:8]:
            elements.extend(_block(item))
    if chase:
        if elements:
            elements.append(_hr())
        elements.append(_md("**这周要去催谁**"))
        for item in chase[:8]:
            elements.extend(_block(item))
    if other:
        if elements:
            elements.append(_hr())
        elements.append(_md("**还在跟**"))
        for item in other[:6]:
            elements.extend(_block(item))
    if not elements:
        elements.append(_md("今天没有待跟进。"))
    return {
        "schema": "2.0",
        "config": {"width_mode": "compact"},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "今日待跟进"},
        },
        "body": {"elements": elements},
    }
