"""Feishu schema 2.0 card for the daily brief. Text is only a fallback."""

from __future__ import annotations

from datetime import date
from typing import Any

from .brief import cn_day, work_priorities
from ..compose.formatters import plain_im_text

_LEVEL_COLOR = {"P0": "red", "P1": "orange", "P2": "yellow", "P3": "green"}
_RSVP_COLOR = {"已接受": "green", "待回复": "orange", "已拒绝": "red", "待定": "yellow"}
_SECTION_BG = {"indigo": "CCE0FF", "blue": "D6EBFF", "wathet": "E6F0FF"}


def _md(content: str, *, size: str = "") -> dict[str, Any]:
    el: dict[str, Any] = {"tag": "markdown", "content": content}
    if size:
        el["text_size"] = size
    return el


def _plain(text: str, *, size: str = "") -> dict[str, Any]:
    el: dict[str, Any] = {"tag": "plain_text", "content": text}
    if size:
        el["text_size"] = size
    return el


def _hr() -> dict[str, Any]:
    return {"tag": "hr"}


def _chip(text: str, color: str) -> str:
    return f"<font color='{color}'>**{text}**</font>"


def _tag(text: str, color: str = "grey") -> dict[str, Any]:
    return {"tag": "tag", "text": {"tag": "plain_text", "content": text}, "color": color}


def _icon_text(text: str, icon_token: str) -> dict[str, Any]:
    return {
        "tag": "div",
        "text": {"tag": "lark_md", "content": text},
        "icon": {"tag": "standard_icon", "token": icon_token},
    }


def _stat_card(number: int, label: str, color: str) -> dict[str, Any]:
    return {
        "tag": "column",
        "width": "weighted",
        "weight": 1,
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"<font color='{color}' size=24>**{number}**</font>",
                },
            },
            {"tag": "div", "text": {"tag": "plain_text", "content": label}},
        ],
    }


def _overview_columns(progressed: int, unreplied: int, meetings: int) -> dict[str, Any]:
    return {
        "tag": "column_set",
        "horizontal_rule": {"style": 1, "color": "0x0000000D"},
        "columns": [
            _stat_card(progressed, "昨日推进", "green"),
            _stat_card(unreplied, "待回复", "orange" if unreplied else "grey"),
            _stat_card(meetings, "今日会议", "blue"),
        ],
    }


def _section_header(title: str, subtitle: str = "", *, icon: str = "") -> dict[str, Any]:
    text = f"**{title}**"
    if subtitle:
        text += f" · <font color='grey'>{subtitle}</font>"
    return _icon_text(text, icon or "right_outlined")


def _note_box(text: str, color: str = "grey") -> dict[str, Any]:
    return {"tag": "note", "elements": [_plain(text)], "color": color}


def _link(title: str, url: str) -> str:
    if url:
        return f"[{title}]({url})"
    return title


def _agenda_line(entry: dict[str, Any]) -> str:
    start = str(entry.get("start") or "")
    end = str(entry.get("end") or "")
    when = f"{start}–{end}" if start and end else start
    title = _link(str(entry.get("title") or ""), str(entry.get("app_link") or ""))
    rsvp = str(entry.get("rsvp") or "")
    line = f"**{when}**  {title}" if when else title
    if rsvp:
        line += f"  {_chip(rsvp, _RSVP_COLOR.get(rsvp, 'grey'))}"
    org = str(entry.get("organizer") or "")
    if org:
        line += f"\n<font color='grey' size=12>{org}</font>"
    return line


def _agenda_column(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tag": "column",
        "width": "weighted",
        "weight": 1,
        "elements": [_md(_agenda_line(e)) for e in entries],
    }


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
            out.append(_open_btn(f"回复·{title}", app_link, kind="primary"))
    return out[:4]


def _priority_blocks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        level = str(row.get("level") or "P3")
        title = plain_im_text(str(row.get("title") or ""))
        reason = plain_im_text(str(row.get("reason") or "待跟进"))
        out.append(
            {
                "tag": "column_set",
                "horizontal_rule": {"style": 2, "color": "0x0000000A"},
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [
                            _tag(level, _LEVEL_COLOR.get(level, "green")),
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 5,
                        "elements": [
                            _md(title),
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 3,
                        "elements": [
                            _md(f"<font color='grey'>{reason}</font>"),
                        ],
                    },
                ],
            }
        )
    return out


def _pending_blocks(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items[:5]:
        who = str(item.get("sender_name") or "").strip()
        where = str(item.get("chat_name") or "群")
        text = plain_im_text(str(item.get("text") or ""))[:90]
        tag = str(item.get("tag") or "")
        head = f"**{who}** · {where}" if who else f"**{where}**"
        body = text
        if tag:
            body += f"  {_chip(tag, 'grey')}"

        buttons: list[dict[str, Any]] = []
        link = str(item.get("link") or "").strip()
        if link:
            buttons.append(_open_btn("去看", link, kind="primary"))
        key = str(item.get("key") or "")
        if key:
            buttons.append(
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "✅ 已处理"},
                    "type": "default",
                    "size": "small",
                    "value": {"act": "done", "key": key},
                    "behaviors": [
                        {"type": "callback", "value": {"act": "done", "key": key}}
                    ],
                }
            )
        out.append(
            {
                "tag": "column_set",
                "horizontal_rule": {"style": 2, "color": "0x0000000A"},
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 6,
                        "elements": [
                            _md(head),
                            _md(f"<font color='grey'>{body}</font>", size="notation"),
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 4,
                        "vertical_align": "center",
                        "elements": [{"tag": "action", "actions": buttons}] if buttons else [],
                    },
                ],
            }
        )
    return out


def brief_card(data: dict[str, Any], *, followups: str = "") -> dict[str, Any]:
    today = date.fromisoformat(str(data["today"]))
    workday = date.fromisoformat(str(data["workday"]))
    entries = [item for item in (data.get("today_entries") or []) if isinstance(item, dict)]
    pending = [item for item in (data.get("pending") or []) if isinstance(item, dict)]
    progressed = [
        plain_im_text(str(item)) for item in (data.get("progressed") or []) if item
    ]
    unreplied = [
        plain_im_text(str(item)) for item in (data.get("unreplied") or []) if item
    ]
    long_term = [
        plain_im_text(str(item)) for item in (data.get("long_term") or []) if item
    ]
    week_notes = [str(item) for item in (data.get("week_notes") or []) if item]
    rows = work_priorities(data.get("priorities") or [], agenda=entries or data.get("today_agenda"))
    work_lines = [
        plain_im_text(line)
        for line in (followups or str(data.get("followups") or "")).splitlines()
        if line.strip()
    ]

    elements: list[dict[str, Any]] = []

    # 顶部统计
    elements.append(_overview_columns(len(progressed), len(unreplied), len(entries)))

    # 昨日小结
    if progressed or unreplied or long_term:
        elements.append(_hr())
        elements.append(
            _section_header(
                "一、昨天小结", cn_day(workday, paren=False), icon="time_filled"
            )
        )
        if progressed:
            elements.append(_md("**推进事项**"))
            for item in progressed:
                elements.append(_md(f"✅ {item}"))
        if unreplied:
            elements.append(_md(f"**待处理 · {len(unreplied)}**"))
            elements.extend(_pending_blocks([
                {"text": item, "sender_name": "", "chat_name": "", "tag": ""}
                for item in unreplied
            ]))
        if long_term:
            elements.append(_note_box(f"长期待办 {len(long_term)} 项：" + " / ".join(long_term)))

    # 今天规划
    today_elements: list[dict[str, Any]] = []
    if entries:
        today_elements.append(_md("**今日日程**"))
        today_elements.extend(_md(_agenda_line(e)) for e in entries)
        agenda_btns = _agenda_actions(entries)
        if agenda_btns:
            today_elements.append({"tag": "action", "actions": agenda_btns})
    elif week_notes:
        today_elements.append(_md("**本周值得关注**"))
        for item in week_notes:
            today_elements.append(_md(f"• {item}"))

    if today_elements or rows or work_lines:
        elements.append(_hr())
        elements.append(
            _section_header("二、今天规划", cn_day(today, paren=False), icon="tab_calendar_colorful")
        )
        elements.extend(today_elements)

    if rows:
        clean_rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            clean = dict(row)
            clean["title"] = plain_im_text(str(row.get("title") or ""))
            clean["reason"] = plain_im_text(str(row.get("reason") or ""))
            clean_rows.append(clean)
        elements.append(_md("**优先处理**"))
        elements.extend(_priority_blocks(clean_rows))
    elif entries and not work_lines:
        elements.append(
            _note_box("没有卡人的待办，先把今天的会开完。", color="blue")
        )

    if work_lines:
        if elements and elements[-1].get("tag") != "hr":
            elements.append(_hr())
        elements.append(_md("**要跟的活**"))
        for line in work_lines:
            elements.append(_md(f"• {line}"))

    if pending:
        elements.append(_hr())
        elements.append(
            _section_header("新收到的 @ 与指派", f"{len(pending)} 条", icon="at_filled")
        )
        elements.extend(_pending_blocks(pending))

    if not elements:
        elements.append(_note_box("没有必须立刻排的事。"))

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
                "content": f"{cn_day(today).replace(' (', ' · ').rstrip(')')} · {len(entries)}场会 · 待回复{len(unreplied)}",
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
        else f"**今天规划** · {cn_day(day, paren=False)}"
    )

    elements: list[dict[str, Any]] = []
    elements.append(_overview_columns(0, 0, len(entries)))
    elements.append(_hr())
    elements.append(_section_header(heading, cn_day(day, paren=False), icon="tab_calendar_colorful"))

    body_lines = [section]
    if entries:
        body_lines.append("**日程**")
        body_lines.extend(_agenda_line(e) for e in entries)
    if tasks:
        body_lines.append("**待办**")
        for line in tasks:
            body_lines.append(f"- {line}")
    if work:
        body_lines.append("**要跟的活**")
        for line in work.splitlines():
            if line.strip():
                body_lines.append(f"- {plain_im_text(line)}")

    if len(body_lines) > 1:
        elements.append(_md("\n".join(body_lines)))
        agenda_btns = _agenda_actions(entries)
        if agenda_btns:
            elements.append({"tag": "action", "actions": agenda_btns})

    if not tasks and entries and not work:
        footer = (
            "没有卡人的待办，先看明天的会。"
            if tomorrow
            else "没有卡人的待办，先把今天的会开完。"
        )
        elements.append(_note_box(footer, color="blue"))

    if not elements:
        elements.append(_note_box("这天没有日程，跟进账里也没有未闭环的活。"))

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
            "subtitle": {
                "tag": "plain_text",
                "content": f"{cn_day(day).replace(' (', ' · ').rstrip(')')} · {len(entries)}场会",
            },
            "template": "indigo",
            "icon": {"tag": "standard_icon", "token": "calendar_outlined"},
        },
        "body": {"elements": elements},
    }
