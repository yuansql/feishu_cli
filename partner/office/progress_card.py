"""Progress brief card for Agent Runtime v2 tasks.

Used for manual or scheduled broadcast of "who is waiting for what / next step".
"""

from __future__ import annotations

from typing import Any


def _status_label(status: str) -> str:
    return {
        "running": "执行中",
        "blocked": "等待确认",
        "queued": "排队中",
        "done": "已完成",
        "failed": "失败",
        "cancelled": "已取消",
    }.get(status, status)


def format_patches_text(task: dict[str, Any] | None) -> str:
    """Human-readable patch history for a task."""
    if not task or not isinstance(task, dict):
        return "找不到任务。"
    task_id = str(task.get("id") or "")
    goal = str(task.get("goal") or "").strip() or "（无目标）"
    status = str(task.get("status") or "")
    lines = [
        f"任务 {task_id} · {goal}",
        f"状态：{_status_label(status)}",
        "",
        "意图补丁：",
    ]
    patches = [p for p in (task.get("intent_patches") or []) if isinstance(p, dict)]
    if not patches:
        lines.append("（还没有追加补丁）")
        return "\n".join(lines)
    for p in patches:
        author = str(p.get("sender_name") or p.get("author_open_id") or "某人")
        action = str(p.get("action") or "append")
        text = str(p.get("text") or "")
        merged = "✓" if p.get("merged") else "○"
        lines.append(f"{merged} [{action}] {author}：{text[:80]}")
    return "\n".join(lines)


def progress_brief_card(task: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build a progress brief interactive card for an agent task.

    Returns None when there is no displayable task.
    """
    if not task or not isinstance(task, dict):
        return None
    task_id = str(task.get("id") or "")
    goal = str(task.get("goal") or "").strip() or "（无目标）"
    status = str(task.get("status") or "")
    label = _status_label(status)
    pending_text = "任务正在推进，暂无卡点。"
    if status == "blocked":
        pending = task.get("pending_write") or {}
        tool = pending.get("tool") or pending.get("reason") or "写操作"
        pending_text = f"卡在「{tool}」确认闸上，等待有人点击确认或回复「我来确认」。"
    elif status == "queued":
        pending_text = "任务在后台队列中等待执行。"
    patches = [p for p in (task.get("intent_patches") or []) if isinstance(p, dict)]
    patch_count = len(patches)
    merged_count = sum(1 for p in patches if p.get("merged"))
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**目标**：{goal}\n**状态**：{label}",
            },
        },
        {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": f"当前：{pending_text}",
            },
        },
    ]
    if patch_count:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": f"已收到 {patch_count} 条意图补丁（已合并 {merged_count} 条）。",
            },
        })
    if status == "blocked":
        elements.append({
            "tag": "hr",
        })
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "我来确认"},
                    "type": "primary",
                    "value": {
                        "act": "claim",
                        "task_id": task_id,
                    },
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "取消任务"},
                    "type": "default",
                    "value": {
                        "act": "decline",
                        "task_id": task_id,
                    },
                },
            ],
        })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "任务进度简报"},
        },
        "elements": elements,
    }
