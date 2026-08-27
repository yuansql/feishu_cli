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

# Intent-patch actions that can be appended by other users in group chats.
_PATCH_ACTIONS = frozenset({"append", "override", "cancel"})


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
        approval = task.get("pending_write", {}).get("approval")
        card_hint = (
            "已发送确认卡片，请点击确认或取消。"
            if approval and approval.get("message_id")
            else "写操作待确认：回复「确认写入」。也可「取消任务」。"
        )
        lines.append("")
        lines.append(card_hint)
    return "\n".join(lines).strip()


def format_patches_text(task: dict[str, Any]) -> str:
    """Format an Agent v2 task and its intent patches for CLI output."""
    task_id = str(task.get("id") or "")
    status = str(task.get("status") or "")
    goal = str(task.get("goal") or "").strip() or "(无目标)"
    lines = [
        f"任务 {task_id} · {status}",
        f"目标：{goal}",
    ]
    patches = list(task.get("intent_patches") or [])
    if patches:
        lines.append("")
        lines.append(f"意图补丁 {len(patches)} 条：")
        for idx, p in enumerate(patches, 1):
            action = str(p.get("action") or "append")
            author = str(p.get("sender_name") or p.get("author_open_id") or "某人")
            text = str(p.get("text") or "").strip() or "(无内容)"
            merged = "已合并" if p.get("merged") else "待合并"
            lines.append(f"  {idx}. [{action}] {author} · {merged}")
            lines.append(f"     {text}")
    else:
        lines.append("")
        lines.append("暂无意图补丁。")
    observations = list(task.get("observations") or [])
    if observations:
        lines.append("")
        lines.append(f"当前 observations {len(observations)} 条：")
        for item in observations:
            lines.append(f"- {item}")
    return "\n".join(lines)


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


def _merge_intent_patches(task: dict[str, Any]) -> bool:
    """Merge unmerged intent patches into observations and mark them merged.

    Returns True if any patch was consumed. Cancel patches terminal the task.
    """
    patches = task.get("intent_patches")
    if not isinstance(patches, list):
        return False
    changed = False
    observations = list(task.get("observations") or [])
    for patch in patches:
        if not isinstance(patch, dict) or patch.get("merged"):
            continue
        action = str(patch.get("action") or "append").strip().lower()
        if action not in _PATCH_ACTIONS:
            action = "append"
        text = str(patch.get("text") or "").strip()
        if not text:
            patch["merged"] = True
            continue
        changed = True
        author = str(patch.get("sender_name") or patch.get("author_open_id") or "某人")
        if action == "cancel":
            task["status"] = "cancelled"
            task["summary"] = (task.get("summary") or "") + f"\n（{author} 取消任务）"
            task["pending_write"] = None
            patch["merged"] = True
            save_task(task)
            emit_trace(
                str(task.get("id") or ""),
                "agent_v2.patch_cancelled",
                author=author,
            )
            return True
        label = {
            "append": "补充要求",
            "override": "目标修正",
        }.get(action, "补充要求")
        observations.append(f"[{label} · {author}] {text}")
        patch["merged"] = True
    if changed:
        task["observations"] = observations
        save_task(task)
    return changed


def append_intent_patch(
    task_id: str,
    patch: dict[str, Any],
) -> dict[str, Any] | None:
    """Append an intent patch to a running/blocked agent task.

    Patch fields: author_open_id, text, action (append/override/cancel), ts.
    Returns the updated task or None if the task is not patchable.
    """
    task = load_task(task_id)
    if not task or task.get("runtime") != RUNTIME:
        return None
    status = str(task.get("status") or "")
    if status in {"done", "failed", "cancelled"}:
        return None
    author = str(patch.get("author_open_id") or patch.get("sender_name") or "")
    text = str(patch.get("text") or "").strip()
    if not author or not text:
        return None
    action = str(patch.get("action") or "append").strip().lower()
    if action not in _PATCH_ACTIONS:
        action = "append"
    entry = {
        "author_open_id": author,
        "sender_name": str(patch.get("sender_name") or "").strip(),
        "text": text,
        "action": action,
        "ts": str(patch.get("ts") or "").strip() or _now(),
        "merged": False,
    }
    patches = task.get("intent_patches")
    if not isinstance(patches, list):
        patches = []
    patches.append(entry)
    task["intent_patches"] = patches
    save_task(task)
    emit_trace(
        str(task.get("id") or ""),
        "agent_v2.patch_appended",
        author=author,
        action=action,
        text=text[:120],
    )
    return task


def claim_confirmation(task_id: str, operator_id: str) -> dict[str, Any]:
    """Allow another user to take over a blocked approval and resume the task.

    Returns {"ok": bool, "task": task_or_none, "error": str, "message_id": str}.
    """
    result: dict[str, Any] = {
        "ok": False,
        "task": None,
        "error": "",
        "message_id": "",
    }
    task = load_task(task_id)
    if not task or task.get("runtime") != RUNTIME:
        result["error"] = "找不到 Agent 任务。"
        return result
    if str(task.get("status")) != "blocked":
        result["error"] = "任务当前不在确认闸上。"
        result["task"] = task
        return result
    pending = task.get("pending_write") or {}
    approval = pending.get("approval") or {}
    message_id = str(approval.get("message_id") or "").strip()
    token = str(approval.get("token") or "").strip()
    if not message_id:
        result["error"] = "没有待确认的写操作。"
        return result
    from ...core.run_store import approve_approval

    resolved = approve_approval(message_id, operator_id=operator_id, token=token)
    if not resolved:
        # Approval may already be resolved or token mismatch; still safe to fail.
        result["error"] = "接管确认失败（可能已被处理或令牌不匹配）。"
        return result
    task["allow_writes"] = True
    task["status"] = "running"
    task["pending_write"] = None
    save_task(task)
    emit_trace(
        str(task.get("id") or ""),
        "agent_v2.confirmation_claimed",
        operator_id=operator_id,
        message_id=message_id,
    )
    result["ok"] = True
    result["task"] = task
    result["message_id"] = message_id
    return result


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
        "schema_version": 4,
        "status": "queued" if background else "running",
        "background": bool(background),
        "allow_writes": False,
        "observations": [],
        "thoughts": [],
        "intent_patches": [],
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


def _inject_chat_context(task: dict[str, Any]) -> None:
    """Prepend recent chat context as an observation if available."""
    chat_id = str(task.get("chat_id") or "").strip()
    if not chat_id:
        return
    try:
        from ...core import chat_context

        ctx = chat_context.recent_context(chat_id, hours=24, max_items=20)
    except Exception:  # noqa: BLE001
        return
    if not ctx:
        return
    observations: list[str] = list(task.get("observations") or [])
    observations.append(ctx)
    task["observations"] = observations


def _run_task(task: dict[str, Any]) -> str:
    task["status"] = "running"
    _inject_chat_context(task)
    _merge_intent_patches(task)
    # Re-load in case a cancel patch terminalled the task during merge.
    task = load_task(str(task.get("id") or "")) or task
    if str(task.get("status")) == "cancelled":
        save_task(task)
        return _format_result(task)
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
    if str(task.get("status")) == "blocked" and task.get("pending_write"):
        _maybe_request_approval(task)
    return _format_result(task)


def _maybe_request_approval(task: dict[str, Any]) -> None:
    """If a task is blocked on a write, send an interactive approval card."""
    pending = task.get("pending_write")
    if not isinstance(pending, dict):
        return
    # Avoid duplicate requests if a card was already sent.
    existing = pending.get("approval", {})
    if existing and existing.get("message_id"):
        return
    chat_id = str(task.get("chat_id") or "").strip()
    task_id = str(task.get("id") or "").strip()
    if not chat_id or not task_id:
        return
    tool = str(pending.get("tool") or "").strip()
    args = pending.get("args") if isinstance(pending.get("args"), dict) else {}
    if not tool and pending.get("reason") != "confirmation":
        return
    message_id = f"apv_{uuid.uuid4().hex[:16]}"
    token = uuid.uuid4().hex
    from ...core.run_store import request_approval
    from ...office.approval_card import approval_card_payload
    from ...actions import send_card

    ok = request_approval(
        task_id=task_id,
        message_id=message_id,
        chat_id=chat_id,
        tool=tool,
        args=args,
        token=token,
    )
    if not ok:
        return
    card = approval_card_payload(
        tool=tool,
        args=args,
        task_id=task_id,
        message_id=message_id,
        token=token,
    )
    result = send_card(chat_id, card, as_identity="bot")
    sent_ok = (result or "").strip() == "已发送。"
    pending["approval"] = {
        "message_id": message_id,
        "requested_at": _now(),
        "token": token,
        "sent_ok": sent_ok,
    }
    task["pending_write"] = pending
    save_task(task)
    emit_trace(
        task_id,
        "agent_v2.approval_requested",
        tool=tool,
        message_id=message_id,
        sent_ok=sent_ok,
    )


def resume_agent_task(task_id: str) -> str:
    task = load_task(task_id)
    if not task or task.get("runtime") != RUNTIME:
        return "找不到 Agent 任务。"
    if str(task.get("status") or "") == "cancelled":
        return _format_result(task)
    return _run_task(task)


def resume_agent_task_after_claim(task_id: str, operator_id: str) -> str:
    """Claim a pending approval from another user and resume the task."""
    result = claim_confirmation(task_id, operator_id)
    if not result.get("ok"):
        return result.get("error") or "接管确认失败。"
    task = result.get("task")
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


def confirm_agent_writes_by_message(message_id: str) -> dict[str, Any]:
    """Resume a blocked task after a card approval callback.

    Returns {"ok": bool, "task": task_or_none, "error": str}.
    """
    from ...core.run_store import get_approval

    approval = get_approval(message_id)
    if not approval:
        return {"ok": False, "task": None, "error": "找不到对应审批记录。"}
    task_id = str(approval.get("task_id") or "").strip()
    task = load_task(task_id) if task_id else None
    if not task or task.get("runtime") != RUNTIME:
        return {"ok": False, "task": None, "error": "关联的 Agent 任务不存在。"}
    if str(task.get("status")) != "blocked":
        return {"ok": False, "task": task, "error": "任务当前不在确认闸上。"}
    task["allow_writes"] = True
    task["status"] = "running"
    task["pending_write"] = None
    save_task(task)
    emit_trace(task_id, "agent_v2.confirm_writes_by_card", message_id=message_id)
    return {"ok": True, "task": task, "error": ""}


def decline_agent_writes_by_message(message_id: str) -> dict[str, Any]:
    """Cancel a blocked task after a card decline callback."""
    from ...core.run_store import get_approval

    approval = get_approval(message_id)
    if not approval:
        return {"ok": False, "task": None, "error": "找不到对应审批记录。"}
    task_id = str(approval.get("task_id") or "").strip()
    task = load_task(task_id) if task_id else None
    if not task or task.get("runtime") != RUNTIME:
        return {"ok": False, "task": None, "error": "关联的 Agent 任务不存在。"}
    task["status"] = "cancelled"
    task["summary"] = (task.get("summary") or "") + "\n（用户取消写入）"
    task["pending_write"] = None
    save_task(task)
    emit_trace(task_id, "agent_v2.decline_writes_by_card", message_id=message_id)
    return {"ok": True, "task": task, "error": ""}


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
