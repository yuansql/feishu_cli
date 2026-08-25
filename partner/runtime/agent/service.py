"""Public Agent Runtime v2 service (LangGraph think→act)."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ...core.trace import emit_trace
from .graph import run_loop

CN_TZ = timezone(timedelta(hours=8))
RUNTIME = "agent_v2"


def tasks_dir() -> Path:
    override = os.environ.get("FEISHU_PARTNER_TASKS_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner" / "tasks"


def _now() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _path(task_id: str) -> Path:
    return tasks_dir() / f"{task_id}.json"


def save_task(task: dict[str, Any]) -> None:
    root = tasks_dir()
    root.mkdir(parents=True, exist_ok=True)
    task = dict(task)
    task["updated_at"] = _now()
    path = _path(str(task["id"]))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        from ...core.run_store import upsert_run

        upsert_run(task)
    except Exception:
        emit_trace(str(task.get("id") or ""), "run_store.upsert_failed")


def load_task(task_id: str) -> dict[str, Any] | None:
    path = _path(task_id)
    if not path.is_file():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return blob if isinstance(blob, dict) else None


def active_agent_task(chat_id: str) -> dict[str, Any] | None:
    cid = (chat_id or "").strip()
    if not cid or not tasks_dir().is_dir():
        return None
    rows: list[dict[str, Any]] = []
    for path in tasks_dir().glob("*.json"):
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(blob, dict):
            continue
        if blob.get("runtime") != RUNTIME:
            continue
        if str(blob.get("chat_id") or "") != cid:
            continue
        if str(blob.get("status") or "") in {"done", "failed", "cancelled"}:
            continue
        rows.append(blob)
    rows.sort(key=lambda t: str(t.get("updated_at") or ""), reverse=True)
    return rows[0] if rows else None


def _format_result(task: dict[str, Any]) -> str:
    status = str(task.get("status") or "")
    summary = str(task.get("summary") or "").strip()
    thoughts = list(task.get("thoughts") or [])
    lines = [f"任务 {task.get('id')} · runtime={RUNTIME} · {status}"]
    if thoughts:
        lines.append("思考：")
        for item in thoughts[-5:]:
            lines.append(f"- {item}")
    if summary:
        lines.append("")
        lines.append(summary)
    if status == "blocked":
        lines.append("")
        lines.append("写操作待确认：回复「确认写入」。也可「取消任务」。")
    return "\n".join(lines).strip()


def _apply_loop_result(task: dict[str, Any], final: dict[str, Any]) -> dict[str, Any]:
    task["observations"] = list(final.get("observations") or [])
    task["status"] = str(final.get("status") or task.get("status") or "done")
    task["summary"] = str(final.get("summary") or "")
    task["pending_write"] = final.get("pending_write")
    task["steps_taken"] = int(final.get("steps_taken") or 0)
    thought = str(final.get("last_thought") or "").strip()
    if thought:
        thoughts = list(task.get("thoughts") or [])
        thoughts.append(thought)
        task["thoughts"] = thoughts[-20:]
    task["allow_writes"] = bool(final.get("allow_writes"))
    save_task(task)
    emit_trace(
        str(task["id"]),
        "agent_v2.tick",
        status=task["status"],
        steps=task.get("steps_taken"),
        thought=thought[:200],
    )
    return task


def start_agent_task(goal: str, chat_id: str, *, background: bool = False) -> str:
    from .settings import agent_max_steps, ensure_agent_config

    ensure_agent_config()
    cleaned = (goal or "").strip()
    if not cleaned:
        return "说一下要规划的目标。"
    task_id = uuid.uuid4().hex[:12]
    task = {
        "id": task_id,
        "chat_id": (chat_id or "").strip(),
        "goal": cleaned,
        "runtime": RUNTIME,
        "schema_version": 3,
        "status": "queued" if background else "running",
        "background": bool(background),
        "allow_writes": False,
        "observations": [],
        "thoughts": [],
        "pending_write": None,
        "summary": "",
        "steps_taken": 0,
        "max_steps": agent_max_steps(),
        "created_at": _now(),
        "updated_at": _now(),
    }
    save_task(task)
    emit_trace(task_id, "agent_v2.created", goal=cleaned, background=bool(background))
    if background:
        from ..runner import ensure_worker

        ensure_worker()
        return (
            f"任务已创建（{task_id}），Agent 正在后台自己想下一步并取数。\n"
            "可说「任务进度」或「取消任务」。"
        )
    return _run_task(task)


def _run_task(task: dict[str, Any]) -> str:
    task["status"] = "running"
    save_task(task)
    final = run_loop(
        goal=str(task.get("goal") or ""),
        chat_id=str(task.get("chat_id") or ""),
        observations=list(task.get("observations") or []),
        allow_writes=bool(task.get("allow_writes")),
        max_steps=int(task.get("max_steps") or 8),
        pending_write=task.get("pending_write")
        if isinstance(task.get("pending_write"), dict)
        else None,
    )
    task = _apply_loop_result(task, final)
    return _format_result(task)


def resume_agent_task(task_id: str) -> str:
    task = load_task(task_id)
    if not task or task.get("runtime") != RUNTIME:
        return "找不到 Agent 任务。"
    if str(task.get("status") or "") == "cancelled":
        return _format_result(task)
    return _run_task(task)


def continue_agent_task(chat_id: str) -> str:
    task = active_agent_task(chat_id)
    if not task:
        return "当前没有进行中的 Agent 任务。可以说「规划 XXX」新建。"
    status = str(task.get("status") or "")
    if status == "blocked":
        return _format_result(task) + "\n\n若要写入，回复「确认写入」。"
    if status in {"done", "failed"}:
        return _format_result(task)
    return _run_task(task)


def confirm_agent_writes(chat_id: str) -> str:
    task = active_agent_task(chat_id)
    if not task:
        # also allow most recent blocked agent task
        return "当前没有待确认的 Agent 写操作。"
    if str(task.get("status") or "") != "blocked":
        return _format_result(task) + "\n\n现在没有卡在确认闸上。"
    task["allow_writes"] = True
    task["status"] = "running"
    task["pending_write"] = None
    save_task(task)
    emit_trace(str(task["id"]), "agent_v2.confirm_writes")
    return _run_task(task)


def status_agent_task(chat_id: str) -> str:
    task = active_agent_task(chat_id)
    if not task:
        # show last agent task for chat if any
        cid = (chat_id or "").strip()
        latest = None
        if tasks_dir().is_dir():
            for path in tasks_dir().glob("*.json"):
                try:
                    blob = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(blob, dict) and blob.get("runtime") == RUNTIME and str(blob.get("chat_id") or "") == cid:
                    if latest is None or str(blob.get("updated_at") or "") > str(latest.get("updated_at") or ""):
                        latest = blob
        if not latest:
            return "还没有 Agent 任务。"
        return _format_result(latest)
    return _format_result(task)


def cancel_agent_task(chat_id: str, *, task_id: str = "") -> str:
    task = load_task(task_id) if task_id else active_agent_task(chat_id)
    if not task or task.get("runtime") != RUNTIME:
        return "没有可取消的 Agent 任务。"
    task["status"] = "cancelled"
    task["summary"] = (task.get("summary") or "") + "\n（已取消）"
    save_task(task)
    emit_trace(str(task["id"]), "agent_v2.cancelled")
    return _format_result(task)


def claim_queued_agent_task() -> dict[str, Any] | None:
    from ...core.run_store import acquire_lease, apply_recovered_leases

    apply_recovered_leases(load_task, save_task)
    if not tasks_dir().is_dir():
        return None
    rows: list[dict[str, Any]] = []
    for path in tasks_dir().glob("*.json"):
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(blob, dict)
            and blob.get("runtime") == RUNTIME
            and blob.get("background")
            and str(blob.get("status") or "") == "queued"
        ):
            rows.append(blob)
    rows.sort(key=lambda t: str(t.get("created_at") or ""))
    if not rows:
        return None
    task = rows[0]
    task["status"] = "running"
    save_task(task)
    tid = str(task.get("id") or "")
    if tid and not acquire_lease(tid, f"agent:{os.getpid()}"):
        task["status"] = "queued"
        save_task(task)
        return None
    return task
