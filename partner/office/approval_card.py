"""Interactive approval card for write operations in Agent Runtime v2.

Cards are standard Feishu interactive messages with approve/decline buttons.
The button values carry enough metadata for serve-side recovery without
relying on chat state.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

CN_TZ = timezone(timedelta(hours=8))

_TIMEOUT_MIN = 5


def _now() -> datetime:
    return datetime.now(CN_TZ)


def _format_args(args: dict[str, Any]) -> str:
    out: list[str] = []
    for k, v in sorted((args or {}).items()):
        s = str(v).strip()
        if not s:
            continue
        # Keep lines reasonably short for card display.
        if len(s) > 120:
            s = s[:120] + "…"
        out.append(f"- **{k}**：{s}")
    return "\n".join(out) if out else "（无参数）"


def _tool_label(tool: str) -> str:
    labels: dict[str, str] = {
        "followup_add": "写入本地跟进账",
        "task_create": "创建飞书任务",
        "docs_create": "新建飞书云文档",
    }
    return labels.get(tool, f"执行工具 `{tool}`") if tool else "允许 Agent 写入飞书"


def _risk_level(tool: str) -> str:
    if not tool:
        return "中"
    if tool in {"docs_create", "task_create"}:
        return "中"
    if tool == "followup_add":
        return "低"
    return "中"


def approval_card_payload(
    *,
    tool: str,
    args: dict[str, Any],
    task_id: str,
    message_id: str,
    token: str,
    timeout_min: int = _TIMEOUT_MIN,
) -> dict[str, Any]:
    """Build an interactive card payload for a pending write approval.

    The returned dict can be passed directly to partner.office.messaging.send_card.
    """
    expires = _now() + timedelta(minutes=max(1, int(timeout_min)))
    expires_text = expires.strftime("%H:%M")
    has_tool = bool((tool or "").strip())
    label = _tool_label(tool)
    arg_lines = _format_args(args) if has_tool else "Agent 将在你确认后继续执行写操作。"
    risk = _risk_level(tool)
    value = json.dumps(
        {
            "act": "approve",
            "key": task_id,
            "task_id": task_id,
            "message_id": message_id,
            "token": token,
        },
        ensure_ascii=False,
    )
    decline_value = json.dumps(
        {
            "act": "decline",
            "key": task_id,
            "task_id": task_id,
            "message_id": message_id,
            "token": token,
        },
        ensure_ascii=False,
    )
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**{label}**\n风险等级：{risk} · {expires_text} 前有效",
            },
        },
        {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": "即将写入的参数：" if has_tool else "说明：",
            },
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": arg_lines,
            },
        },
        {
            "tag": "hr",
        },
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "✅ 确认写入"},
                    "type": "primary",
                    "value": value,
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "❌ 取消"},
                    "type": "default",
                    "value": decline_value,
                },
            ],
        },
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "Agent 请求确认写入"},
        },
        "elements": elements,
    }


def approval_result_card(*, approved: bool, tool: str, detail: str = "") -> dict[str, Any]:
    """Small card shown after user clicks approve/decline."""
    if approved:
        title = "已确认"
        template = "green"
        body = f"已执行 `{tool}`。{detail}".strip()
    else:
        title = "已取消"
        template = "grey"
        body = f"已取消 `{tool}`。{detail}".strip()
    return {
        "config": {"wide_screen_mode": False},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "plain_text", "content": body},
            }
        ],
    }


def approval_expired_card(*, tool: str, goal: str = "") -> dict[str, Any]:
    """Card shown when an approval request times out without a user response."""
    body = f"写入 `{tool}` 的确认已超时，任务已自动取消。"
    if goal:
        body += f"<br>原任务：{goal}"
    return {
        "config": {"wide_screen_mode": False},
        "header": {
            "template": "grey",
            "title": {"tag": "plain_text", "content": "确认已超时"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": body + "<br><br>如需继续，可重新发起任务。",
                },
            },
        ],
    }
