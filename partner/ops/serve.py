from __future__ import annotations

import json
import os
import pty
import re
import select
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..core.ack import ACK_EMOJI, ack_line, should_ack_text
from ..actions import add_reaction, dispatch, send_card, send_style_card, send_text, send_message
from ..office.brief import already_pushed, push_brief
from ..core.events import (
    CardAction,
    InboundMessage,
    extract_card_action,
    extract_inbound_message,
    should_reply,
)
from ..core.ids import BOT_OPEN_ID, P2P_CHAT_ID, USER_OPEN_ID, identity_hint, identity_ready, reload_identity
from ..core.run_store import (
    approve_approval,
    decline_approval,
    expire_stale_approvals,
    list_pending_approvals_for_chat,
    load_run,
)
from ..core import chat_context
from ..office.approval_card import approval_expired_card
from ..office.followup import (
    apply_action,
    assign_push_card,
    format_assign_push,
    ingest,
    in_chat_watch_window,
    load_chat_sync_since,
    mark_chat_synced,
    push_digest,
    should_scan_bitable,
    should_sync_user_chats,
    sync_user_chats,
)
from ..core.inbox import append_item
from ..routing.intents import parse_intent, strip_wake_prefix
from ..routing.p2p_router import refine_p2p_intent
from ..core.lark import find_lark_cli
from ..compose.llm import (
    _looks_like_leak,
    _looks_like_provider_error,
    _looks_like_transport_error,
)
from ..routing.resolved import confirm_card
from ..runtime.runner import ensure_worker
from ..runtime.agent.service import (
    active_agent_task,
    append_intent_patch,
    confirm_agent_writes_by_message,
    decline_agent_writes_by_message,
    resume_agent_task_after_claim,
    start_agent_task,
)
from ..office.watch import consider, format_watch_push

_CONTROL_INTENTS = frozenset({
    "task_status", "task_cancel", "task_confirm", "task_continue",
    "help", "today", "tasks", "brief", "today_recap", "weekly",
    "tomorrow", "chats", "inbox", "approval", "minutes", "aily",
    "digest", "weekly_tasks", "write_weekly", "write_doc", "plan",
    "send", "resolve",
})
_CLAIM_PHRASES = ("我来确认", "我确认", "我接管", "替我确认")
_APPEND_CUES = ("再加", "补充", "也加上", "还要", "另外", "追加", "加上", "别忘了")
_OVERRIDE_CUES = ("改成", "改为")

CN_TZ = timezone(timedelta(hours=8))

LOG_DIR = Path.home() / ".feishu-partner"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_FU_ACTS = frozenset({"fu_done", "fu_snooze", "fu_ignore"})
_last_bitable_scan = 0.0
_last_chat_sync = 0.0
_last_approval_expire_check = 0.0
_APPROVAL_EXPIRE_INTERVAL_SEC = 60.0
_CHAT_CONTEXT_DECAY_INTERVAL_SEC = 3600.0
_TRIGGER_POLL_INTERVAL_SEC = 60.0
_last_chat_context_decay = 0.0
_last_trigger_poll = 0.0


def _log(line: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "serve.log").open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip() + "\n")
    print(line, file=sys.stderr, flush=True)


def _disable_card_button(
    message_id: str,
    key: str,
    label: str,
    card: dict[str, Any] | None = None,
) -> None:
    """Patch the original card so the clicked button becomes disabled and renamed.

    Delegates to messaging.disable_card_buttons, which re-caches the merged card
    after every patch: rapid consecutive clicks accumulate disabled buttons
    instead of clobbering each other. (2026-09-03 连点 4 个「完成」只有 1 个变灰——
    旧实现 patch 后不回写缓存，每次都拿原始卡片全量覆盖，后写的冲掉先写的。)
    """
    if not message_id:
        return
    from ..office.messaging import disable_card_buttons

    _ok, note = disable_card_buttons(message_id, [(key, label)], card=card)
    _log(note)



def send_ok(result: str) -> bool:
    return (result or "").strip() == "已发送。"


def looks_like_bad_reply(text: str) -> bool:
    blob = (text or "").strip()
    if not blob:
        return True
    if _looks_like_transport_error(blob):
        return True
    if _looks_like_provider_error(blob):
        return True
    if _looks_like_leak(blob):
        return True
    # Hermes / partner prompt residue that must never reach Feishu.
    if any(
        n in blob
        for n in (
            "按材料原文回复即可",
            "不用再调用工具，也不许编造",
            "jsonschema",
            "'local' is not of type",
            "让我组织一下",
            "可能的回复",
            "用第一人称写回复",
        )
    ):
        return True
    return False


def looks_like_transient_fail(result: str) -> bool:
    if send_ok(result):
        return False
    blob = (result or "").lower()
    if "missing_scope" in blob or "缺权限" in blob:
        return False
    if _looks_like_transport_error(result):
        return True
    return any(
        token in blob
        for token in (
            "timeout",
            "timed out",
            "econnreset",
            "connection reset",
            "429",
            "502",
            "503",
            "504",
        )
    )


def send_checked(
    chat_id: str,
    text: str,
    *,
    as_identity: str = "bot",
    attempts: int = 2,
) -> str:
    result = send_message(chat_id, text, as_identity=as_identity, attempts=attempts)
    if send_ok(result):
        if result != "已发送。":
            _log(f"send-retry ok")
        return result
    _log(f"send-checked: {result[:160]}")
    return result


def reply_user(msg: InboundMessage) -> None:
    channel = "group" if msg.chat_type == "group" else "p2p"
    intent = parse_intent(msg.text)
    if channel == "p2p":
        intent = refine_p2p_intent(msg.text, intent)
    _log(f"intent={intent.action} text={msg.text[:80]!r}")
    reacted = add_reaction(msg.message_id, ACK_EMOJI)
    _log("ack-react: " + reacted)
    if should_ack_text(intent.action):
        acked = send_checked(msg.chat_id, ack_line(intent.action), as_identity="bot")
        _log("ack-text: " + acked)
    asked = strip_wake_prefix(msg.text)
    if msg.msg_type in ("image", "file", "media"):
        from ..office.media_understand import understand_media_message

        try:
            media_note = understand_media_message(msg.msg_type, msg.content, msg.message_id)
        except Exception as exc:  # noqa: BLE001
            _log("media-fail: " + str(exc)[:160])
            media_note = ""
        if media_note and not asked:
            result = send_checked(
                msg.chat_id, media_note + "\n\n要我基于这个做什么？", as_identity="bot"
            )
            _log("reply-media: " + result)
            return
        if media_note:
            asked = asked + "\n" + media_note
    if channel == "p2p":
        try:
            if send_style_card(intent, msg.chat_id):
                _log("reply: card")
                return
        except Exception as exc:
            _log("card-fail: " + str(exc)[:160])
    from ..office.artifact_gen import answer_to_doc, looks_like_doc_request

    if looks_like_doc_request(asked):
        result = send_checked(msg.chat_id, answer_to_doc(msg.chat_id), as_identity="bot")
        _log("reply-doc: " + result)
        return
    reply = dispatch(
        intent,
        user_text=asked,
        channel=channel,
        chat_id=msg.chat_id,
    )
    if looks_like_bad_reply(reply):
        _log("bad-reply, retry facts")
        reply = dispatch(
            intent,
            user_text=asked,
            channel=channel,
            chat_id=msg.chat_id,
            force_facts=True,
        )
    if looks_like_bad_reply(reply):
        reply = "刚才写回复抽风了，你再说一次我重试。"
    result = send_checked(msg.chat_id, reply, as_identity="bot")
    sent_body = reply
    if send_ok(result) and looks_like_bad_reply(reply):
        _log("sent-bad-body, correcting")
        fixed = dispatch(
            intent,
            user_text=asked,
            channel=channel,
            chat_id=msg.chat_id,
            force_facts=True,
        )
        if not looks_like_bad_reply(fixed):
            result = send_checked(msg.chat_id, fixed, as_identity="bot")
            sent_body = fixed
    if send_ok(result) and not looks_like_bad_reply(sent_body):
        from ..office.artifact_gen import save_last_answer

        try:
            save_last_answer(msg.chat_id, asked, sent_body)
        except Exception as exc:  # noqa: BLE001
            _log("save-answer-fail: " + str(exc)[:160])
    _log("reply: " + result)


def _handle_line(line: str, seen: set[str]) -> None:
    raw = ANSI_RE.sub("", line).strip()
    if not raw:
        return
    if raw.startswith("[event]") or raw.startswith("[source]"):
        _log(raw)
        return
    if not raw.startswith("{"):
        _log(raw)
        return
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        _log("skip-non-json: " + raw[:200])
        return
    _log("event: " + raw[:500])
    act = extract_card_action(payload)
    if act is not None:
        eid = act.event_id or act.key
        if eid and eid in seen:
            return
        if eid:
            seen.add(eid)
        if act.operator_id and act.operator_id != USER_OPEN_ID:
            _log(f"card skip operator={act.operator_id}")
            return
        if act.act in _FU_ACTS and act.key:
            # 先 PATCH 卡片（用户立刻看到按钮变灰），再发文字确认。
            # 这样能消除 ~2s 的「点了没反应」感。
            if act.act == "fu_done" and act.open_message_id:
                _disable_card_button(act.open_message_id, act.key, "已完成", card=act.card_content)
            reply = apply_action(act.act, act.key)
            result = send_checked(act.chat_id or P2P_CHAT_ID, reply, as_identity="bot")
            _log("followup-card: " + result + " " + reply)
            return
        if act.act == "approve" or (act.act == "done" and act.task_id):
            _handle_approval_card(act, approved=True)
            return
        if act.act == "decline":
            _handle_approval_card(act, approved=False)
            return
        if act.act != "done" or not act.key:
            _log("card skip value")
            return
        # 先 PATCH 卡片（用户立刻看到按钮变灰），再发文字确认。
        if act.open_message_id:
            _disable_card_button(act.open_message_id, act.key, "已处理", card=act.card_content)
        reply = confirm_card(act.key)
        result = send_checked(act.chat_id or P2P_CHAT_ID, reply, as_identity="bot")
        _log("card: " + result + " " + reply)
        return
    msg = extract_inbound_message(payload)
    if msg is None:
        _log("extract: none")
        return
    if _maybe_handle_approval_text(msg):
        return
    if _maybe_claim_confirmation(msg):
        return
    if _maybe_append_intent_patch(msg):
        return
    if _maybe_run_message_triggers(msg):
        # Message was consumed by a keyword trigger. Still ingest context but
        # avoid duplicate dispatching.
        if msg.sender_type in {"", "user"}:
            chat_context.ingest_inbound_message(msg)
        return
    if msg.sender_type in {"", "user"}:
        chat_context.ingest_inbound_message(msg)
    if msg.message_id and msg.message_id in seen:
        return
    if msg.message_id:
        seen.add(msg.message_id)
        if len(seen) > 500:
            seen.clear()
    created = ingest(msg, user_open_id=USER_OPEN_ID, bot_open_id=BOT_OPEN_ID)
    if created:
        _log(f"followup kind={created.get('kind')} id={created.get('id')}")
    if should_reply(msg, BOT_OPEN_ID):
        reply_user(msg)
        return
    decision = consider(msg, user_open_id=USER_OPEN_ID, bot_open_id=BOT_OPEN_ID)
    if decision is None:
        _log(
            f"skip chat={msg.chat_id} type={msg.chat_type} sender={msg.sender_type}"
        )
        return
    wrote = append_item(decision.item)
    _log(
        f"watch reason={decision.reason} notify={decision.notify} wrote={wrote} "
        f"text={msg.text[:80]!r}"
    )
    if decision.notify and wrote:
        result = send_checked(P2P_CHAT_ID, format_watch_push(decision.item), as_identity="bot")
        _log("watch-push: " + result)


_APPROVE_KEYWORDS = frozenset({
    "确认写入", "确认", "同意", "允许", "approve", "ok", "好", "可以",
})
_DECLINE_KEYWORDS = frozenset({
    "取消写入", "取消", "拒绝", "decline", "不要", "算了",
})


def _is_approval_reply(text: str) -> str | None:
    """Return 'approve'/'decline' if the text is a standalone approval reply."""
    blob = (text or "").strip().lower()
    # Treat punctuation/whitespace only as not an approval reply.
    if not blob or len(blob) > 40:
        return None
    # Strip trailing punctuation commonly used in IM replies.
    stripped = re.sub(r"[。！？.!?]+$", "", blob)
    if stripped in {k.lower() for k in _APPROVE_KEYWORDS}:
        return "approve"
    if stripped in {k.lower() for k in _DECLINE_KEYWORDS}:
        return "decline"
    return None


def _maybe_handle_approval_text(msg: InboundMessage) -> bool:
    """Handle text replies that confirm/decline a pending approval card.

    Returns True when the message was consumed as an approval reply.
    """
    if msg.sender_type != "user":
        return False
    kind = _is_approval_reply(msg.text)
    if kind is None:
        return False
    approvals = list_pending_approvals_for_chat(msg.chat_id)
    if not approvals:
        return False
    approval = approvals[0]
    act = CardAction(
        chat_id=msg.chat_id,
        operator_id=msg.sender_id,
        event_id=msg.message_id,
        act=kind,
        key="",
        token=str(approval.get("token") or ""),
        task_id=str(approval.get("task_id") or ""),
        message_id=str(approval.get("message_id") or ""),
    )
    _handle_approval_card(act, approved=(kind == "approve"))
    _log(f"approval-text: {kind} by {msg.sender_id} for {approval.get('message_id')}")
    return True


def _is_claim_confirmation(text: str) -> bool:
    blob = (text or "").strip().lower()
    if not blob or len(blob) > 20:
        return False
    blob = re.sub(r"[。！？.!?]+$", "", blob)
    return blob in {p.lower() for p in _CLAIM_PHRASES}


def _maybe_claim_confirmation(msg: InboundMessage) -> bool:
    """Let another user take over a blocked approval by saying '我来确认'."""
    if msg.sender_type != "user":
        return False
    if not _is_claim_confirmation(msg.text):
        return False
    task = active_agent_task(msg.chat_id)
    if not task:
        send_checked(
            msg.chat_id,
            "当前没有运行中的任务可以接管。",
            as_identity="bot",
        )
        return True
    status = str(task.get("status") or "")
    if status != "blocked":
        send_checked(
            msg.chat_id,
            "当前任务不在确认闸上，无需接管。",
            as_identity="bot",
        )
        return True
    task_id = str(task.get("id") or "").strip()
    body = resume_agent_task_after_claim(task_id, msg.sender_id)
    send_checked(msg.chat_id, body, as_identity="bot")
    _log(f"confirmation-claimed: {task_id} by {msg.sender_id}")
    return True


def _patch_action_from_text(text: str) -> str:
    lowered = (text or "").lower()
    if any(c in lowered for c in _OVERRIDE_CUES):
        return "override"
    if "取消" in lowered or "算了" in lowered or "不用做" in lowered or "不要" in lowered:
        return "cancel"
    return "append"


def _maybe_append_intent_patch(msg: InboundMessage) -> bool:
    """Append a group-chat message as an intent patch to a running agent task.

    Returns True if the message was consumed as a patch.
    """
    if msg.chat_type != "group":
        return False
    if msg.sender_type in {"app", "bot"}:
        return False
    text = strip_wake_prefix(msg.text).strip()
    if not text or len(text) < 2:
        return False
    # Direct @bot messages bypass cue detection and are always treated as patches
    # unless they are known control intents.
    is_direct = should_reply(msg, BOT_OPEN_ID)
    if not is_direct and not (
        any(c in text for c in _APPEND_CUES)
        or any(c in text for c in _OVERRIDE_CUES)
        or "取消" in text
        or "算了" in text
        or "不要" in text
        or "不用做" in text
    ):
        return False
    intent = parse_intent(msg.text)
    if intent.action in _CONTROL_INTENTS:
        return False
    task = active_agent_task(msg.chat_id)
    if not task:
        return False
    status = str(task.get("status") or "")
    if status in {"done", "failed", "cancelled"}:
        return False
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        return False
    action = _patch_action_from_text(text)
    patch = {
        "author_open_id": msg.sender_id or msg.sender_name,
        "sender_name": msg.sender_name,
        "text": text,
        "action": action,
        "ts": datetime.now(CN_TZ).isoformat(timespec="seconds"),
    }
    updated = append_intent_patch(task_id, patch)
    if not updated:
        return False
    goal = str(updated.get("goal") or "").strip() or "当前任务"
    ack = f"已把这条补充追加到任务「{goal}」。可说「任务进度」查看。"
    if action == "override":
        ack = f"已把这条修正追加到任务「{goal}」。Agent 下次运行时会按新意图处理。"
    if action == "cancel":
        ack = f"已收到取消意图，任务「{goal}」将被终止。"
    send_checked(msg.chat_id, ack, as_identity="bot")
    _log(f"intent-patch: {action} by {msg.sender_id} for {task_id}")
    if _has_patch_conflict(updated) and not updated.get("conflict_notified"):
        updated["conflict_notified"] = True
        from ..runtime.agent.service import save_task

        save_task(updated)
        _send_clarify_card(msg.chat_id, updated)
    return True


def _has_patch_conflict(task: dict[str, Any]) -> bool:
    """True if unmerged patches contain both cancel and append/override."""
    patches = [
        p
        for p in (task.get("intent_patches") or [])
        if isinstance(p, dict) and not p.get("merged")
    ]
    if not patches:
        return False
    has_cancel = any(str(p.get("action") or "") == "cancel" for p in patches)
    has_continue = any(
        str(p.get("action") or "") in {"append", "override"} for p in patches
    )
    return has_cancel and has_continue


def _send_clarify_card(chat_id: str, task: dict[str, Any]) -> None:
    """Send an informational card when conflicting patches appear."""
    patches = [
        p
        for p in (task.get("intent_patches") or [])
        if isinstance(p, dict) and not p.get("merged")
    ]
    lines = ["当前任务收到互相矛盾的指令："]
    for p in patches[-6:]:
        author = str(p.get("sender_name") or p.get("author_open_id") or "某人")
        action = str(p.get("action") or "append")
        text = str(p.get("text") or "")[:60]
        lines.append(f"- {author} [{action}]：{text}")
    lines.append("")
    lines.append("请统一意见后回复：继续任务 / 取消任务 / 追加要求。")
    send_checked(chat_id, "\n".join(lines), as_identity="bot")


def _handle_approval_card(act: Any, *, approved: bool) -> None:
    """Process approve/decline card callbacks for Agent write operations."""
    from ..core.run_store import approve_approval, decline_approval
    from ..office.approval_card import approval_result_card
    from ..actions import send_card

    message_id = (act.message_id or "").strip()
    token = (act.token or "").strip()
    if not message_id:
        _log("approval-card: missing message_id")
        return
    operator_id = (act.operator_id or "").strip()
    if approved:
        resolved = approve_approval(
            message_id, operator_id=operator_id, token=token
        )
        if not resolved:
            _log(f"approval-card: approve failed or already resolved mid={message_id}")
            send_checked(
                act.chat_id or P2P_CHAT_ID,
                "这条确认已经处理过了，无法重复确认。",
                as_identity="bot",
            )
            return
        result = confirm_agent_writes_by_message(message_id)
        tool_name = str(resolved.get("tool") or "")
        if result.get("ok") and result.get("task"):
            reply_task = _run_resumed_task(result["task"])
            detail = reply_task or f"工具 `{tool_name}` 已执行。"
        else:
            detail = result.get("error") or "确认后任务未能继续。"
        card = approval_result_card(approved=True, tool=tool_name, detail=detail)
        card_result = send_card(act.chat_id or P2P_CHAT_ID, card, as_identity="bot")
        _log("approval-card approved: " + card_result)
        return
    resolved = decline_approval(
        message_id, operator_id=operator_id, token=token
    )
    if not resolved:
        _log(f"approval-card: decline failed or already resolved mid={message_id}")
        send_checked(
            act.chat_id or P2P_CHAT_ID,
            "这条确认已经处理过了。",
            as_identity="bot",
        )
        return
    result = decline_agent_writes_by_message(message_id)
    tool_name = str(resolved.get("tool") or "")
    detail = result.get("error") or "已取消写入。"
    card = approval_result_card(approved=False, tool=tool_name, detail=detail)
    card_result = send_card(act.chat_id or P2P_CHAT_ID, card, as_identity="bot")
    _log("approval-card declined: " + card_result)


def _run_resumed_task(task: dict[str, Any]) -> str:
    """Run a task that was just unblocked by an approval callback.

    Returns the human-readable result summary.
    """
    from ..runtime.agent.service import _run_task

    try:
        return str(_run_task(task) or "").strip()
    except Exception as exc:  # noqa: BLE001
        return f"确认后继续执行失败：{exc}"


def _maybe_push_brief() -> None:
    now = datetime.now(CN_TZ)
    if now.hour != 9:
        return
    if not already_pushed(now):
        _log("09:00 简报：" + push_brief(now=now).split("\n", 1)[0])
    extra = push_digest(now=now)
    if extra and extra not in {"今日待跟进已推过。", "周末不推待跟进。"}:
        _log("09:00 待跟进：" + extra.split("\n", 1)[0])


def _maybe_scan_bitable() -> None:
    global _last_bitable_scan
    now = time.monotonic()
    if not should_scan_bitable(_last_bitable_scan, now):
        return
    _last_bitable_scan = now
    from ..office.bitable import load_config, scan_bitable

    if not (load_config().get("scan_tables") or os.environ.get("FEISHU_PARTNER_SCAN_TABLES")):
        return
    try:
        note = scan_bitable()
    except Exception as exc:  # noqa: BLE001
        # 周期扫描异常（lark-cli 超时、网络抖动）绝不能打死 serve 主循环。
        _log("bitable-scan fail: " + str(exc)[:160])
        return
    if note:
        _log("bitable-scan: " + note.split("\n", 1)[0])


def _maybe_sync_user_chats() -> None:
    global _last_chat_sync
    now = datetime.now(CN_TZ)
    if not in_chat_watch_window(now):
        return
    now_mono = time.monotonic()
    if not should_sync_user_chats(_last_chat_sync, now_mono):
        return
    _last_chat_sync = now_mono
    since = load_chat_sync_since(now) - timedelta(seconds=5)
    try:
        created = sync_user_chats(start=since, now=now, limit_chats=8, page_size=15)
        mark_chat_synced(now)
    except Exception as exc:
        _log("user-chat-sync fail: " + str(exc)[:160])
        return
    for item in created:
        if str(item.get("kind") or "") not in {"direct", "assign_b", "self_transfer"}:
            continue
        card = assign_push_card(item)
        if card:
            result = send_card(P2P_CHAT_ID, card, as_identity="bot")
            _log("assign-push-card: " + result + " " + str(item.get("id") or ""))
        else:
            result = send_checked(
                P2P_CHAT_ID, format_assign_push(item), as_identity="bot"
            )
            _log("assign-push-text: " + result + " " + str(item.get("id") or ""))


def _maybe_decay_chat_context() -> None:
    """Drop old ambient chat context once per hour."""
    global _last_chat_context_decay
    now_mono = time.monotonic()
    if now_mono - _last_chat_context_decay < _CHAT_CONTEXT_DECAY_INTERVAL_SEC:
        return
    _last_chat_context_decay = now_mono
    try:
        removed = chat_context.decay()
        if removed:
            _log(f"chat-context-decay: removed {removed} old signals")
    except Exception as exc:  # noqa: BLE001
        _log("chat-context-decay fail: " + str(exc)[:160])


def _maybe_expire_approvals() -> None:
    """Cancel approval requests that have timed out and notify the originating chat."""
    global _last_approval_expire_check
    now_mono = time.monotonic()
    if now_mono - _last_approval_expire_check < _APPROVAL_EXPIRE_INTERVAL_SEC:
        return
    _last_approval_expire_check = now_mono
    try:
        expired = expire_stale_approvals()
    except Exception as exc:
        _log("approval-expire fail: " + str(exc)[:160])
        return
    if not expired:
        return
    for row in expired:
        message_id = str(row.get("message_id") or "").strip()
        task_id = str(row.get("task_id") or "").strip()
        tool = str(row.get("tool") or "").strip()
        if not message_id or not task_id:
            continue
        try:
            from ..runtime.agent.service import decline_agent_writes_by_message

            decline_agent_writes_by_message(message_id)
        except Exception as exc:
            _log(f"approval-expire cancel {message_id}: " + str(exc)[:160])
        payload = load_run(task_id) or {}
        chat_id = str(payload.get("chat_id") or P2P_CHAT_ID).strip() or P2P_CHAT_ID
        goal = str(payload.get("goal") or "").strip()
        try:
            card = approval_expired_card(tool=tool, goal=goal)
            result = send_card(chat_id, card, as_identity="bot")
            _log("approval-expired-card: " + result + " " + message_id)
        except Exception as exc:
            _log("approval-expired-card fail: " + str(exc)[:160])


def _maybe_run_triggers() -> None:
    """Fire due scheduled triggers and enqueue background Agent tasks."""
    global _last_trigger_poll
    now_mono = time.monotonic()
    if now_mono - _last_trigger_poll < _TRIGGER_POLL_INTERVAL_SEC:
        return
    _last_trigger_poll = now_mono
    try:
        from ..core.triggers import poll_due_triggers, run_trigger

        due = poll_due_triggers()
    except Exception as exc:
        _log("trigger-poll fail: " + str(exc)[:160])
        return
    for spec in due:
        trigger_id = str(spec.get("id") or "").strip()
        goal = str(spec.get("goal") or "").strip()
        chat_id = (
            str(spec.get("chat_id") or "").strip() or P2P_CHAT_ID
        )
        title = str(spec.get("title") or goal[:30] or "触发器").strip()
        eid = f"{trigger_id}:{spec.get('next_run_at') or ''}"
        try:
            task, message = run_trigger(
                spec,
                source="schedule",
                external_id=eid,
                matched_condition={"next_run_at": spec.get("next_run_at")},
            )
        except Exception as exc:
            _log(f"trigger-run fail {trigger_id}: " + str(exc)[:160])
            continue
        notify = f"⏰ 触发器「{title}」已启动：{goal}"
        if not task:
            _log(f"trigger-run skip {trigger_id}: {message}")
            continue
        try:
            result = send_checked(chat_id, notify, as_identity="bot")
            _log("trigger-notify: " + result + " " + trigger_id)
        except Exception as exc:
            _log(f"trigger-notify fail {trigger_id}: " + str(exc)[:160])


def _maybe_run_message_triggers(msg: InboundMessage) -> bool:
    """Fire message-keyword triggers. Returns True if any fired."""
    from ..core.triggers import (
        list_enabled_message_triggers,
        match_message_trigger,
        run_trigger,
    )

    fired_any = False
    for spec in list_enabled_message_triggers():
        matched = match_message_trigger(spec, msg)
        if not matched:
            continue
        eid = f"msg:{msg.message_id or ''}:{spec.get('id')}"
        try:
            task, _ = run_trigger(
                spec,
                source="message",
                external_id=eid,
                matched_condition=matched,
            )
            if task:
                fired_any = True
                _log(
                    f"trigger-message: {spec.get('id')} matched {matched.get('keywords')}"
                )
        except Exception as exc:
            _log(f"trigger-message fail {spec.get('id')}: " + str(exc)[:160])
    return fired_any


def _spawn_consume(
    binary: Path,
    event_key: str,
    timeout: str | None,
    max_events: int,
) -> tuple[subprocess.Popen, int]:
    cmd = [str(binary), "event", "consume", event_key, "--as", "bot"]
    if timeout:
        cmd.extend(["--timeout", timeout])
    if max_events:
        cmd.extend(["--max-events", str(max_events)])
    _log("工作伙伴收事件 " + " ".join(cmd))
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        cmd,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
    )
    os.close(slave)
    return proc, master


def serve(timeout: str | None = None, max_events: int = 0) -> int:
    reload_identity()
    if not identity_ready():
        print(identity_hint(), file=sys.stderr)
        return 2
    # Import-time copies may be stale after setup; rebind from ids module.
    from ..core import ids as _ids

    global USER_OPEN_ID, BOT_OPEN_ID, P2P_CHAT_ID
    USER_OPEN_ID = _ids.USER_OPEN_ID
    BOT_OPEN_ID = _ids.BOT_OPEN_ID
    P2P_CHAT_ID = _ids.P2P_CHAT_ID
    binary = find_lark_cli()
    msg_proc, msg_fd = _spawn_consume(
        binary, "im.message.receive_v1", timeout, max_events
    )
    card_proc, card_fd = _spawn_consume(
        binary, "card.action.trigger", timeout, max_events
    )
    seen: set[str] = set()
    bufs = {msg_fd: "", card_fd: ""}
    card_warned = False
    try:
        while True:
            try:
                # 周期任务任何异常都只记日志，绝不打断事件主循环
                # （2026-09-03 bitable 扫描超时曾把 serve 打死，卡片回调全丢）。
                _maybe_push_brief()
                _maybe_scan_bitable()
                _maybe_sync_user_chats()
                _maybe_decay_chat_context()
                _maybe_expire_approvals()
                _maybe_run_triggers()
                ensure_worker()
            except Exception as exc:  # noqa: BLE001
                _log("periodic fail: " + str(exc)[:200])
            live: list[int] = []
            if msg_proc.poll() is None:
                live.append(msg_fd)
            if card_proc.poll() is None:
                live.append(card_fd)
            elif not card_warned:
                _log("卡片事件流已停；文字销账仍可用。")
                card_warned = True
            if not live:
                break
            ready, _, _ = select.select(live, [], [], 1.0)
            for fd in ready:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    continue
                if not chunk:
                    continue
                bufs[fd] += chunk.decode("utf-8", "replace")
                while "\n" in bufs[fd]:
                    line, bufs[fd] = bufs[fd].split("\n", 1)
                    _handle_line(line, seen)
            if msg_proc.poll() is not None:
                break
        for fd, leftover in bufs.items():
            if leftover.strip():
                _handle_line(leftover, seen)
    except KeyboardInterrupt:
        _log("已停止。")
    finally:
        for fd in (msg_fd, card_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        for proc in (msg_proc, card_proc):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
    code = msg_proc.poll()
    return 0 if code is None else code
