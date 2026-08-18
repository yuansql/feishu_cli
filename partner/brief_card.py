"""Feishu schema 2.0 card for the daily brief. Text is only a fallback."""

from __future__ import annotations

from datetime import date
from typing import Any

from .brief import cn_day, work_priorities
from .formatters import plain_im_text

_LEVEL_COLOR = {"P0": "red", "P1": "orange", "P2": "yellow", "P3": "green"}
_RSVP_COLOR = {"已接受": "green", "待回复": "orange", "已拒绝": "red", "待定": "yellow"}


def _md(content: str, *, size: str = "") -> dict[str, Any]:
    el: dict[str, Any] = {"tag": "markdown", "content": content}
    if size:
        el["text_size"] = size
    return el


def _hr() -> dict[str, Any]:
    return {"tag": "hr"}


def _chip(text: str, color: str) -> str:
    return f"<font color='{color}'>**{text}**</font>"


def _subtitle(today: date, *, meetings: int, pending: int) -> str:
    bits = [cn_day(today).replace(" (", " · ").rstrip(")")]
    if meetings:
        bits.append(f"{meetings}场会")
    bits.append(f"{pending}条待回复" if pending else "待回复已清")
    return " · ".join(bits)


def _link(title: str, url: str) -> str:
    if url:
        return f"[{title}]({url})"
    return title


def _agenda_line(entry: dict[str, Any]) -> str:
    start = str(entry.get("start") or "")
    end = str(entry.get("end") or "")
    when = f"{start}–{end}" if start and end else start
    title = _link(str(entry.get("title") or ""), str(entry.get("app_link") or ""))
    org = str(entry.get("organizer") or "")
    rsvp = str(entry.get("rsvp") or "")
    bits = [f"**{when}** {title}" if when else title]
    if org:
        bits.append(org)
    line = " · ".join(bit for bit in bits if bit)
    if rsvp:
        line += "  " + _chip(rsvp, _RSVP_COLOR.get(rsvp, "grey"))
    return "- " + line


def _open_btn(label: str, url: str, *, kind: str = "default") -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": kind,
        "size": "small",
        "behaviors": [
            {
                "type": "open_url",
                "default_url": url,
                "pc_url": url,
                "ios_url": url,
                "android_url": url,
            }
        ],
    }


def _agenda_actions(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only RSVP-pending meetings get a jump button. Title itself is already a calendar link."""
    out: list[dict[str, Any]] = []
    for entry in entries:
        title = str(entry.get("title") or "日程")[:8]
        app_link = str(entry.get("app_link") or "")
        if str(entry.get("rsvp") or "") == "待回复" and app_link:
            out.append(_open_btn(f"去回复·{title}", app_link, kind="primary"))
    return out[:4]


def _priority_table(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tag": "table",
        "page_size": 5,
        "row_height": "low",
        "header_style": {
            "text_align": "left",
            "background_style": "grey",
            "text_color": "grey",
            "bold": True,
        },
        "columns": [
            {
                "name": "level",
                "display_name": "优先级",
                "data_type": "options",
                "width": "72px",
            },
            {
                "name": "item",
                "display_name": "事项",
                "data_type": "text",
                "width": "auto",
            },
            {
                "name": "reason",
                "display_name": "原因",
                "data_type": "text",
                "width": "auto",
            },
        ],
        "rows": [
            {
                "level": [
                    {
                        "text": str(row.get("level") or "P3"),
                        "color": _LEVEL_COLOR.get(str(row.get("level") or "P3"), "green"),
                    }
                ],
                "item": str(row.get("title") or ""),
                "reason": str(row.get("reason") or "待跟进"),
            }
            for row in rows
        ],
    }


def _pending_blocks(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items[:5]:
        who = str(item.get("sender_name") or "").strip()
        where = str(item.get("chat_name") or "群")
        text = plain_im_text(str(item.get("text") or ""))[:80]
        tag = str(item.get("tag") or "")
        head = f"**{who}** · {where}" if who else f"**{where}**"
        body = text + (f"  {_chip(tag, 'grey')}" if tag else "")
        out.append(_md(f"{head}\n{body}"))
        buttons: list[dict[str, Any]] = []
        link = str(item.get("link") or "").strip()
        if link:
            buttons.append(_open_btn("去看", link, kind="primary"))
        key = str(item.get("key") or "")
        if key:
            buttons.append(
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "已处理"},
                    "type": "default",
                    "size": "small",
                    "value": {"act": "done", "key": key},
                    "behaviors": [
                        {"type": "callback", "value": {"act": "done", "key": key}}
                    ],
                }
            )
        if buttons:
            out.append({"tag": "action", "actions": buttons})
    return out


def brief_card(data: dict[str, Any]) -> dict[str, Any]:
    today = date.fromisoformat(str(data["today"]))
    workday = date.fromisoformat(str(data["workday"]))
    entries = [item for item in (data.get("today_entries") or []) if isinstance(item, dict)]
    pending = [item for item in (data.get("pending") or []) if isinstance(item, dict)]
    progressed = [str(item) for item in (data.get("progressed") or []) if item]
    unreplied = [str(item) for item in (data.get("unreplied") or []) if item]
    long_term = [str(item) for item in (data.get("long_term") or []) if item]
    week_notes = [str(item) for item in (data.get("week_notes") or []) if item]
    rows = work_priorities(data.get("priorities") or [], agenda=entries or data.get("today_agenda"))

    elements: list[dict[str, Any]] = []
    yesterday: list[str] = [f"**一、昨天小结** · {cn_day(workday, paren=False)}"]
    if progressed:
        yesterday.append("**推进**")
        yesterday.extend(f"- {item}" for item in progressed)
    if unreplied:
        yesterday.append(f"**待回复 · {len(unreplied)}**")
        yesterday.extend(f"{index}. {item}" for index, item in enumerate(unreplied, 1))
    if long_term:
        yesterday.append(f"**长期待办 · {len(long_term)}**")
        yesterday.extend(f"- {item}" for item in long_term)
    if len(yesterday) > 1:
        elements.append(_md("\n".join(yesterday)))

    today_bits: list[str] = [f"**二、今天规划** · {cn_day(today, paren=False)}"]
    if entries:
        today_bits.append("**今日日程**")
        today_bits.extend(_agenda_line(entry) for entry in entries)
    if week_notes and not entries:
        today_bits.append("**本周值得关注**")
        today_bits.extend(f"- {item}" for item in week_notes)
    if len(today_bits) > 1:
        if elements:
            elements.append(_hr())
        elements.append(_md("\n".join(today_bits)))
        agenda_btns = _agenda_actions(entries)
        if agenda_btns:
            elements.append({"tag": "action", "actions": agenda_btns})
    if rows:
        elements.append(_md("**优先处理** · 待办 / 回复"))
        elements.append(_priority_table(rows))
    elif entries:
        elements.append(_md("<font color='grey'>没有卡人的待办，先把今天的会开完。</font>", size="notation"))

    if pending:
        elements.append(_hr())
        elements.append(_md("**待回复** · 点已处理明早不催"))
        elements.extend(_pending_blocks(pending))

    if not elements:
        elements.append(_md("没有必须立刻排的事。"))

    return {
        "schema": "2.0",
        "config": {
            "width_mode": "compact",
            "enable_forward": True,
            "update_multi": True,
            "summary": {"content": f"每日工作简报 · {cn_day(today)}"},
        },
        "header": {
            "title": {"tag": "plain_text", "content": "每日工作简报"},
            "subtitle": {
                "tag": "plain_text",
                "content": _subtitle(today, meetings=len(entries), pending=len(unreplied)),
            },
            "template": "indigo",
            "icon": {"tag": "standard_icon", "token": "calendar_outlined"},
        },
        "body": {"elements": elements},
    }


def day_work_card(
    *,
    kind: str,
    day: date,
    entries: list[dict[str, Any]],
    followups: str = "",
    task_lines: list[str] | None = None,
) -> dict[str, Any]:
    tasks = [line for line in (task_lines or []) if line]
    work = (followups or "").strip()
    tomorrow = kind == "tomorrow"
    title = "明日安排" if tomorrow else "每日工作简报"
    heading = "明日日程" if tomorrow else "今日日程"
    section = (
        f"**明天安排** · {cn_day(day, paren=False)}"
        if tomorrow
        else f"**二、今天规划** · {cn_day(day, paren=False)}"
    )
    body: list[str] = [section]
    if entries:
        body.append(f"**{heading}**")
        body.extend(_agenda_line(entry) for entry in entries)
    if tasks:
        body.append("**待办**")
        body.extend(f"- {line}" for line in tasks)
    if work:
        body.append("**要跟的活**")
        body.extend(work.splitlines())
    elements: list[dict[str, Any]] = []
    if len(body) > 1:
        elements.append(_md("\n".join(body)))
        agenda_btns = _agenda_actions(entries)
        if agenda_btns:
            elements.append({"tag": "action", "actions": agenda_btns})
    if not tasks and entries and not work:
        footer = (
            "没有卡人的待办，先看明天的会。"
            if tomorrow
            else "没有卡人的待办，先把今天的会开完。"
        )
        elements.append(_md(f"<font color='grey'>{footer}</font>", size="notation"))
    if not elements:
        elements.append(_md("这天没有日程，跟进账里也没有未闭环的活。"))
    sub_bits = [cn_day(day).replace(" (", " · ").rstrip(")")]
    if entries:
        sub_bits.append(f"{len(entries)}场会")
    if not tomorrow:
        sub_bits.append("待回复已清")
    return {
        "schema": "2.0",
        "config": {
            "width_mode": "compact",
            "enable_forward": True,
            "update_multi": True,
            "summary": {"content": f"{title} · {cn_day(day)}"},
        },
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "subtitle": {"tag": "plain_text", "content": " · ".join(sub_bits)},
            "template": "indigo",
            "icon": {"tag": "standard_icon", "token": "calendar_outlined"},
        },
        "body": {"elements": elements},
    }
