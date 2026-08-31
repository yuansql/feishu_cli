"""Daily 09:00 brief: yesterday workday + today's 3–5 priorities. No padding."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import fcntl
import os
import re
from pathlib import Path
from typing import Any

from ..compose.formatters import _items, _when, plain_im_text
from ..core.ids import P2P_CHAT_ID, USER_OPEN_ID, USER_NAMES
from ..core.inbox import recent_items
from ..core.lark import run_lark
from ..compose.llm import accept_polished_brief, polish_brief
from ..routing.resolved import is_resolved, pending_key, save_pending

CN_TZ = timezone(timedelta(hours=8))
STAMP = Path.home() / ".feishu-partner" / "brief-sent.on"
_BLOCK = ("请", "同步", "确认", "帮忙", "对齐", "卡", "阻塞", "评审", "提测", "回复")
MENTIONS_PAGE = 20  # lark-cli +messages-search hard cap; we probe saturation against it.


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


def _extract_messages(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Normalise a lark-cli list payload into a list of message dicts."""
    if not isinstance(payload, dict):
        return []
    hits = payload.get("data")
    if isinstance(hits, dict):
        hits = hits.get("messages") or hits.get("items") or []
    if not isinstance(hits, list):
        return []
    return [item for item in hits if isinstance(item, dict)]


def _next_page_token(payload: dict[str, Any] | None) -> str:
    """Best-effort pagination token. Empty string ⇒ no more pages (or unknown)."""
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data")
    if isinstance(data, dict):
        token = data.get("page_token") or data.get("next_page_token")
        if isinstance(token, str) and token:
            return token
    return ""


def _fetch_group_mentions(start: datetime, end: datetime) -> tuple[list[dict[str, Any]], bool, bool]:
    """Pull @-mentions in a window. Returns (hits, truncated, fetch_failed).

    ``truncated`` means the result hit the page-size cap (more may exist);
    ``fetch_failed`` means the API errored and the list is definitely partial.
    We auto-paginate only when the API exposes a real page token, so a busy
    day degrades to an honest "可能未采集全" banner instead of silently
    dropping threads.
    """
    def _call(*, token: str = "") -> dict[str, Any]:
        args = [
            "im",
            "+messages-search",
            "--at-chatter-ids",
            USER_OPEN_ID,
            "--chat-type",
            "group",
            "--exclude-sender-type",
            "bot",
            "--start",
            start.isoformat(),
            "--end",
            end.isoformat(),
            "--page-size",
            str(MENTIONS_PAGE),
            "--no-reactions",
        ]
        if token:
            args += ["--page-token", token]
        return run_lark(args, as_identity="user")

    first = _call()
    if first.get("ok") is False:
        return [], False, True
    hits = _extract_messages(first)
    truncated = len(hits) >= MENTIONS_PAGE
    token = _next_page_token(first)
    pages = 0
    while token and pages < 4:
        page = _call(token=token)
        if page.get("ok") is False:
            # Partial success: keep what we have but flag the gap.
            return hits, True, True
        more = _extract_messages(page)
        if not more:
            break
        hits.extend(more)
        truncated = len(more) >= MENTIONS_PAGE
        token = _next_page_token(page)
        pages += 1
    return hits, truncated, False


_QUESTION = ("?", "？", "还是", "哪个", "哪条", "哪边", "什么意思", "是不是", "对吗", "对么")
_DONE = ("已同步", "已处理", "已改", "已发", "已回", "已合并", "搞定", "做完", "提交了", "合并了")
_ACK = ("好的", "收到", "嗯", "行", "ok", "OK", "没问题", "可以")
_SELF_RESOLVED = (
    "不用了",
    "不需要了",
    "没事了",
    "解决了",
    "已解决",
    "已搞定",
    "已确认",
    "就这么定了",
    "先这样",
    "先不用",
    "先不",
    "先停了",
    "暂停",
    "取消",
    "作罢",
    "ignore",
)


def _looks_like_self_resolved(text: str) -> bool:
    """True when the sender has already closed the topic themselves."""
    blob = (text or "").strip()
    if not blob:
        return False
    # Positive: sender says "不用了 / 定了 / 解决了 / 先这样 / 先不用".
    if any(mark in blob for mark in _SELF_RESOLVED):
        return True
    # Positive: short acknowledgment-only messages (e.g., "好的", "ok").
    stripped = re.sub(r"[。！？.!?~～\s]+$", "", blob)
    if stripped in _ACK or stripped.lower() in {"ok", "yes", "yep", "没问题", "可以", "行", "嗯"}:
        return True
    return False


def _is_bot_sender(msg: dict[str, Any]) -> bool:
    sender = msg.get("sender")
    if isinstance(sender, dict):
        if sender.get("type") == "bot":
            return True
        if sender.get("is_bot"):
            return True
    return False


def third_party_ack(
    messages: list[dict[str, Any]],
    *,
    after: datetime,
    exclude_id: str = "",
    participants: set[str] | None = None,
) -> bool:
    """Whether a LICENSED third party gave an ack-like response.

    After the user followed up (state == ``clarifying``), a third party's
    ``ok`` / ``好的`` / ``收到`` settles the thread — but only if that person
    is part of THIS thread (was @'d, or already spoke in it). A random
    passer-by's one-word ``ok`` or a bot auto-reply must NOT close it.

    Pass ``participants`` (open_ids + ``name:<display>`` handles) to enable the
    licensing gate. When omitted, behaviour stays permissive (legacy callers).
    """
    for msg in messages:
        if _is_bot_sender(msg):
            continue
        mid = str(msg.get("message_id") or "")
        if exclude_id and mid == exclude_id:
            continue
        sender = msg.get("sender")
        sid = sender.get("id") if isinstance(sender, dict) else msg.get("sender_id")
        # Skip the user's own messages — handled separately.
        if str(sid or "") == USER_OPEN_ID:
            continue
        when = parse_msg_time(msg.get("create_time") or msg.get("ts"))
        if when is None or when < after:
            continue
        content = msg.get("content")
        text = content.get("text") if isinstance(content, dict) else str(content or "")
        text = (text or "").replace("\n", " ").strip()
        if not text:
            continue
        name = sender.get("name") if isinstance(sender, dict) else msg.get("sender_name")
        # License gate: only participants of this thread may settle it.
        if participants is not None:
            if (
                str(sid or "") not in participants
                and ("name:" + str(name or "")) not in participants
            ):
                continue
        # Ultra-short ack pattern: ok / 好的 / 收到 / 1 / 👍 / etc.
        cleaned = re.sub(r"[。！？.!?~～👍🙌✅\s]+$", "", text)
        if (
            cleaned in _ACK
            or cleaned.lower() in {"ok", "yes", "yep", "1"}
            or text.strip() in {"👍", "🙌", "✅", "+1"}
        ):
            return True
        # Slightly longer but still clearly affirmative (and not a question).
        if any(
            mark in text
            for mark in (
                "没问题",
                "可以",
                "行",
                "知道了",
                "了解",
                "明白",
                "同意",
                "参加",
                "到时",
                "准时",
                "好的",
            )
        ) and not any(q in text for q in ("？", "?", "吗", "么", "哪个", "什么")):
            return True
    return False


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


def _message_app_link(chat_id: str, message_id: str) -> str:
    """Build an applink that opens the chat and jumps to a specific message."""
    if not chat_id:
        return ""
    base = f"https://applink.feishu.cn/client/chat/open?openChatId={chat_id}"
    if message_id:
        base += f"&position={message_id}"
    return base


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
        if item.get("completed_at") or item.get("complete_time"):
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


_LEVEL_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
_RSVP = {
    "accept": "已接受",
    "accepted": "已接受",
    "needs_action": "待回复",
    "decline": "已拒绝",
    "declined": "已拒绝",
    "tentative": "待定",
}
_WD = "一二三四五六日"


def cn_day(day: date, *, paren: bool = True) -> str:
    wd = f"周{_WD[day.weekday()]}"
    if paren:
        return f"{day.month}月{day.day}日 ({wd})"
    return f"{day.month}月{day.day}日 {wd}"


def _infer_level(item: dict[str, Any]) -> str:
    if item.get("level") in _LEVEL_RANK:
        return str(item["level"])
    kind = str(item.get("kind") or "")
    if kind == "meeting":
        return "P0"
    if item.get("blocks") and item.get("urgent"):
        return "P1"
    if item.get("blocks"):
        return "P2"
    if item.get("urgent"):
        return "P2"
    return "P3"


def _infer_reason(item: dict[str, Any]) -> str:
    if item.get("reason"):
        return str(item["reason"])
    kind = str(item.get("kind") or "")
    if kind == "meeting":
        return "固定会议"
    if kind == "approval":
        return "卡审批"
    if kind == "reply":
        return "待回复，卡进度"
    if item.get("blocks"):
        return "卡人"
    if item.get("urgent"):
        return "紧急"
    return "待跟进"


def rank_priorities(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in candidates:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        if item.get("kind") != "meeting" and not (item.get("blocks") or item.get("urgent")):
            continue
        rows.append(
            {
                "title": title,
                "level": _infer_level(item),
                "reason": _infer_reason(item),
                "blocks": bool(item.get("blocks")),
                "urgent": bool(item.get("urgent")),
                "kind": str(item.get("kind") or ""),
                "key": str(item.get("key") or ""),
                "link": str(item.get("link") or "").strip(),
            }
        )
    rows.sort(
        key=lambda row: (
            _LEVEL_RANK.get(str(row["level"]), 9),
            not row["blocks"],
            not row["urgent"],
        )
    )
    return rows[:5]


def work_priorities(
    priorities: list[Any],
    *,
    agenda: list[Any] | None = None,
) -> list[dict[str, Any]]:
    rows = [item for item in priorities if isinstance(item, dict) and item.get("title")]
    if agenda:
        rows = [item for item in rows if item.get("kind") != "meeting"]
    return rows


def pending_brief_line(item: dict[str, Any]) -> str:
    who = str(item.get("sender_name") or "").strip()
    where = str(item.get("chat_name") or "群")
    text = plain_im_text(str(item.get("text") or ""))
    tag = str(item.get("tag") or "").strip()
    link = str(item.get("link") or "").strip()
    head = f"{who}（{where}）" if who else where
    line = f"{head}：{text}" if text else head
    if tag:
        line += f"（{tag}）"
    if link and link not in line:
        # Feishu plain-text fallback cannot render markdown links reliably;
        # keep the raw applink so it is at least tappable/ copyable.
        line += f" 跳转：{link}"
    return line


def _sender_name(hit: dict[str, Any]) -> str:
    sender = hit.get("sender")
    if isinstance(sender, dict):
        return str(sender.get("name") or sender.get("sender_name") or "").strip()
    return str(hit.get("sender_name") or "").strip()


def agenda_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("ok") is False:
        return []
    out: list[dict[str, Any]] = []
    for item in _items(payload, "events", "items", "calendar_events"):
        if not isinstance(item, dict):
            continue
        title = str(item.get("summary") or item.get("title") or "").strip()
        if not title:
            continue
        start = _when(item.get("start_time") or item.get("start"))
        end = _when(item.get("end_time") or item.get("end"))
        org = item.get("event_organizer") or item.get("organizer") or {}
        organizer = ""
        if isinstance(org, dict):
            organizer = str(org.get("display_name") or org.get("name") or "").strip()
        rsvp = _RSVP.get(str(item.get("self_rsvp_status") or "").lower(), "")
        vchat = item.get("vchat") if isinstance(item.get("vchat"), dict) else {}
        out.append(
            {
                "title": title,
                "start": start,
                "end": end,
                "organizer": organizer,
                "rsvp": rsvp,
                "app_link": str(item.get("app_link") or "").strip(),
                "meet_url": str(vchat.get("meeting_url") or "").strip(),
                "event_id": str(item.get("event_id") or "").strip(),
            }
        )
    return out


def format_agenda_today(entry: dict[str, Any]) -> str:
    start = str(entry.get("start") or "")
    end = str(entry.get("end") or "")
    when = f"{start}–{end}" if start and end else start or end
    bits = [f"{when} {entry.get('title') or ''}".strip() if when else str(entry.get("title") or "")]
    if entry.get("organizer"):
        bits.append(str(entry["organizer"]))
    if entry.get("rsvp"):
        bits.append(str(entry["rsvp"]))
    return " · ".join(bit for bit in bits if bit)


def format_agenda_progress(entry: dict[str, Any]) -> str:
    start = str(entry.get("start") or "")
    end = str(entry.get("end") or "")
    when = f"{start}–{end}" if start and end else start
    org = str(entry.get("organizer") or "")
    extra = when + (f"，{org}" if org and when else org)
    title = str(entry.get("title") or "")
    if extra:
        return f"{title}（{extra}）（已结束）"
    return f"{title}（已结束）"


def format_daily_brief(
    *,
    today: date,
    workday: date,
    progressed: list[str],
    unreplied: list[str],
    priorities: list[Any],
    week_notes: list[str],
    today_agenda: list[str] | None = None,
    long_term: list[str] | None = None,
    notes: list[str] | None = None,
) -> str:
    lines = [f"📋 每日工作简报 · {cn_day(today)}"]
    showed_unreplied = bool(unreplied)
    if progressed or showed_unreplied or long_term:
        lines += ["", f"一、昨天小结（{cn_day(workday, paren=False)}）"]
        if progressed:
            lines.append("推进事项")
            lines.extend(f"- {item}" for item in progressed)
        if showed_unreplied:
            lines.append(f"⚠️ 待处理 / 待回复（{len(unreplied)}项）")
            for index, item in enumerate(unreplied, 1):
                lines.append(f"{index}. {item}")
        if long_term:
            lines.append(f"长期待办（{len(long_term)}项）")
            lines.extend(f"- {item}" for item in long_term)
    if priorities or today_agenda:
        lines += ["", f"二、今天规划（{cn_day(today, paren=False)}）"]
        if today_agenda:
            lines.append("今日日程")
            lines.extend(f"- {item}" for item in today_agenda)
        shown = work_priorities(priorities, agenda=today_agenda) if today_agenda else [
            item for item in priorities if isinstance(item, dict)
        ]
        if shown:
            lines.append("优先处理（待办 / 回复，不含已列日程）")
            for item in shown:
                lines.append(f"{item.get('level') or 'P3'}  {item.get('title')}")
                if item.get("reason"):
                    lines.append(f"    {item['reason']}")
        elif any(isinstance(item, str) for item in priorities):
            lines.append("优先处理")
            for index, item in enumerate(priorities, 1):
                if isinstance(item, str):
                    lines.append(f"{index}. {item}")
    elif week_notes:
        lines += ["", "【本周值得关注】"]
        lines.extend(f"- {item}" for item in week_notes)
    if notes:
        lines += ["", ".".join(notes)]
    if len(lines) == 1:
        lines += ["", "没有必须立刻排的事。"]
    return "\n".join(lines)


def completed_task_lines(payload: dict[str, Any], day: date) -> list[str]:
    if payload.get("ok") is False:
        return []
    out: list[str] = []
    for item in _items(payload, "items", "tasks"):
        if not isinstance(item, dict):
            continue
        title = str(item.get("summary") or item.get("title") or "").strip()
        if not title:
            continue
        done = parse_msg_time(
            item.get("completed_at") or item.get("complete_time") or item.get("updated_at")
        )
        if done is None or done.date() != day:
            continue
        out.append(f"{title}（已结束）")
        if len(out) >= 5:
            break
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


def _thread_participants(
    *,
    item: dict[str, Any],
    messages: list[dict[str, Any]],
    after: datetime,
    exclude_id: str,
    user_id: str,
) -> set[str]:
    """Open IDs + name handles licensed to settle this @ thread.

    Licensed = people @'d in the original mention, or people who already
    spoke in the thread window (excluding the user). Stops a random
    passer-by's ``ok`` from closing a thread they were never part of.
    """
    ids: set[str] = set()
    for m in item.get("mentions") or []:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        oid = mid.get("open_id") if isinstance(mid, dict) else mid
        if oid:
            ids.add(str(oid))
        name = m.get("name")
        if name:
            ids.add("name:" + str(name))
    for msg in messages:
        mid = str(msg.get("message_id") or "")
        if exclude_id and mid == exclude_id:
            continue
        sender = msg.get("sender")
        sid = sender.get("id") if isinstance(sender, dict) else msg.get("sender_id")
        when = parse_msg_time(msg.get("create_time") or msg.get("ts"))
        if when is None or when < after:
            continue
        if str(sid or "") == user_id:
            continue
        if sid:
            ids.add(str(sid))
        name = sender.get("name") if isinstance(sender, dict) else msg.get("sender_name")
        if name:
            ids.add("name:" + str(name))
    return ids


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
    mentions, mentions_truncated, mentions_fetch_failed = _fetch_group_mentions(
        w_start, w_end
    )

    progressed = [format_agenda_progress(entry) for entry in agenda_entries(y_agenda)]
    done_tasks = run_lark(
        ["task", "+search", "--completed", "--page-limit", "20"],
        as_identity="user",
    )
    progressed.extend(completed_task_lines(done_tasks, workday))
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
        msgs = messages_for(chat_id, when)
        replies = user_reply_texts(
            msgs,
            after=when,
            user_id=USER_OPEN_ID,
            skip_id=str(item.get("message_id") or ""),
        )
        state = reply_status(ask, replies)
        # If user followed up but we're still "clarifying", check whether a
        # LICENSED third party has since acknowledged (e.g. 吴梦晨 says "ok").
        # Licensed = was @'d in the ask, or already spoke in the thread — so a
        # random passer-by's "ok" can't settle a thread they were never in.
        if state == "clarifying":
            participants = _thread_participants(
                item=item,
                messages=msgs,
                after=when,
                exclude_id=str(item.get("message_id") or ""),
                user_id=USER_OPEN_ID,
            )
            if third_party_ack(
                msgs,
                after=when,
                exclude_id=str(item.get("message_id") or ""),
                participants=participants,
            ):
                state = "answered"
        return state

    pending: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def _add_pending(item: dict[str, Any]) -> None:
        key = str(item.get("key") or "")
        if not key or key in seen_keys or is_resolved(key):
            return
        seen_keys.add(key)
        pending.append(item)

    for hit in mentions:
            if not isinstance(hit, dict) or not _about_user(hit):
                continue
            content = hit.get("content")
            raw = content.get("text") if isinstance(content, dict) else str(content or "")
            raw = (raw or "").replace("\n", " ").strip()
            if _looks_like_self_resolved(raw):
                continue
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
                "sender_name": _sender_name(hit),
                "message_id": str(hit.get("message_id") or ""),
                "text": text,
                "tag": tag,
                "link": str(hit.get("message_app_link") or _message_app_link(
                    str(hit.get("chat_id") or ""), str(hit.get("message_id") or "")
                )).strip(),
                }
            )
    for item in recent_items(days=4):
        ts = str(item.get("ts") or "")
        if ts[:10] != workday.isoformat():
            continue
        raw = (item.get("text") or "").replace("\n", " ").strip()
        if _looks_like_self_resolved(raw):
            continue
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
                "sender_name": _sender_name(item),
                "message_id": str(item.get("message_id") or ""),
                "text": text,
                "tag": tag,
                "link": _message_app_link(
                    str(item.get("chat_id") or ""), str(item.get("message_id") or "")
                ),
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
    unreplied = [pending_brief_line(item) for item in pending[:8]]

    candidates: list[dict[str, Any]] = []
    today_entries = agenda_entries(t_agenda)
    today_agenda = [format_agenda_today(entry) for entry in today_entries]
    for entry in today_entries:
        when = ""
        if entry.get("start") and entry.get("end"):
            when = f"（{entry['start']}–{entry['end']}）"
        elif entry.get("start"):
            when = f"（{entry['start']}）"
        reason = "固定会议"
        if entry.get("organizer"):
            reason += f"，{entry['organizer']}组织"
        if entry.get("rsvp"):
            reason += f"，{entry['rsvp']}"
        candidates.append(
            {
                "title": f"{entry['title']}{when}",
                "blocks": True,
                "urgent": True,
                "kind": "meeting",
                "level": "P0",
                "reason": reason,
            }
        )
    if tasks.get("ok") is not False:
        for item in _items(tasks, "items", "tasks"):
            if not isinstance(item, dict):
                continue
            if item.get("completed_at") or item.get("complete_time"):
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
            candidates.append(
                {
                    "title": label,
                    "blocks": blocks,
                    "urgent": urgent,
                    "kind": "task",
                    "reason": "过期待办" if due and due < today else ("今日截止" if urgent else "待办"),
                }
            )
    for line in approval_priority_lines(approvals):
        candidates.append(
            {
                "title": f"{line}（未完成·卡人）",
                "blocks": True,
                "urgent": True,
                "kind": "approval",
                "level": "P1",
                "reason": "卡审批",
            }
        )
    for item in pending[:4]:
        tag = str(item.get("tag") or "")
        who = str(item.get("sender_name") or "").strip()
        where = str(item.get("chat_name") or "群")
        quote = str(item.get("text") or "")[:90]
        title = f"回复{who}" if who else f"回 {where} 那条"
        if quote:
            title += f"：{quote}"
        candidates.append(
            {
                "title": title,
                "blocks": True,
                "urgent": True,
                "kind": "reply",
                "level": "P1" if "未回复" in tag else "P2",
                "reason": "未回复，卡进度" if "未回复" in tag else "已追问未答完",
                "key": str(item.get("key") or ""),
                "link": str(item.get("link") or "").strip(),
            }
        )

    picked = rank_priorities(candidates)
    titles = dedupe_priorities(unreplied, [str(item["title"]) for item in picked])
    priorities = [
        {**item, "title": title} for item, title in zip(picked, titles)
    ]
    # Ensure the injected reply key/link survives dedup.
    for idx, item in enumerate(priorities):
        if item.get("kind") == "reply" and idx < len(picked):
            if not item.get("key"):
                item["key"] = picked[idx].get("key", "")
            if not item.get("link"):
                item["link"] = picked[idx].get("link", "")
    long_term = long_term_task_lines(
        tasks, today=today, skip_titles=[str(item["title"]) for item in priorities]
    )

    today_titles = {entry["title"] for entry in today_entries}
    week_notes: list[str] = []
    for entry in agenda_entries(w_agenda):
        if entry["title"] in today_titles:
            continue
        line = format_agenda_today(entry)
        if line not in week_notes:
            week_notes.append(line)
        if len(week_notes) >= 3:
            break

    return {
        "progressed": progressed[:8],
        "unreplied": [],
        "priorities": priorities,
        "week_notes": week_notes,
        "today_agenda": today_agenda,
        "today_entries": today_entries,
        "long_term": long_term,
        "pending": pending[:8],
        "workday": workday.isoformat(),
        "today": today.isoformat(),
        "truncated": mentions_truncated,
        "fetch_failed": mentions_fetch_failed,
    }


def followup_items_for_brief(path: Path | None = None) -> list[dict[str, Any]]:
    """Open follow-up items in a card-friendly shape."""
    from . import followup as _followup
    return _followup.followup_items_for_command(path=path)


def snapshot_pending(now: datetime | None = None) -> None:
    now = now or datetime.now(CN_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    _collect(now.astimezone(CN_TZ))


def collect_brief(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(CN_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    return _collect(now.astimezone(CN_TZ))


def _collect_warnings(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if data.get("fetch_failed"):
        out.append("⚠️ 部分 @ 消息采集失败，今天的待处理列表可能不完整")
    elif data.get("truncated"):
        out.append("⚠️ 昨日 @ 消息较多，只采集到部分内容，请手动复核群聊")
    return out


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
        notes=_collect_warnings(data),
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


def claim_daily_stamp(path: Path, now: datetime, *, force: bool = False) -> bool:
    """Flock then write today's date. False if another process already claimed today."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    today = now.astimezone(CN_TZ).date().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        raw = os.read(fd, 64).decode("utf-8").strip()
        if raw == today and not force:
            return False
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, (today + "\n").encode("utf-8"))
        os.fsync(fd)
        return True
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def push_brief(*, force: bool = False, now: datetime | None = None) -> str:
    from ..actions import send_card, send_text
    from .brief_card import brief_card

    now = now or datetime.now(CN_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    now = now.astimezone(CN_TZ)
    if not claim_daily_stamp(STAMP, now, force=force):
        return "今日简报已推过。"
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
        notes=_collect_warnings(data),
    )
    from .followup import followup_items_for_command, followups_for_command

    card_res = send_card(
        P2P_CHAT_ID,
        brief_card(data, followups=followups_for_command(), followup_items=followup_items_for_command()),
        as_identity="bot",
    )
    if card_res == "已发送。":
        return "已推送今日简报。\n\n" + text
    spoken = polish_brief(text)
    fallback = spoken if spoken and accept_polished_brief(text, spoken) else text
    result = send_text(P2P_CHAT_ID, fallback, as_identity="bot")
    if result != "已发送。":
        return card_res + "\n" + result + "\n\n" + fallback
    return "卡片没发出，已改发文字。\n" + card_res + "\n\n" + fallback
