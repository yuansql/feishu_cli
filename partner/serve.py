from __future__ import annotations

import json
import os
import pty
import re
import select
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .actions import dispatch, send_text
from .brief import already_pushed, push_brief
from .events import extract_card_action, extract_inbound_message, should_reply
from .ids import BOT_OPEN_ID, P2P_CHAT_ID, USER_OPEN_ID
from .inbox import append_item
from .intents import parse_intent, strip_wake_prefix
from .lark import find_lark_cli
from .resolved import confirm_card
from .watch import consider, format_watch_push

CN_TZ = timezone(timedelta(hours=8))

LOG_DIR = Path.home() / ".feishu-partner"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _log(line: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "serve.log").open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip() + "\n")
    print(line, file=sys.stderr, flush=True)


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
        if act.act != "done" or not act.key:
            _log("card skip value")
            return
        reply = confirm_card(act.key)
        result = send_text(act.chat_id or P2P_CHAT_ID, reply, as_identity="bot")
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
    if should_reply(msg, BOT_OPEN_ID):
        intent = parse_intent(msg.text)
        _log(f"intent={intent.action} text={msg.text[:80]!r}")
        channel = "group" if msg.chat_type == "group" else "p2p"
        reply = dispatch(
            intent,
            user_text=strip_wake_prefix(msg.text),
            channel=channel,
            chat_id=msg.chat_id,
        )
        result = send_text(msg.chat_id, reply, as_identity="bot")
        _log("reply: " + result)
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
        result = send_text(P2P_CHAT_ID, format_watch_push(decision.item), as_identity="bot")
        _log("watch-push: " + result)


def _maybe_push_brief() -> None:
    now = datetime.now(CN_TZ)
    if now.hour != 9 or already_pushed(now):
        return
    _log("09:00 简报：" + push_brief(now=now).split("\n", 1)[0])


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
