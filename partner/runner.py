"""Persisted task runner: plan → act → observe → resume → confirm writes."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .planner import plan_steps

CN_TZ = timezone(timedelta(hours=8))

READ_TOOLS = frozenset(
    {
        "today",
        "tomorrow",
        "tasks",
        "search",
        "read",
        "person",
        "chats",
        "inbox",
        "minutes",
        "approval",
        "brief",
    }
)
WRITE_TOOLS = frozenset({"task_create", "followup_add", "docs_create"})
INTERNAL_TOOLS = frozenset({"summarize"})


def tasks_dir() -> Path:
    override = os.environ.get("FEISHU_PARTNER_TASKS_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner" / "tasks"


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _normalize_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, raw in enumerate(steps, 1):
        if not isinstance(raw, dict):
            continue
        tool = str(raw.get("tool") or "").strip()
        if not tool:
            continue
        args = raw.get("args") if isinstance(raw.get("args"), dict) else {}
        clean_args = {str(k): str(v) for k, v in args.items()}
        out.append(
            {
                "id": index,
                "title": str(raw.get("title") or tool),
                "tool": tool,
                "args": clean_args,
                "status": "pending",
                "result": "",
                "error": "",
                "requires_confirm": bool(raw.get("requires_confirm")),
            }
        )
    return out


def create_task(goal: str, chat_id: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    cleaned = (goal or "").strip()
    if not cleaned:
        raise ValueError("goal required")
    normalized = _normalize_steps(steps)
    if not normalized:
        raise ValueError("steps required")
    task_id = uuid.uuid4().hex[:12]
    now = _now_iso()
    task = {
        "id": task_id,
        "chat_id": (chat_id or "").strip(),
        "goal": cleaned,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "steps": normalized,
    }
    save_task(task)
    return task


def save_task(task: dict[str, Any]) -> None:
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        raise ValueError("task id required")
    root = tasks_dir()
    root.mkdir(parents=True, exist_ok=True)
    task["updated_at"] = _now_iso()
    path = root / f"{task_id}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_task(task_id: str) -> dict[str, Any] | None:
    tid = (task_id or "").strip()
    if not tid:
        return None
    path = tasks_dir() / f"{tid}.json"
    if not path.is_file():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return blob if isinstance(blob, dict) else None


def _iter_chat_tasks(chat_id: str) -> list[dict[str, Any]]:
    cid = (chat_id or "").strip()
    if not cid:
        return []
    root = tasks_dir()
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        task = load_task(path.stem)
        if not task:
            continue
        if str(task.get("chat_id") or "") != cid:
            continue
        rows.append(task)
    return rows


def active_task_for_chat(chat_id: str) -> dict[str, Any] | None:
    for task in _iter_chat_tasks(chat_id):
        status = str(task.get("status") or "")
        if status in {"pending", "running", "blocked"}:
            return task
    return None


def _pending_index(task: dict[str, Any]) -> int | None:
    steps = task.get("steps") or []
    if not isinstance(steps, list):
        return None
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        if str(step.get("status") or "") == "pending":
            return index
    return None


def _blocked_write_indices(task: dict[str, Any]) -> list[int]:
    out: list[int] = []
    for index, step in enumerate(task.get("steps") or []):
        if not isinstance(step, dict):
            continue
        if str(step.get("status") or "") != "blocked":
            continue
        if str(step.get("tool") or "") in WRITE_TOOLS:
            out.append(index)
    return out


def run_tool(tool: str, args: dict[str, str]) -> str:
    name = (tool or "").strip()
    if name == "summarize":
        raise RuntimeError("summarize must go through _summarize_task")
    if name in WRITE_TOOLS:
        raise RuntimeError(f"write tool {name} requires confirmation")
    if name not in READ_TOOLS:
        raise RuntimeError(f"unknown tool: {name}")
    from .actions import (
        approval_text,
        brief_text,
        chats_text,
        inbox_text,
        minutes_text,
        person_text,
        read_text,
        search_text,
        tasks_text,
        today_text,
        tomorrow_text,
    )

    runners: dict[str, Callable[[dict[str, str]], str]] = {
        "today": lambda _a: today_text(),
        "tomorrow": lambda _a: tomorrow_text(),
        "tasks": lambda _a: tasks_text(),
        "search": lambda a: search_text(a.get("query", "")),
        "read": lambda a: read_text(a.get("doc", "")),
        "person": lambda a: person_text(a.get("query", "")),
        "chats": lambda a: chats_text(a.get("query", "")),
        "inbox": lambda _a: inbox_text(),
        "minutes": lambda _a: minutes_text(),
        "approval": lambda _a: approval_text(),
        "brief": lambda _a: brief_text(),
    }
    fn = runners.get(name)
    if fn is None:
        raise RuntimeError(f"tool not wired: {name}")
    return fn(args)


def run_write_tool(tool: str, args: dict[str, str], *, confirmed: bool) -> str:
    if not confirmed:
        raise RuntimeError("write tool requires confirmation")
    name = (tool or "").strip()
    if name == "followup_add":
        from .followup import add_goal_item

        goal = args.get("goal") or args.get("summary") or args.get("query") or ""
        item = add_goal_item(goal, chat_id=args.get("chat_id") or "")
        return f"已写入跟进账：{item.get('text')}（{item.get('id')}）"
    if name == "task_create":
        from .actions import create_task_item

        summary = args.get("summary") or args.get("goal") or ""
        return create_task_item(summary, due=args.get("due") or "")
    if name == "docs_create":
        from .actions import write_doc_text

        query = args.get("query") or args.get("goal") or args.get("summary") or ""
        return write_doc_text(query)
    raise RuntimeError(f"unknown write tool: {name}")


def _looks_like_failure(text: str) -> bool:
    blob = (text or "").strip()
    if not blob:
        return True
    markers = (
        "飞书权威失败",
        "missing_scope",
        "缺权限",
        "unexpected",
        "forbidden",
        "不能为空",
    )
    lower = blob.lower()
    return any(token in blob or token in lower for token in markers)


def _summarize_task(task: dict[str, Any]) -> str:
    goal = str(task.get("goal") or "")
    lines = [f"任务：{goal}", "", "【已收集】"]
    had_material = False
    for step in task.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if str(step.get("tool") or "") == "summarize":
            continue
        if str(step.get("status") or "") != "done":
            continue
        body = str(step.get("result") or "").strip()
        if not body:
            continue
        had_material = True
        preview = body.replace("\n", " ")
        if len(preview) > 120:
            preview = preview[:120] + "…"
        lines.append(f"- {step.get('title') or step.get('tool')}：{preview}")
    if not had_material:
        lines.append("- 还没有可用材料；先确认飞书权限或重试。")
    blocked = _blocked_write_indices(task)
    lines.extend(["", "【建议动作】", "- 按上面材料逐项推进；缺权限的先补 OAuth。"])
    if blocked:
        lines.append("- 回复「确认写入」把跟进账/飞书待办/云文档同步出去（写操作带确认闸）。")
    else:
        lines.append("- 回复「下一步」继续；「任务进度」看状态。")
    return "\n".join(lines)


def _execute_step(
    task: dict[str, Any],
    index: int,
    *,
    confirm_write: bool = False,
) -> tuple[str, bool]:
    steps = task.get("steps") or []
    if not isinstance(steps, list) or index >= len(steps):
        return "没有可执行的步骤。", False
    step = steps[index]
    if not isinstance(step, dict):
        return "步骤损坏。", False
    tool = str(step.get("tool") or "")
    args = step.get("args") if isinstance(step.get("args"), dict) else {}
    clean_args = {str(k): str(v) for k, v in args.items()}
    clean_args.setdefault("chat_id", str(task.get("chat_id") or ""))
    title = str(step.get("title") or tool)
    if tool in WRITE_TOOLS:
        if not confirm_write and (
            step.get("requires_confirm") or str(step.get("status") or "") == "blocked"
        ):
            step["status"] = "blocked"
            step["error"] = "写操作需确认；回复「确认写入」。"
            task["status"] = "blocked"
            save_task(task)
            return (
                f"第 {index + 1} 步「{title}」待确认。回复「确认写入」执行跟进账/待办/云文档同步。",
                False,
            )
        try:
            result = run_write_tool(tool, clean_args, confirmed=True)
        except Exception as exc:
            step["status"] = "failed"
            step["error"] = str(exc)
            task["status"] = "blocked"
            save_task(task)
            return f"第 {index + 1} 步失败：{title}（{exc}）", False
        step["result"] = result
        if _looks_like_failure(result):
            step["status"] = "failed"
            step["error"] = result[:240]
            task["status"] = "blocked"
            save_task(task)
            return f"第 {index + 1} 步受阻：{title}", False
        step["status"] = "done"
        step["error"] = ""
        save_task(task)
        return f"第 {index + 1} 步完成：{title}", True
    if tool == "summarize":
        step["result"] = _summarize_task(task)
        step["status"] = "done"
        save_task(task)
        return f"第 {index + 1} 步完成：{title}", True
    try:
        result = run_tool(tool, clean_args)
    except Exception as exc:
        step["status"] = "failed"
        step["error"] = str(exc)
        task["status"] = "blocked"
        save_task(task)
        return f"第 {index + 1} 步失败：{title}（{exc}）", False
    step["result"] = result
    if _looks_like_failure(result):
        step["status"] = "failed"
        step["error"] = result[:240]
        task["status"] = "blocked"
        save_task(task)
        return f"第 {index + 1} 步受阻：{title}", False
    step["status"] = "done"
    save_task(task)
    return f"第 {index + 1} 步完成：{title}", True


def _refresh_task_status(task: dict[str, Any]) -> None:
    steps = task.get("steps") or []
    if not isinstance(steps, list) or not steps:
        task["status"] = "done"
        return
    statuses = [
        str(step.get("status") or "")
        for step in steps
        if isinstance(step, dict)
    ]
    if any(status == "blocked" for status in statuses):
        task["status"] = "blocked"
    elif any(status == "failed" for status in statuses):
        task["status"] = "blocked"
    elif all(status == "done" for status in statuses):
        task["status"] = "done"
    elif any(status == "done" for status in statuses):
        task["status"] = "running"
    else:
        task["status"] = "pending"


def run_next(task_id: str, *, confirm_write: bool = False) -> str:
    task = load_task(task_id)
    if not task:
        return "找不到这个任务。"
    index = _pending_index(task)
    if index is None:
        _refresh_task_status(task)
        save_task(task)
        if str(task.get("status") or "") == "done":
            return format_status(task) + "\n\n任务已完成。"
        if _blocked_write_indices(task):
            return (
                format_status(task)
                + "\n\n有写操作待确认。回复「确认写入」同步跟进账/飞书待办/云文档。"
            )
        return format_status(task)
    task["status"] = "running"
    save_task(task)
    msg, _ok = _execute_step(task, index, confirm_write=confirm_write)
    task = load_task(task_id) or task
    _refresh_task_status(task)
    save_task(task)
    total = len(task.get("steps") or [])
    done = sum(
        1
        for step in task.get("steps") or []
        if isinstance(step, dict) and str(step.get("status") or "") == "done"
    )
    head = f"任务「{task.get('goal')}」进度 {done}/{total}"
    if str(task.get("status") or "") == "done":
        summary = ""
        for step in task.get("steps") or []:
            if isinstance(step, dict) and str(step.get("tool") or "") == "summarize":
                summary = str(step.get("result") or "")
                break
        tail = summary or format_status(task)
        return f"{head}\n\n任务完成。\n\n{tail}"
    if _blocked_write_indices(task):
        return f"{head}\n{msg}\n\n回复「确认写入」继续同步。"
    return f"{head}\n{msg}"


def run_all(task_id: str, *, stop_on_blocked: bool = True) -> str:
    messages: list[str] = []
    while True:
        task = load_task(task_id)
        if not task:
            return "找不到这个任务。"
        index = _pending_index(task)
        if index is None:
            _refresh_task_status(task)
            save_task(task)
            break
        msg = run_next(task_id)
        messages.append(msg)
        task = load_task(task_id)
        if not task:
            break
        if stop_on_blocked and str(task.get("status") or "") in {"blocked"}:
            break
        if str(task.get("status") or "") == "done":
            break
    final = load_task(task_id)
    if not final:
        return messages[-1] if messages else "任务结束。"
    if messages:
        return messages[-1]
    return format_status(final)


def confirm_writes(chat_id: str, *, step_id: int | None = None) -> str:
    task = active_task_for_chat(chat_id)
    if not task:
        latest = _iter_chat_tasks(chat_id)
        task = latest[0] if latest else None
    if not task:
        return "没有待确认的任务。先说「规划 XXX」创建任务。"
    task_id = str(task.get("id") or "")
    messages: list[str] = []
    for index, step in enumerate(task.get("steps") or []):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool") or "") not in WRITE_TOOLS:
            continue
        if str(step.get("status") or "") not in {"blocked", "pending"}:
            continue
        if step_id is not None and int(step.get("id") or 0) != step_id:
            continue
        if str(step.get("status") or "") == "pending" and step.get("requires_confirm"):
            step["status"] = "blocked"
        msg, _ok = _execute_step(task, index, confirm_write=True)
        messages.append(msg)
        task = load_task(task_id) or task
    _refresh_task_status(task)
    save_task(task)
    if not messages:
        return format_status(task) + "\n\n没有待确认的写步骤。"
    rest = run_next(task_id) if _pending_index(task) is not None else ""
    head = "写操作已确认执行：\n" + "\n".join(messages)
    if rest:
        return head + "\n\n" + rest
    if str(task.get("status") or "") == "done":
        return head + "\n\n任务已全部完成。"
    return head + "\n\n" + format_status(task)


def format_status(task: dict[str, Any]) -> str:
    goal = str(task.get("goal") or "")
    status = str(task.get("status") or "unknown")
    lines = [f"任务：{goal}", f"状态：{status}", "", "步骤："]
    for step in task.get("steps") or []:
        if not isinstance(step, dict):
            continue
        mark = {
            "done": "✓",
            "pending": "○",
            "failed": "✗",
            "blocked": "!",
        }.get(str(step.get("status") or ""), "?")
        suffix = "（需确认）" if step.get("requires_confirm") else ""
        lines.append(f"{mark} {step.get('id')}. {step.get('title') or step.get('tool')}{suffix}")
        err = str(step.get("error") or "").strip()
        if err:
            lines.append(f"   原因：{err[:120]}")
    return "\n".join(lines)


def start_task(goal: str, chat_id: str) -> str:
    steps = plan_steps(goal)
    task = create_task(goal, chat_id, steps)
    body = run_all(task["id"])
    if _blocked_write_indices(load_task(task["id"]) or task):
        body += "\n\n写回飞书/跟进账需你确认：回复「确认写入」。"
    return f"任务已创建（{task['id']}）。\n\n{body}"


def continue_task(chat_id: str) -> str:
    task = active_task_for_chat(chat_id)
    if not task:
        return "当前没有进行中的任务。可以说「规划 XXX」新建一个。"
    if str(task.get("status") or "") == "done":
        return format_status(task) + "\n\n这条任务已经跑完了。"
    return run_next(str(task.get("id") or ""))


def status_task(chat_id: str) -> str:
    task = active_task_for_chat(chat_id)
    if not task:
        latest = _iter_chat_tasks(chat_id)
        if latest:
            return format_status(latest[0])
        return "还没有任务。可以说「规划 A6 上线」开始。"
    return format_status(task)
