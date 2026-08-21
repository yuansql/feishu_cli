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

from ..core.ack import ACK_EMOJI, ack_line, should_ack_text
from ..actions import add_reaction, dispatch, send_style_card, send_text
from ..office.brief import already_pushed, push_brief
from ..core.events import InboundMessage, extract_card_action, extract_inbound_message, should_reply
from ..core.ids import BOT_OPEN_ID, P2P_CHAT_ID, USER_OPEN_ID, identity_hint, identity_ready, reload_identity
from ..office.followup import (
    apply_action,
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
from ..core.lark import find_lark_cli
from ..compose.llm import (
    _looks_like_leak,
    _looks_like_provider_error,
    _looks_like_transport_error,
)
from ..routing.resolved import confirm_card
from ..runtime.runner import ensure_worker
from ..office.watch import consider, format_watch_push

CN_TZ = timezone(timedelta(hours=8))

LOG_DIR = Path.home() / ".feishu-partner"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_FU_ACTS = frozenset({"fu_done", "fu_snooze", "fu_ignore"})
_last_bitable_scan = 0.0
_last_chat_sync = 0.0


def _log(line: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "serve.log").open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip() + "\n")
    print(line, file=sys.stderr, flush=True)


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
    result = "skip"
    for attempt in range(attempts):
        result = send_text(chat_id, text, as_identity=as_identity)
        if send_ok(result):
            if attempt:
                _log(f"send-retry ok attempt={attempt + 1}")
            return result
        if not looks_like_transient_fail(result):
            return result
        _log(f"send-retry attempt={attempt + 1} {result[:160]}")
    return result


def reply_user(msg: InboundMessage) -> None:
    intent = parse_intent(msg.text)
    _log(f"intent={intent.action} text={msg.text[:80]!r}")
    reacted = add_reaction(msg.message_id, ACK_EMOJI)
    _log("ack-react: " + reacted)
    if should_ack_text(intent.action):
        acked = send_checked(msg.chat_id, ack_line(intent.action), as_identity="bot")
        _log("ack-text: " + acked)
    channel = "group" if msg.chat_type == "group" else "p2p"
    asked = strip_wake_prefix(msg.text)
    if channel == "p2p":
        try:
            if send_style_card(intent, msg.chat_id):
                _log("reply: card")
                return
        except Exception as exc:
            _log("card-fail: " + str(exc)[:160])
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
            reply = apply_action(act.act, act.key)
            result = send_checked(act.chat_id or P2P_CHAT_ID, reply, as_identity="bot")
            _log("followup-card: " + result + " " + reply)
            return
        if act.act != "done" or not act.key:
            _log("card skip value")
            return
        reply = confirm_card(act.key)
        result = send_checked(act.chat_id or P2P_CHAT_ID, reply, as_identity="bot")
        _log("card: " + result + " " + reply)
        return
    msg = extract_inbound_message(payload)
    if msg is None:
        _log("extract: none")
        return
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
    note = scan_bitable()
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
        result = send_checked(
            P2P_CHAT_ID, format_assign_push(item), as_identity="bot"
        )
        _log("assign-push: " + result + " " + str(item.get("id") or ""))


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
            _maybe_push_brief()
            _maybe_scan_bitable()
            _maybe_sync_user_chats()
            ensure_worker()
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
