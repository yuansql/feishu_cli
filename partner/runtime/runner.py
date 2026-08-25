"""Persisted task runner: plan → act → observe → resume → confirm writes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .multi_agent import enrich_facts_for_plan
from .planner import agent_plan_steps, plan_steps
from .tool_registry import (
    confirm_tools,
    execute_tool,
    internal_tools,
    read_tools,
    write_tools,
)
from ..core.trace import emit_trace

CN_TZ = timezone(timedelta(hours=8))

READ_TOOLS = read_tools()
WRITE_TOOLS = write_tools()
CONFIRM_TOOLS = confirm_tools()
INTERNAL_TOOLS = internal_tools()
ALL_TOOLS = READ_TOOLS | WRITE_TOOLS | INTERNAL_TOOLS
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})


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
                "attempts": 0,
                "started_at": "",
                "finished_at": "",
            }
        )
    return out


def create_task(
    goal: str,
    chat_id: str,
    steps: list[dict[str, Any]],
    *,
    mode: str = "fixed",
    background: bool = False,
) -> dict[str, Any]:
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
        "schema_version": 2,
        "mode": "agent" if mode == "agent" else "fixed",
        "phase": "observe" if mode == "agent" else "execute",
        "background": bool(background),
        "cancel_requested": False,
        "plan_version": 0,
        "replan_count": 0,
        "max_replans": 2,
        "plan_history": [],
        "steps": normalized,
    }
    if background:
        task["status"] = "queued"
    save_task(task)
    emit_trace(
        task_id,
        "task.created",
        goal=cleaned,
        chat_id=task["chat_id"],
        mode=task["mode"],
        background=task["background"],
    )
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
    try:
        from ..core.run_store import upsert_run

        upsert_run(task)
    except Exception:
        # ponytail: JSON is canonical; index/lease must not block persist
        emit_trace(task_id, "run_store.upsert_failed")


def _migrate_task(task: dict[str, Any]) -> dict[str, Any]:
    task.setdefault("schema_version", 2)
    task.setdefault("mode", "fixed")
    task.setdefault("phase", "execute")
    task.setdefault("background", False)
    task.setdefault("cancel_requested", False)
    task.setdefault("plan_version", 0)
    task.setdefault("replan_count", 0)
    task.setdefault("max_replans", 2)
    task.setdefault("plan_history", [])
    if str(task.get("status") or "") == "succeeded":
        task["status"] = "done"
    steps = task.get("steps")
    if isinstance(steps, list):
        for index, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                continue
            step.setdefault("id", index)
            step.setdefault("attempts", 0)
            step.setdefault("started_at", "")
            step.setdefault("finished_at", "")
    return task


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
    return _migrate_task(blob) if isinstance(blob, dict) else None


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
        if status in {"pending", "queued", "running", "blocked"}:
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
        if str(step.get("tool") or "") in CONFIRM_TOOLS:
            out.append(index)
    return out


def run_tool(tool: str, args: dict[str, str]) -> str:
    return execute_tool(tool, args, confirmed=False)


def run_write_tool(tool: str, args: dict[str, str], *, confirmed: bool) -> str:
    return execute_tool(tool, args, confirmed=confirmed)


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


def _collect_materials_for_report(task: dict[str, Any]) -> str:
    sections: list[str] = []
    for step in task.get("steps") or []:
        if not isinstance(step, dict):
            continue
        tool = str(step.get("tool") or "")
        if tool in {"summarize", "report_write"}:
            continue
        if str(step.get("status") or "") != "done":
            continue
        body = str(step.get("result") or "").strip()
        if not body:
            continue
        title = str(step.get("title") or tool)
        sections.append(f"## {title}\n\n{body}")
    brief = task.get("multi_agent")
    if isinstance(brief, dict):
        writer = brief.get("writer")
        if isinstance(writer, dict):
            body = str(writer.get("text") or "").strip()
            if body:
                sections.append(f"## 交付结构\n\n{body}")
    joined = "\n\n".join(sections).strip()
    if len(joined) > 12000:
        return joined[:12000] + "\n…(截断)"
    return joined


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
    brief = task.get("multi_agent")
    if isinstance(brief, dict):
        writer = brief.get("writer")
        if isinstance(writer, dict):
            body = str(writer.get("text") or "").strip()
            if body:
                lines.extend(["", "【交付结构】", body[:1200]])
    if blocked:
        lines.append("- 回复「确认写入」把跟进账/飞书待办/云文档同步出去（写操作带确认闸）。")
    else:
        lines.append("- 回复「下一步」继续；「任务进度」看状态。")
    return "\n".join(lines)


def _observation_steps(goal: str) -> list[dict[str, Any]]:
    """Collect authoritative context before asking the model to plan."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for step in plan_steps(goal):
        tool = str(step.get("tool") or "")
        if tool not in READ_TOOLS:
            continue
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        signature = (tool, json.dumps(args, ensure_ascii=False, sort_keys=True))
        if signature in seen:
            continue
        seen.add(signature)
        rows.append(step)
    return rows or [
        {"title": "读取今天上下文", "tool": "today", "args": {}},
        {"title": "读取未完成待办", "tool": "tasks", "args": {}},
    ]


def _step_signature(step: dict[str, Any]) -> tuple[str, str]:
    args = step.get("args") if isinstance(step.get("args"), dict) else {}
    return (
        str(step.get("tool") or ""),
        json.dumps(args, ensure_ascii=False, sort_keys=True),
    )


def _facts_for_plan(task: dict[str, Any]) -> str:
    lines: list[str] = []
    for step in task.get("steps") or []:
        if not isinstance(step, dict):
            continue
        status = str(step.get("status") or "")
        if status not in {"done", "failed"}:
            continue
        title = str(step.get("title") or step.get("tool") or "")
        body = str(step.get("result") or step.get("error") or "").strip()
        if not body:
            continue
        lines.append(f"[{status}] {title}\n{body[:3000]}")
        if sum(len(line) for line in lines) >= 14000:
            break
    return "\n\n".join(lines)[:14000]


def _materialize_agent_plan(
    task: dict[str, Any],
    *,
    failure: str = "",
    replacing: bool = False,
) -> bool:
    if str(task.get("mode") or "") != "agent" or task.get("cancel_requested"):
        return False
    facts = _facts_for_plan(task)
    if str(task.get("mode") or "") == "agent":
        facts, brief = enrich_facts_for_plan(
            str(task.get("goal") or ""),
            facts,
            task_id=str(task.get("id") or ""),
        )
        task["multi_agent"] = brief.to_dict()
    previous = [
        {
            "title": str(step.get("title") or ""),
            "tool": str(step.get("tool") or ""),
            "args": step.get("args") if isinstance(step.get("args"), dict) else {},
            "status": str(step.get("status") or ""),
        }
        for step in task.get("steps") or []
        if isinstance(step, dict)
    ]
    planned = agent_plan_steps(
        str(task.get("goal") or ""),
        facts,
        available_tools=ALL_TOOLS,
        write_tools=CONFIRM_TOOLS,
        previous_steps=previous,
        failure=failure,
    )
    source = "model"
    if not planned:
        planned = [
            dict(step)
            for step in plan_steps(str(task.get("goal") or ""), facts)
        ]
        source = "fallback"

    existing = {
        _step_signature(step)
        for step in task.get("steps") or []
        if isinstance(step, dict)
        and str(step.get("status") or "") in {"done", "failed", "superseded"}
    }
    if replacing:
        for step in task.get("steps") or []:
            if not isinstance(step, dict):
                continue
            if str(step.get("status") or "") in {"pending", "failed"}:
                step["status"] = "superseded"

    normalized = _normalize_steps(planned)
    normalized = [step for step in normalized if _step_signature(step) not in existing]
    if not normalized and not any(
        isinstance(step, dict)
        and str(step.get("tool") or "") == "summarize"
        and str(step.get("status") or "") == "done"
        for step in task.get("steps") or []
    ):
        normalized = _normalize_steps(
            [{"title": "汇总结果并验收目标", "tool": "summarize", "args": {}}]
        )
    next_id = max(
        (
            int(step.get("id") or 0)
            for step in task.get("steps") or []
            if isinstance(step, dict)
        ),
        default=0,
    )
    for offset, step in enumerate(normalized, 1):
        step["id"] = next_id + offset
    task.setdefault("steps", []).extend(normalized)
    task["phase"] = "execute"
    task["plan_version"] = int(task.get("plan_version") or 0) + 1
    history = task.get("plan_history")
    if not isinstance(history, list):
        history = []
        task["plan_history"] = history
    history.append(
        {
            "version": task["plan_version"],
            "at": _now_iso(),
            "source": source,
            "failure": failure[:500],
            "steps": [
                {
                    "id": step.get("id"),
                    "title": step.get("title"),
                    "tool": step.get("tool"),
                    "args": step.get("args"),
                }
                for step in normalized
            ],
        }
    )
    task["status"] = "pending"
    save_task(task)
    emit_trace(
        str(task.get("id") or ""),
        "plan.replanned" if replacing else "plan.created",
        version=task["plan_version"],
        source=source,
        failure=failure,
        steps=history[-1]["steps"],
    )
    return bool(normalized)


def _definitive_failure(text: str) -> bool:
    lower = (text or "").lower()
    return any(
        marker in lower
        for marker in (
            "missing_scope",
            "缺权限",
            "forbidden",
            "unauthorized",
            "请本人授权",
            "不能为空",
        )
    )


def _retryable_failure(text: str) -> bool:
    lower = (text or "").lower()
    return any(
        marker in lower
        for marker in (
            "timeout",
            "timed out",
            "ssl",
            "connection",
            "temporarily",
            "internalservererror",
            "provider internal",
        )
    )


def _replan_after_failure(task: dict[str, Any], failure: str) -> bool:
    if _definitive_failure(failure):
        return False
    count = int(task.get("replan_count") or 0)
    maximum = int(task.get("max_replans") or 2)
    if count >= maximum:
        return False
    task["replan_count"] = count + 1
    return _materialize_agent_plan(task, failure=failure, replacing=True)


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
    task_id = str(task.get("id") or "")
    if task.get("cancel_requested") or str(task.get("status") or "") == "cancelled":
        task["status"] = "cancelled"
        save_task(task)
        return "任务已取消。", False

    tool = str(step.get("tool") or "")
    args = step.get("args") if isinstance(step.get("args"), dict) else {}
    clean_args = {str(k): str(v) for k, v in args.items()}
    clean_args.setdefault("chat_id", str(task.get("chat_id") or ""))
    title = str(step.get("title") or tool)
    if tool == "report_write" and not clean_args.get("materials"):
        clean_args["materials"] = _collect_materials_for_report(task)
    if tool in CONFIRM_TOOLS and not confirm_write and (
        step.get("requires_confirm") or str(step.get("status") or "") == "blocked"
    ):
        step["status"] = "blocked"
        step["error"] = "写操作需确认；回复「确认写入」。"
        task["status"] = "blocked"
        task["blocked_reason"] = "confirmation"
        save_task(task)
        emit_trace(task_id, "step.blocked", step_id=step.get("id"), tool=tool, reason="confirmation")
        return (
            f"第 {index + 1} 步「{title}」待确认。回复「确认写入」执行跟进账/待办/云文档同步。",
            False,
        )

    step["status"] = "running"
    step["attempts"] = int(step.get("attempts") or 0) + 1
    step["started_at"] = _now_iso()
    step["error"] = ""
    task["status"] = "running"
    task["blocked_reason"] = ""
    save_task(task)
    emit_trace(
        task_id,
        "step.started",
        step_id=step.get("id"),
        title=title,
        tool=tool,
        args=clean_args,
        attempt=step["attempts"],
    )
    started = time.monotonic()
    result = ""
    failure = ""
    try:
        if tool in WRITE_TOOLS:
            result = run_write_tool(tool, clean_args, confirmed=True)
        elif tool == "summarize":
            result = _summarize_task(task)
        else:
            result = run_tool(tool, clean_args)
        if tool != "summarize" and _looks_like_failure(result):
            failure = result[:1000]
    except Exception as exc:
        failure = str(exc)[:1000]

    step["finished_at"] = _now_iso()
    step["latency_ms"] = int((time.monotonic() - started) * 1000)
    latest = load_task(task_id)
    if latest and latest.get("cancel_requested"):
        task["cancel_requested"] = True
        task["status"] = "cancelled"
        step["status"] = "cancelled"
        step["result"] = result
        save_task(task)
        emit_trace(task_id, "task.cancelled", during_step=step.get("id"))
        return "任务已取消；当前工具返回后没有继续执行。", False

    if failure:
        step["result"] = result
        step["error"] = failure[:240]
        if _retryable_failure(failure) and int(step.get("attempts") or 0) < 2:
            step["status"] = "pending"
            task["status"] = "running"
            save_task(task)
            emit_trace(
                task_id,
                "step.retry_scheduled",
                step_id=step.get("id"),
                tool=tool,
                failure=failure,
            )
            return f"第 {index + 1} 步暂时失败，正在重试：{title}", False
        step["status"] = "failed"
        task["status"] = "blocked"
        task["blocked_reason"] = "authority" if _definitive_failure(failure) else "execution"
        save_task(task)
        emit_trace(
            task_id,
            "step.failed",
            step_id=step.get("id"),
            tool=tool,
            failure=failure,
            latency_ms=step["latency_ms"],
        )
        if _replan_after_failure(task, failure):
            return f"第 {index + 1} 步失败：{title}；已根据失败证据重规划。", True
        return f"第 {index + 1} 步受阻：{title}", False

    step["result"] = result
    step["status"] = "done"
    step["error"] = ""
    save_task(task)
    emit_trace(
        task_id,
        "step.succeeded",
        step_id=step.get("id"),
        tool=tool,
        result=result,
        latency_ms=step["latency_ms"],
    )
    return f"第 {index + 1} 步完成：{title}", True


def _refresh_task_status(task: dict[str, Any]) -> None:
    if task.get("cancel_requested") or str(task.get("status") or "") == "cancelled":
        task["status"] = "cancelled"
        return
    steps = task.get("steps") or []
    if not isinstance(steps, list) or not steps:
        task["status"] = "done"
        return
    statuses = [
        str(step.get("status") or "")
        for step in steps
        if isinstance(step, dict)
    ]
    active = [status for status in statuses if status != "superseded"]
    if any(status == "blocked" for status in active):
        task["status"] = "blocked"
    elif any(status == "running" for status in active):
        task["status"] = "running"
    elif any(status == "failed" for status in active):
        task["status"] = "blocked"
    elif active and all(status in {"done", "cancelled"} for status in active):
        task["status"] = "done" if all(status == "done" for status in active) else "cancelled"
    elif str(task.get("status") or "") == "queued":
        task["status"] = "queued"
    elif any(status == "done" for status in active):
        task["status"] = "running"
    else:
        task["status"] = "pending"


def _finalize_if_done(task: dict[str, Any]) -> bool:
    """Stamp completed_at + experience once. True if newly finalized."""
    if str(task.get("status") or "") != "done" or task.get("completed_at"):
        return False
    task["completed_at"] = _now_iso()
    task_id = str(task.get("id") or "")
    emit_trace(task_id, "task.completed", plan_version=task.get("plan_version"))
    from ..office.memory import note_task_outcome

    last = ""
    for step in reversed(task.get("steps") or []):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool") or "") == "summarize" and str(step.get("result") or "").strip():
            last = str(step.get("result") or "")[:200]
            break
        if str(step.get("status") or "") == "done" and not last:
            last = str(step.get("result") or "")[:200]
    note_task_outcome(str(task.get("goal") or ""), "done", summary=last)
    return True


def run_next(task_id: str, *, confirm_write: bool = False) -> str:
    task = load_task(task_id)
    if not task:
        return "找不到这个任务。"
    status = str(task.get("status") or "")
    if status == "cancelled" or task.get("cancel_requested"):
        return format_status(task) + "\n\n任务已取消。"
    if status == "queued":
        task["status"] = "running"
        save_task(task)

    index = _pending_index(task)
    planned_now = False
    if index is None and str(task.get("phase") or "") == "observe":
        planned_now = _materialize_agent_plan(task)
        task = load_task(task_id) or task
        index = _pending_index(task)
    if index is None:
        _refresh_task_status(task)
        _finalize_if_done(task)
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
    if (
        str(task.get("phase") or "") == "observe"
        and _pending_index(task) is None
        and str(task.get("status") or "") not in {"blocked", "cancelled"}
    ):
        planned_now = _materialize_agent_plan(task)
        task = load_task(task_id) or task
    _refresh_task_status(task)
    _finalize_if_done(task)
    save_task(task)

    visible = [
        step
        for step in task.get("steps") or []
        if isinstance(step, dict) and str(step.get("status") or "") != "superseded"
    ]
    done = sum(1 for step in visible if str(step.get("status") or "") == "done")
    head = f"任务「{task.get('goal')}」进度 {done}/{len(visible)}"
    if str(task.get("status") or "") == "cancelled":
        return f"{head}\n\n任务已取消。"
    if str(task.get("status") or "") == "done":
        summary = ""
        for step in reversed(task.get("steps") or []):
            if isinstance(step, dict) and str(step.get("tool") or "") == "summarize":
                summary = str(step.get("result") or "")
                if summary:
                    break
        tail = summary or format_status(task)
        return f"{head}\n\n任务完成。\n\n{tail}"
    if _blocked_write_indices(task):
        return f"{head}\n{msg}\n\n回复「确认写入」继续同步。"
    if planned_now:
        return f"{head}\n{msg}\n\n已基于真实观察生成执行计划，将继续后台运行。"
    return f"{head}\n{msg}"


def run_all(task_id: str, *, stop_on_blocked: bool = True) -> str:
    messages: list[str] = []
    cycles = 0
    while cycles < 64:
        cycles += 1
        task = load_task(task_id)
        if not task:
            return "找不到这个任务。"
        if task.get("cancel_requested") or str(task.get("status") or "") == "cancelled":
            task["status"] = "cancelled"
            save_task(task)
            break
        index = _pending_index(task)
        if index is None and str(task.get("phase") or "") == "observe":
            if _materialize_agent_plan(task):
                continue
        if index is None:
            _refresh_task_status(task)
            _finalize_if_done(task)
            save_task(task)
            break
        msg = run_next(task_id)
        messages.append(msg)
        task = load_task(task_id)
        if not task:
            break
        if stop_on_blocked and str(task.get("status") or "") == "blocked":
            break
        if str(task.get("status") or "") in TERMINAL_STATUSES:
            break
    else:
        task = load_task(task_id)
        if task:
            task["status"] = "blocked"
            task["blocked_reason"] = "cycle_limit"
            save_task(task)
            emit_trace(task_id, "task.blocked", reason="cycle_limit")
            messages.append("任务达到最大执行轮次，已安全停止。")
    final = load_task(task_id)
    if not final:
        return messages[-1] if messages else "任务结束。"
    if messages:
        return messages[-1]
    return format_status(final)


def confirm_writes(chat_id: str, *, step_id: int | None = None) -> str:
    from .agent.service import active_agent_task, confirm_agent_writes

    if active_agent_task(chat_id):
        return confirm_agent_writes(chat_id)
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
        if str(step.get("tool") or "") not in CONFIRM_TOOLS:
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
    rest = ""
    if _pending_index(task) is not None:
        if task.get("background"):
            task["status"] = "queued"
            task["blocked_reason"] = ""
            save_task(task)
            rest = "剩余步骤已放回后台队列。"
        else:
            rest = run_next(task_id)
    head = "写操作已确认执行：\n" + "\n".join(messages)
    if rest:
        return head + "\n\n" + rest
    if str(task.get("status") or "") == "done":
        return head + "\n\n任务已全部完成。"
    return head + "\n\n" + format_status(task)


def format_status(task: dict[str, Any]) -> str:
    goal = str(task.get("goal") or "")
    status = str(task.get("status") or "unknown")
    labels = {
        "pending": "待执行",
        "queued": "后台排队",
        "running": "执行中",
        "blocked": "等待确认/处理",
        "done": "已完成",
        "failed": "失败",
        "cancelled": "已取消",
    }
    phase = "观察" if str(task.get("phase") or "") == "observe" else "执行"
    lines = [
        f"任务：{goal}",
        f"状态：{labels.get(status, status)}",
        f"阶段：{phase} · 计划版本 v{int(task.get('plan_version') or 0)}",
        "",
        "步骤：",
    ]
    for step in task.get("steps") or []:
        if not isinstance(step, dict) or str(step.get("status") or "") == "superseded":
            continue
        mark = {
            "done": "✓",
            "pending": "○",
            "running": "…",
            "failed": "✗",
            "blocked": "!",
            "cancelled": "×",
        }.get(str(step.get("status") or ""), "?")
        suffix = "（需确认）" if step.get("requires_confirm") else ""
        attempts = int(step.get("attempts") or 0)
        retry = f"（尝试 {attempts} 次）" if attempts > 1 else ""
        lines.append(f"{mark} {step.get('id')}. {step.get('title') or step.get('tool')}{suffix}{retry}")
        err = str(step.get("error") or "").strip()
        if err:
            lines.append(f"   原因：{err[:120]}")
    return "\n".join(lines)


def enqueue_background_goal(goal: str, chat_id: str) -> dict[str, Any]:
    """Create a queued agent task and ensure the worker is running."""
    from .agent.settings import use_legacy_runner

    if not use_legacy_runner():
        import re

        from .agent.service import load_task as load_agent_task
        from .agent.service import start_agent_task

        msg = start_agent_task(goal, chat_id, background=True)
        match = re.search(r"（([0-9a-f]{12})）", msg)
        if match:
            task = load_agent_task(match.group(1))
            if task:
                return task
        return {"id": "", "goal": goal, "runtime": "agent_v2", "status": "queued"}
    task = create_task(
        goal,
        chat_id,
        _observation_steps(goal),
        mode="agent",
        background=True,
    )
    ensure_worker()
    return task


def _legacy_start_task(goal: str, chat_id: str, *, background: bool = False) -> str:
    steps = _observation_steps(goal) if background else plan_steps(goal)
    task = create_task(
        goal,
        chat_id,
        steps,
        mode="agent" if background else "fixed",
        background=background,
    )
    if background:
        return (
            f"任务已创建（{task['id']}），正在后台读取真实上下文。\n\n"
            "完成观察后会动态规划、逐步执行并把结果发回；可说「任务进度」或「取消任务」。"
        )
    body = run_all(task["id"])
    if _blocked_write_indices(load_task(task["id"]) or task):
        body += "\n\n写回飞书/跟进账需你确认：回复「确认写入」。"
    return f"任务已创建（{task['id']}）。\n\n{body}"


def start_task(goal: str, chat_id: str, *, background: bool = False) -> str:
    from .agent.settings import use_legacy_runner

    if use_legacy_runner():
        return _legacy_start_task(goal, chat_id, background=background)
    from .agent.service import start_agent_task

    return start_agent_task(goal, chat_id, background=background)


def continue_task(chat_id: str) -> str:
    from .agent.service import active_agent_task, continue_agent_task

    if active_agent_task(chat_id):
        return continue_agent_task(chat_id)
    task = active_task_for_chat(chat_id)
    if not task:
        return "当前没有进行中的任务。可以说「规划 XXX」新建一个。"
    status = str(task.get("status") or "")
    if status == "done":
        return format_status(task) + "\n\n这条任务已经跑完了。"
    if status == "cancelled":
        return format_status(task) + "\n\n这条任务已取消。"
    if status == "blocked" and str(task.get("blocked_reason") or "") == "confirmation":
        return format_status(task) + "\n\n写操作仍待确认；回复「确认写入」。"
    if task.get("background"):
        task["status"] = "queued"
        task["blocked_reason"] = ""
        for step in task.get("steps") or []:
            if not isinstance(step, dict):
                continue
            if str(step.get("status") or "") == "failed":
                step["status"] = "pending"
        save_task(task)
        emit_trace(str(task.get("id") or ""), "task.resumed")
        return "任务已重新放回后台队列；完成后会把结果发回。"
    return run_next(str(task.get("id") or ""))


def status_task(chat_id: str) -> str:
    from .agent.service import active_agent_task, status_agent_task

    if active_agent_task(chat_id):
        return status_agent_task(chat_id)
    task = active_task_for_chat(chat_id)
    if not task:
        agent_msg = status_agent_task(chat_id)
        if "还没有 Agent 任务" not in agent_msg:
            return agent_msg
        latest = _iter_chat_tasks(chat_id)
        if latest:
            return format_status(latest[0])
        return "还没有任务。可以说「规划 A6 上线」开始。"
    return format_status(task)


def cancel_task(chat_id: str, *, task_id: str = "") -> str:
    from .agent.service import (
        active_agent_task,
        cancel_agent_task,
        load_task as load_agent_task,
    )

    if task_id:
        agent = load_agent_task(task_id)
        if agent and agent.get("runtime") == "agent_v2":
            return cancel_agent_task(chat_id, task_id=task_id)
    if active_agent_task(chat_id):
        return cancel_agent_task(chat_id, task_id=task_id)
    task = load_task(task_id) if task_id else active_task_for_chat(chat_id)
    if not task:
        return "没有可取消的任务。"
    status = str(task.get("status") or "")
    if status in TERMINAL_STATUSES:
        return format_status(task) + "\n\n任务已经结束，不能重复取消。"
    task["cancel_requested"] = True
    task["status"] = "cancelled"
    task["cancelled_at"] = _now_iso()
    for step in task.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if str(step.get("status") or "") in {"pending", "queued", "running", "blocked"}:
            step["status"] = "cancelled"
    save_task(task)
    emit_trace(str(task.get("id") or ""), "task.cancelled", requested_from=chat_id)
    return f"已取消任务「{task.get('goal')}」（{task.get('id')}）。"


def has_queued_tasks() -> bool:
    root = tasks_dir()
    if not root.is_dir():
        return False
    for path in root.glob("*.json"):
        task = load_task(path.stem)
        if not task or not task.get("background"):
            continue
        if str(task.get("status") or "") == "queued":
            return True
    return False


def _claim_next_background_task() -> dict[str, Any] | None:
    """Claim exactly one queued task across worker processes."""
    import fcntl

    from ..core.run_store import acquire_lease, apply_recovered_leases

    apply_recovered_leases(load_task, save_task)
    root = tasks_dir()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".worker.lock"
    owner = f"pid:{os.getpid()}"
    try:
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            paths = sorted(root.glob("*.json"), key=lambda path: path.stat().st_mtime)
            for path in paths:
                task = load_task(path.stem)
                if not task or not task.get("background"):
                    continue
                if task.get("runtime") == "agent_v2":
                    continue
                if str(task.get("status") or "") != "queued":
                    continue
                task["status"] = "running"
                task["worker_pid"] = os.getpid()
                task["worker_started_at"] = _now_iso()
                save_task(task)
                tid = str(task.get("id") or "")
                if tid and not acquire_lease(tid, owner):
                    task["status"] = "queued"
                    task["worker_pid"] = 0
                    save_task(task)
                    continue
                emit_trace(tid, "task.claimed", worker_pid=os.getpid())
                return task
    except OSError:
        return None
    return None


def _notify_background_result(task: dict[str, Any], body: str) -> None:
    chat_id = str(task.get("chat_id") or "")
    if not chat_id or os.environ.get("FEISHU_PARTNER_DISABLE_NOTIFICATIONS") == "1":
        return
    status = str(task.get("status") or "")
    notice_key = ":".join(
        (
            status,
            str(task.get("plan_version") or 0),
            str(task.get("blocked_reason") or ""),
        )
    )
    if str(task.get("last_notice_key") or "") == notice_key:
        return
    text = body
    if status == "blocked" and str(task.get("blocked_reason") or "") == "confirmation":
        text += "\n\n需要写入飞书/跟进账时，请回复「确认写入」。"
    try:
        from ..actions import send_text

        sent = send_text(chat_id, text)
    except Exception as exc:
        emit_trace(
            str(task.get("id") or ""),
            "notification.failed",
            error=str(exc),
        )
        return
    if sent != "已发送。":
        emit_trace(
            str(task.get("id") or ""),
            "notification.failed",
            error=sent,
        )
        return
    task["last_notice_key"] = notice_key
    task["notified_at"] = _now_iso()
    save_task(task)
    emit_trace(str(task.get("id") or ""), "notification.sent", chat_id=chat_id)


def worker_once(*, notify: bool = True) -> bool:
    from .agent.service import claim_queued_agent_task, load_task as load_agent_task
    from .agent.service import resume_agent_task

    agent = claim_queued_agent_task()
    if agent:
        task_id = str(agent.get("id") or "")
        body = resume_agent_task(task_id)
        final = load_agent_task(task_id) or agent
        if notify:
            _notify_background_result(final, body)
        return True
    task = _claim_next_background_task()
    if not task:
        return False
    task_id = str(task.get("id") or "")
    body = run_all(task_id)
    final = load_task(task_id) or task
    final["worker_pid"] = 0
    save_task(final)
    if notify:
        _notify_background_result(final, body)
    return True


def run_worker(*, drain: bool = False) -> int:
    count = 0
    while worker_once():
        count += 1
        if not drain:
            break
    return count


_WORKER_PROC: subprocess.Popen[Any] | None = None


def ensure_worker() -> bool:
    """Start a detached queue worker without blocking Feishu event consumption."""
    global _WORKER_PROC
    if not has_queued_tasks():
        return False
    if _WORKER_PROC is not None and _WORKER_PROC.poll() is None:
        return True
    from ..paths import REPO_ROOT

    root = REPO_ROOT
    env = dict(os.environ)
    old_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(root) + (os.pathsep + old_path if old_path else "")
    try:
        _WORKER_PROC = subprocess.Popen(
            [sys.executable, "-m", "partner", "agent-worker", "--drain"],
            cwd=str(root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        _WORKER_PROC = None
        return False
    return True
