"""Instant feedback before slow lark-cli / Hermes work."""

from __future__ import annotations

ACK_EMOJI = "OnIt"

_ACK = {
    "tasks": "在查待办…",
    "today": "在看今天…",
    "today_recap": "在读今天的消息…",
    "tomorrow": "在看明天…",
    "task_done": "在勾那条待办…",
    "brief": "在写简报…",
    "weekly": "在看周报…",
    "search": "在搜文档…",
    "read": "在读文档…",
    "chats": "在找群…",
    "inbox": "在看谁找你…",
    "person": "在看她怎么回的…",
    "who": "在查这人是谁…",
    "minutes": "在查纪要…",
    "approval": "在查审批…",
    "resolve": "在销账…",
    "write_weekly": "在写周报…",
    "write_doc": "在写文档…",
    "identity": "在认人…",
    "digest": "在理待跟进…",
    "weekly_tasks": "在写本周任务…",
    "plan": "在拆计划…",
    "task_continue": "在跑下一步…",
    "task_status": "在看任务进度…",
    "task_confirm": "在同步写回…",
    "task_cancel": "在停止任务…",
    "aily": "在整理对齐项…",
}

_NO_ACK_TEXT = frozenset({"help", "send"})


def ack_line(action: str) -> str:
    return _ACK.get(action, "收到，在办…")


def should_ack_text(action: str) -> bool:
    return action not in _NO_ACK_TEXT
