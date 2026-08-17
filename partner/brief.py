"""Daily 09:00 brief: yesterday workday + today's 3–5 priorities. No padding."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .formatters import _items, _when
from .ids import P2P_CHAT_ID, USER_OPEN_ID, USER_NAMES
from .inbox import recent_items
from .lark import run_lark
from .llm import accept_polished_brief, polish_brief
from .resolved import is_resolved, pending_key, pending_line, save_pending

CN_TZ = timezone(timedelta(hours=8))
STAMP = Path.home() / ".feishu-partner" / "brief-sent.on"
_BLOCK = ("请", "同步", "确认", "帮忙", "对齐", "卡", "阻塞", "评审", "提测", "回复")


def last_workday(now: datetime) -> date:
    day = (now.astimezone(CN_TZ) if now.tzinfo else now.replace(tzinfo=CN_TZ)).date()
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=CN_TZ)
    return start, start.replace(hour=23, minute=59, second=59)


def parse_msg_time(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).replace(tzinfo=CN_TZ)
        except ValueError:
            continue
    if "T" in text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CN_TZ)
        return parsed.astimezone(CN_TZ)
    return None


def _chat_messages_since(chat_id: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    if not chat_id:
        return []
    payload = run_lark(
        [
            "im",
            "+chat-messages-list",
            "--chat-id",
            chat_id,
            "--start",
            start.isoformat(),
            "--end",
            end.isoformat(),
            "--order",
            "asc",
            "--page-size",
            "20",
            "--no-reactions",
        ],
        as_identity="user",
    )
    if payload.get("ok") is False:
        return []
    hits = payload.get("data")
    if isinstance(hits, dict):
        hits = hits.get("messages") or hits.get("items") or []
    if not isinstance(hits, list):
        return []
    return [item for item in hits if isinstance(item, dict)]


_QUESTION = ("?", "？", "还是", "哪个", "哪条", "哪边", "什么意思", "是不是", "对吗", "对么")
_DONE = ("已同步", "已处理", "已改", "已发", "已回", "已合并", "搞定", "做完", "提交了", "合并了")
_ACK = ("好的", "收到", "嗯", "行", "ok", "OK", "没问题", "可以")


def _looks_like_question(text: str) -> bool:
    blob = (text or "").strip()
    if not blob:
        return False
    if blob in {"?", "？"}:
        return True
    return any(mark in blob for mark in _QUESTION) and not any(done in blob for done in _DONE)


def reply_status(ask: str, replies: list[str]) -> str:
    """none / clarifying / answered. Asking back is not an answer."""
    texts = [str(item).strip() for item in replies if str(item).strip()]
    if not texts:
        return "none"
    if any(any(done in item for done in _DONE) for item in texts):
        return "answered"
    ask_is_question = any(mark in (ask or "") for mark in ("?", "？", "吗", "么", "哪", "是否"))
    if ask_is_question:
        if any(not _looks_like_question(item) for item in texts):
            return "answered"
        return "clarifying"
    return "clarifying"


def user_reply_texts(
    messages: list[dict[str, Any]],
    *,
    after: datetime,
    user_id: str,
    skip_id: str = "",
) -> list[str]:
    out: list[str] = []
    for msg in messages:
        mid = str(msg.get("message_id") or "")
        if skip_id and mid == skip_id:
            continue
        sender = msg.get("sender")
        sid = sender.get("id") if isinstance(sender, dict) else msg.get("sender_id")
        if str(sid or "") != user_id:
            continue
        when = parse_msg_time(msg.get("create_time") or msg.get("ts"))
        if when is None or when < after:
            continue
        content = msg.get("content")
        text = content.get("text") if isinstance(content, dict) else str(content or "")
        text = (text or "").replace("\n", " ").strip()
        if text:
            out.append(text)
    return out


def user_spoke_after(
    messages: list[dict[str, Any]],
    *,
    after: datetime,
    user_id: str,
    skip_id: str = "",
) -> bool:
    for msg in messages:
        mid = str(msg.get("message_id") or "")
        if skip_id and mid == skip_id:
            continue
        sender = msg.get("sender")
        sid = sender.get("id") if isinstance(sender, dict) else msg.get("sender_id")
        if str(sid or "") != user_id:
            continue
        when = parse_msg_time(msg.get("create_time") or msg.get("ts"))
        if when is not None and when >= after:
            return True
    return False


def clip_line(text: str, limit: int = 72) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def approval_priority_lines(payload: dict[str, Any]) -> list[str]:
    if payload.get("ok") is False:
        return []
    out: list[str] = []
    for item in _items(payload, "tasks", "items", "list"):
        if not isinstance(item, dict):
            continue
        title = str(
            item.get("title")
            or item.get("approval_name")
            or item.get("definition_name")
            or ""
        ).strip()
        if not title:
            continue
        out.append(f"审批 {title}")
        if len(out) >= 5:
            break
    return out


def _bare_status(line: str) -> str:
    for mark in ("（进行中", "（未完成", "（已结束"):
        if mark in line:
            return line.split(mark, 1)[0]
    return line


def dedupe_priorities(unreplied: list[str], priorities: list[str]) -> list[str]:
    bare = {_bare_status(item) for item in unreplied}
    out: list[str] = []
    for item in priorities:
        if item.startswith("回："):
            rest = _bare_status(item[2:])
            if rest in bare or item[2:] in unreplied:
                where = rest.split("：", 1)[0]
                follow = "进行中" if "进行中" in item or any("进行中" in row for row in unreplied if rest in row) else "未完成"
                out.append(f"回 {where} 那条（{follow}）")
                continue
        out.append(item)
    return out


def long_term_task_lines(
    payload: dict[str, Any],
    *,
    today: date,
    skip_titles: list[str],
    days: int = 30,
) -> list[str]:
    if payload.get("ok") is False:
        return []
    out: list[str] = []
    skip = " ".join(skip_titles)
    for item in _items(payload, "items", "tasks"):
        if not isinstance(item, dict):
            continue
        title = str(item.get("summary") or item.get("title") or "").strip()
        if not title or title in skip or any(title in row for row in skip_titles):
            continue
        created = parse_msg_time(item.get("created_at") or item.get("create_time"))
        if created is None:
            continue
        age = (today - created.date()).days
        if age < days:
            continue
        out.append(f"{title}（未完成）")
        if len(out) >= 3:
            break
    return out


def pick_priorities(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = [
        item
        for item in candidates
        if item.get("title") and (item.get("blocks") or item.get("urgent"))
    ]
    ranked.sort(key=lambda item: (bool(item.get("blocks")), bool(item.get("urgent"))), reverse=True)
    return ranked[:5]


def format_daily_brief(
    *,
    today: date,
    workday: date,
    progressed: list[str],
    unreplied: list[str],
    priorities: list[str],
    week_notes: list[str],
    today_agenda: list[str] | None = None,
    long_term: list[str] | None = None,
) -> str:
    lines = [f"吴梦晨 · {today.month}月{today.day}日简报"]
    if progressed or unreplied:
        lines += ["", f"【昨天小结】上一个工作日 {workday.month}/{workday.day}"]
        if progressed:
            lines.append("完成/推进")
            lines.extend(f"- {item}" for item in progressed)
        if unreplied:
            lines.append("待处理")
            lines.extend(f"- {item}" for item in unreplied)
        if long_term:
            lines.append("长期待办")
            lines.extend(f"- {item}" for item in long_term)
    elif long_term:
        lines += ["", f"【昨天小结】上一个工作日 {workday.month}/{workday.day}"]
        lines.append("长期待办")
        lines.extend(f"- {item}" for item in long_term)
    if priorities or today_agenda:
        lines += ["", "【今天规划】"]
        if today_agenda:
            lines.append("今日日程")
            lines.extend(f"- {item}" for item in today_agenda)
        if priorities:
            lines.append("优先")
            for index, item in enumerate(priorities, 1):
                lines.append(f"{index}. {item}")
    elif week_notes:
        lines += ["", "【本周值得关注】"]
        lines.extend(f"- {item}" for item in week_notes)
    if len(lines) == 1:
        lines += ["", "没有必须立刻排的事。"]
    return "\n".join(lines)


def _agenda_lines(payload: dict[str, Any]) -> list[str]:
    if payload.get("ok") is False:
        return []
    out: list[str] = []
    for item in _items(payload, "events", "items", "calendar_events"):
        if not isinstance(item, dict):
            continue
        title = str(item.get("summary") or item.get("title") or "").strip()
        if not title:
            continue
        when = _when(item.get("start_time") or item.get("start"))
        out.append(f"{title}" + (f"（{when}）" if when else ""))
    return out


def _task_due(item: dict[str, Any]) -> date | None:
    raw = item.get("due_at") or item.get("due") or ""
    text = str(raw)
    if len(text) >= 10 and text[4] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def _about_user(hit: dict[str, Any]) -> bool:
    mentions = hit.get("mentions") or []
    for mention in mentions:
        if not isinstance(mention, dict):
            continue
        mid = mention.get("id")
        oid = mid.get("open_id") if isinstance(mid, dict) else mid
        if str(oid or "") == USER_OPEN_ID:
            return True
        if str(mention.get("name") or "") in USER_NAMES:
            return True
    text = hit.get("content")
    blob = text.get("text") if isinstance(text, dict) else str(text or "")
    return any(name and name in blob for name in USER_NAMES)


def _collect(now: datetime) -> dict[str, Any]:
    workday = last_workday(now)
    today = now.astimezone(CN_TZ).date()
    w_start, w_end = _bounds(workday)
    t_start, t_end = _bounds(today)
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_end = week_start + timedelta(days=7) - timedelta(seconds=1)

    y_agenda = run_lark(
        ["calendar", "+agenda", "--start", w_start.isoformat(), "--end", w_end.isoformat()],
        as_identity="user",
    )
    t_agenda = run_lark(
        ["calendar", "+agenda", "--start", t_start.isoformat(), "--end", t_end.isoformat()],
        as_identity="user",
    )
    w_agenda = run_lark(
        ["calendar", "+agenda", "--start", week_start.isoformat(), "--end", week_end.isoformat()],
        as_identity="user",
    )
    tasks = run_lark(
        ["task", "+get-my-tasks", "--complete=false", "--page-limit", "20"],
        as_identity="user",
    )
    minutes = run_lark(
        [
            "minutes",
            "+search",
            "--participant-ids",
            "me",
            "--start",
            workday.isoformat(),
            "--end",
            workday.isoformat(),
            "--page-size",
            "8",
        ],
        as_identity="user",
    )
    mail = run_lark(
        [
            "mail",
            "user_mailbox.messages",
            "list",
            "--user-mailbox-id",
            "me",
            "--page-size",
            "10",
            "--only-unread",
        ],
        as_identity="user",
    )
    approvals = run_lark(
        ["approval", "tasks", "query", "--topic", "1", "--page-size", "8"],
        as_identity="user",
    )
    mentions = run_lark(
        [
            "im",
            "+messages-search",
            "--at-chatter-ids",
            USER_OPEN_ID,
            "--chat-type",
            "group",
            "--exclude-sender-type",
            "bot",
            "--start",
            w_start.isoformat(),
            "--end",
            w_end.isoformat(),
            "--page-size",
            "20",
            "--no-reactions",
        ],
        as_identity="user",
    )

    progressed = [f"{line}（已结束）" for line in _agenda_lines(y_agenda)]
    if minutes.get("ok") is not False:
        for item in _items(minutes, "minutes", "items", "list"):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("topic") or item.get("name") or "").strip()
            if title:
                progressed.append(f"纪要《{title}》（已结束）")

    followups: dict[str, tuple[datetime, list[dict[str, Any]]]] = {}

    def messages_for(chat_id: str, start: datetime) -> list[dict[str, Any]]:
        prev = followups.get(chat_id)
        if prev is None or prev[0] > start:
            # ponytail: start at the @, not midnight — page-size 20 would miss a busy morning
            followups[chat_id] = (start, _chat_messages_since(chat_id, start, now))
        return followups[chat_id][1]

    def mention_state(item: dict[str, Any], ask: str) -> str:
        chat_id = str(item.get("chat_id") or "")
        when = parse_msg_time(item.get("create_time") or item.get("ts"))
        if not chat_id or when is None:
            return "none"
        replies = user_reply_texts(
            messages_for(chat_id, when),
            after=when,
            user_id=USER_OPEN_ID,
            skip_id=str(item.get("message_id") or ""),
        )
        return reply_status(ask, replies)

    pending: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def _add_pending(item: dict[str, Any]) -> None:
        key = str(item.get("key") or "")
        if not key or key in seen_keys or is_resolved(key):
            return
        seen_keys.add(key)
        pending.append(item)

    if mentions.get("ok") is not False:
        hits = mentions.get("data")
        if isinstance(hits, dict):
            hits = hits.get("messages") or hits.get("items") or []
        if not isinstance(hits, list):
            hits = []
        for hit in hits:
            if not isinstance(hit, dict) or not _about_user(hit):
                continue
            content = hit.get("content")
            raw = content.get("text") if isinstance(content, dict) else str(content or "")
            raw = (raw or "").replace("\n", " ").strip()
            state = mention_state(hit, raw)
            if state == "answered":
                continue
            text = clip_line(raw)
            if not text:
                continue
            tag = "进行中·已追问未答完" if state == "clarifying" else "未完成·未回复"
            _add_pending(
                {
                    "key": pending_key(
                        message_id=str(hit.get("message_id") or ""),
                        chat_id=str(hit.get("chat_id") or ""),
                        text=text,
                    ),
                    "chat_id": str(hit.get("chat_id") or ""),
                    "chat_name": str(hit.get("chat_name") or hit.get("chat_id") or "群"),
                    "text": text,
                    "tag": tag,
                    "link": str(hit.get("message_app_link") or "").strip(),
                }
            )
    for item in recent_items(days=4):
        ts = str(item.get("ts") or "")
        if ts[:10] != workday.isoformat():
            continue
        raw = (item.get("text") or "").replace("\n", " ").strip()
        state = mention_state(item, raw)
        if state == "answered":
            continue
        text = clip_line(raw)
        if not text:
            continue
        tag = "进行中·已追问未答完" if state == "clarifying" else "未完成·未回复"
        _add_pending(
            {
                "key": pending_key(
                    message_id=str(item.get("message_id") or ""),
                    chat_id=str(item.get("chat_id") or ""),
                    text=text,
                ),
                "chat_id": str(item.get("chat_id") or ""),
                "chat_name": str(item.get("chat_name") or "群"),
                "text": text,
                "tag": tag,
                "link": "",
            }
        )
    if mail.get("ok") is not False:
        for item in _items(mail, "items", "messages"):
            if not isinstance(item, dict):
                continue
            subj = str(item.get("subject") or item.get("title") or "").strip()
            if subj:
                _add_pending(
                    {
                        "key": pending_key(chat_id="mail", text=subj),
                        "chat_id": "",
                        "chat_name": "邮件",
                        "text": subj,
                        "tag": "未完成·未读",
                        "link": "",
                    }
                )
    save_pending(pending[:8])
    unreplied = [pending_line(item) for item in pending[:8]]

    candidates: list[dict[str, Any]] = []
    if tasks.get("ok") is not False:
        for item in _items(tasks, "items", "tasks"):
            if not isinstance(item, dict):
                continue
            title = str(item.get("summary") or item.get("title") or "").strip()
            if not title:
                continue
            due = _task_due(item)
            urgent = bool(due and due <= today)
            blocks = any(word in title for word in _BLOCK)
            tag = ["未完成"]
            if blocks:
                tag.append("卡人")
            if urgent:
                tag.append("紧急" if due and due < today else "今日截止")
            label = title + f"（{'·'.join(tag)}）"
            candidates.append({"title": label, "blocks": blocks, "urgent": urgent})
    for line in approval_priority_lines(approvals):
        candidates.append({"title": f"{line}（未完成·卡人）", "blocks": True, "urgent": True})
    today_agenda = _agenda_lines(t_agenda)
    for line in unreplied[:4]:
        candidates.append({"title": f"回：{line}", "blocks": True, "urgent": True})

    picked = pick_priorities(candidates)
    priorities = dedupe_priorities(unreplied, [str(item["title"]) for item in picked])
    long_term = long_term_task_lines(tasks, today=today, skip_titles=priorities)

    today_titles = {line.split("（")[0] for line in today_agenda}
    week_notes: list[str] = []
    for line in _agenda_lines(w_agenda):
        head = line.split("（")[0]
        if head in today_titles:
            continue
        if line not in week_notes:
            week_notes.append(line)
        if len(week_notes) >= 3:
            break

    return {
        "progressed": progressed[:8],
        "unreplied": unreplied[:8],
        "priorities": priorities,
        "week_notes": week_notes,
        "today_agenda": today_agenda,
        "long_term": long_term,
        "workday": workday.isoformat(),
        "today": today.isoformat(),
    }


def snapshot_pending(now: datetime | None = None) -> None:
    now = now or datetime.now(CN_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    _collect(now.astimezone(CN_TZ))


def brief_text(now: datetime | None = None) -> str:
    now = now or datetime.now(CN_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    now = now.astimezone(CN_TZ)
    data = _collect(now)
    text = format_daily_brief(
        today=date.fromisoformat(data["today"]),
        workday=date.fromisoformat(data["workday"]),
        progressed=data["progressed"],
        unreplied=data["unreplied"],
        priorities=data["priorities"],
        week_notes=data["week_notes"],
        today_agenda=data.get("today_agenda") or [],
        long_term=data.get("long_term") or [],
    )
    spoken = polish_brief(text)
    if spoken and accept_polished_brief(text, spoken):
        return spoken
    return text


def already_pushed(now: datetime | None = None) -> bool:
    now = now or datetime.now(CN_TZ)
    if not STAMP.exists():
        return False
    return STAMP.read_text(encoding="utf-8").strip() == now.astimezone(CN_TZ).date().isoformat()


def mark_pushed(now: datetime | None = None) -> None:
    now = now or datetime.now(CN_TZ)
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    STAMP.write_text(now.astimezone(CN_TZ).date().isoformat() + "\n", encoding="utf-8")


def push_brief(*, force: bool = False, now: datetime | None = None) -> str:
    from .actions import send_card, send_text
    from .resolved import load_pending, pending_card

    now = now or datetime.now(CN_TZ)
    if already_pushed(now) and not force:
        return "今日简报已推过。"
    text = brief_text(now)
    result = send_text(P2P_CHAT_ID, text, as_identity="bot")
    if result != "已发送。":
        return result + "\n\n" + text
    mark_pushed(now)
    extra = ""
    items = [
        item
        for item in load_pending()
        if item.get("key") and not is_resolved(str(item.get("key") or ""))
    ]
    if items:
        card_res = send_card(P2P_CHAT_ID, pending_card(items), as_identity="bot")
        if card_res != "已发送。":
            extra = "\n（卡片没发出，文字「某群那条已处理」仍可用）"
    return "已推送今日简报。" + extra + "\n\n" + text
