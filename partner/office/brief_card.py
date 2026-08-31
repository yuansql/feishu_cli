"""Feishu card for the daily brief. Uses schema 1.0 for interactive buttons."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .brief import cn_day, work_priorities
from ..compose.formatters import plain_im_text
from ..core.ids import USER_NAMES

_AssignPattern = re.compile(r"^【(.+?)】有人指派你了\s*[（(](\d{4}-\d{2}-\d{2})[)）]\s*$")

_LEVEL_COLOR = {"P0": "red", "P1": "orange", "P2": "yellow", "P3": "green"}
_RSVP_COLOR = {"已接受": "green", "待回复": "orange", "已拒绝": "red", "待定": "yellow"}


def _lark_md(content: str) -> dict[str, Any]:
    return {"tag": "lark_md", "content": content}


def _md(content: str) -> dict[str, Any]:
    # Schema 1.0 top-level element must be a container; lark_md is only valid as inner text.
    return {"tag": "div", "text": _lark_md(content)}


def _plain(text: str) -> dict[str, Any]:
    return {"tag": "plain_text", "content": text}


def _hr() -> dict[str, Any]:
    return {"tag": "hr"}


def _chip(text: str, color: str) -> str:
    return f"<font color='{color}'>**{text}**</font>"


def _tag_md(text: str, color: str = "grey") -> str:
    color_map = {
        "red": "#F53F3F",
        "orange": "#FF7D00",
        "yellow": "#FFC300",
        "green": "#00B42A",
        "blue": "#165DFF",
        "grey": "#86909C",
    }
    return f"<font color='{color_map.get(color, color)}'>**{text}**</font>"


def _icon_text(text: str, icon_token: str) -> dict[str, Any]:
    # Schema 1.0 div with markdown text; ignore unregistered icon tokens.
    return {"tag": "div", "text": {"tag": "lark_md", "content": text}}


def _stat_card(number: int, label: str, color: str) -> dict[str, Any]:
    content = (
        f"<font color='{color}' size=24>**{number}**</font><br/>"
        f"<font color='grey' size=12>{label}</font>"
    )
    return {
        "tag": "column",
        "width": "weighted",
        "weight": 1,
        "vertical_align": "center",
        "elements": [
            {
                "tag": "div",
                "text": _lark_md(content),
            },
        ],
    }


def _overview_columns(progressed: int, unreplied: int, meetings: int) -> dict[str, Any]:
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "background_style": "default",
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
    color_map = {
        "red": "#F53F3F",
        "orange": "#FF7D00",
        "yellow": "#FFC300",
        "green": "#00B42A",
        "blue": "#165DFF",
        "grey": "#86909C",
    }
    return _md(f"<font color='{color_map.get(color, color)}'>▌</font> {text}")


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


def _open_button(url: str, label: str = "打开") -> dict[str, Any]:
    """Schema 1.0 link button."""
    return {
        "tag": "button",
        "text": _plain(label),
        "type": "default",
        "size": "small",
        "url": url,
    }


def _card_button(key: str, label: str = "完成") -> dict[str, Any]:
    """Schema 1.0 callback button for pending resolution via confirm_card."""
    return {
        "tag": "button",
        "text": _plain(label),
        "type": "default",
        "size": "small",
        "value": {"act": "done", "key": key},
    }


def _fu_done_button(key: str, label: str = "完成") -> dict[str, Any]:
    """Schema 1.0 callback button for follow-up ledger apply_action."""
    return {
        "tag": "button",
        "text": _plain(label),
        "type": "default",
        "size": "small",
        "value": {"act": "fu_done", "key": key},
    }


def _row_with_action(
    line_text: str,
    key: str,
    label: str = "完成",
    link: str = "",
    *,
    use_followup: bool = False,
) -> list[dict[str, Any]]:
    """Schema 1.0 block: text line followed by an action block of buttons.

    Feishu schema 1.0 requires callback buttons to live inside an 'action'
    element; standalone buttons at the card root are not rendered as clickable.
    """
    actions: list[dict[str, Any]] = []
    if link:
        actions.append(_open_button(link, "打开消息"))
    actions.append(_fu_done_button(key, label) if use_followup else _card_button(key, label))
    return [
        {"tag": "div", "text": _lark_md(line_text)},
        {"tag": "action", "actions": actions},
    ]


def _priority_blocks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        level = str(row.get("level") or "P3")
        title = plain_im_text(str(row.get("title") or ""))
        reason = plain_im_text(str(row.get("reason") or "待跟进"))
        key = str(row.get("key") or "")
        link = str(row.get("link") or "").strip()
        text = f"{_tag_md(level, _LEVEL_COLOR.get(level, 'green'))}  {title}\n<font color='grey' size=12>{reason}</font>"
        if key:
            out.extend(_row_with_action(text, key, "完成", link=link))
        else:
            out.append(_md(text))
    return out


def _task_text(item: dict[str, Any], *, show_source: bool = True) -> str:
    """Render structured follow-up / pending item text."""
    kind = str(item.get("kind") or "").strip()
    who = str(item.get("asker_name") or item.get("sender_name") or "").strip()
    assignee = str(item.get("assignee_name") or "").strip()
    named = who or assignee
    where = str(item.get("chat_name") or item.get("source_name") or "").strip()
    text = plain_im_text(str(item.get("text") or "")).strip()
    due = str(item.get("due") or "").strip()

    # Bitable assignment notifications: prefer table name + assigner + title/record date.
    if kind == "bitable_at":
        match = _AssignPattern.search(text)
        inner_title = ""
        table_date = ""
        if match:
            inner_title = match.group(1).strip()
            table_date = match.group(2)
        # Try to enrich generic Bitable notifications with the real record detail.
        if match and (not named or named == "有人"):
            try:
                from .bitable import reread_bitable_detail
                detail = reread_bitable_detail(
                    record_id=str(item.get("message_id") or item.get("id") or ""),
                    table_label=where,
                )
                if detail:
                    bug = ""
                    assigner = ""
                    for line in detail.splitlines():
                        if line.startswith("Bug描述："):
                            bug = line.split("：", 1)[-1].strip()
                        elif line.startswith("指派人："):
                            assigner = line.split("：", 1)[-1].strip()
                    if bug:
                        text = bug
                    if assigner and assigner not in USER_NAMES:
                        named = assigner
            except Exception:
                pass
        parts: list[str] = []
        if where and where != inner_title:
            parts.append(f"**{where}**")
        if named and named != "有人":
            parts.append(f"**{named}**")
        elif inner_title:
            parts.append(f"**{inner_title}**")
        if text and (not match or (named and named != "有人")):
            parts.append(text)
        if table_date:
            parts.append(f"<font color='grey'>{table_date}</font>")
        elif due:
            parts.append(f"<font color='grey'>截止 {due}</font>")
        return " · ".join(parts) if len(parts) > 1 else (parts[0] if parts else text or "任务")

    # Clean up generic Bitable assignment notifications that lack real title/person.
    table_date: str | None = None
    match = _AssignPattern.search(text)
    if match:
        table_date = match.group(2)
        if table_date and due:
            due = table_date
        text = f"指派日期：{table_date}" if table_date else ""

    parts = []
    if named and named != "有人":
        parts.append(f"**{named}**")
    elif where:
        parts.append(f"**{where}**")
    if text:
        parts.append(text)
    if due and due not in text:
        parts.append(f"<font color='grey'>截止 {due}</font>")
    if show_source and where and not (named and where) and not (text and where in text):
        parts.append(f"<font color='grey'>{where}</font>")
    return " · ".join(parts) if len(parts) > 1 else (parts[0] if parts else text or "任务")


def _work_items_blocks(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Structured follow-up items with done buttons."""
    out: list[dict[str, Any]] = []
    for item in items[:8]:
        key = str(item.get("id") or item.get("key") or "").strip()
        text = _task_text(item, show_source=True)
        link = str(item.get("link") or "").strip()
        if key:
            out.extend(_row_with_action(text, key, "完成", link=link, use_followup=True))
        else:
            out.append(_note_box(text))
    return out


def _pending_blocks(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pending @/mail items with handled buttons."""
    out: list[dict[str, Any]] = []
    for item in items[:5]:
        key = str(item.get("key") or "").strip()
        who = str(item.get("sender_name") or "").strip()
        where = str(item.get("chat_name") or "群")
        raw_text = plain_im_text(str(item.get("text") or ""))[:160]
        tag = str(item.get("tag") or "")
        link = str(item.get("link") or "").strip()

        head = f"**{who}** · {where}" if who else f"**{where}**"
        body = raw_text
        if tag:
            body += f"  {_chip(tag, 'grey')}"
        text = f"{head}\n<font color='grey' size=12>{body}</font>"
        if key:
            out.extend(_row_with_action(text, key, "已处理", link=link))
        else:
            out.append(_md(text))
    return out


def _resolve_work_items(
    data: dict[str, Any], followups: str, followup_items: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Return structured follow-up items for the day."""
    if followup_items:
        # Only open items.
        return [it for it in followup_items if str(it.get("status") or "") not in {"done", "ignore"}]
    # Legacy: parse text lines into pseudo-items.
    lines = [plain_im_text(line) for line in followups.splitlines() if line.strip()]
    return [{"text": line} for line in lines]


def _card_config(title: str, today: date) -> dict[str, Any]:
    return {
        "wide_screen_mode": True,
        "enable_forward": True,
        "update_multi": True,
    }


def brief_card(
    data: dict[str, Any],
    *,
    followups: str = "",
    followup_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
    work_items = _resolve_work_items(data, followups, followup_items)

    elements: list[dict[str, Any]] = []

    # 顶部统计
    elements.append(_overview_columns(len(progressed), len(unreplied), len(entries)))

    # 采集饱和度/失败警告
    if data.get("fetch_failed"):
        elements.append(_note_box("⚠️ 部分 @ 消息采集失败，今天的待处理列表可能不完整，请手动复核", color="red"))
    elif data.get("truncated"):
        elements.append(_note_box("⚠️ 昨日 @ 消息较多，只采集到部分内容，请手动复核群聊", color="orange"))

    # 昨日小结
    if progressed or unreplied or long_term:
        elements.append(_hr())
        elements.append(
            _section_header("一、昨天小结", cn_day(workday, paren=False), icon="time_filled")
        )
        if progressed:
            elements.append(_md("**推进事项**"))
            for item in progressed:
                elements.append(_md(f"✅ {item}"))
        if unreplied:
            elements.append(_md(f"**待处理 · {len(unreplied)}**"))
            elements.extend(
                _pending_blocks([
                    {"text": item, "sender_name": "", "chat_name": "", "tag": ""}
                    for item in unreplied
                ])
            )
        if long_term:
            elements.append(_note_box(f"长期待办 {len(long_term)} 项：" + " / ".join(long_term)))

    # 今天规划
    today_elements: list[dict[str, Any]] = []
    if entries:
        today_elements.append(_md("**今日日程**"))
        today_elements.extend(_md(_agenda_line(e)) for e in entries)
    elif week_notes:
        today_elements.append(_md("**本周值得关注**"))
        for item in week_notes:
            today_elements.append(_md(f"• {item}"))

    if today_elements or rows or work_items:
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
            # Inject the pending key so the card can render a done button for replies.
            clean["key"] = str(row.get("key") or "")
            clean_rows.append(clean)
        if clean_rows:
            elements.append(_md("**优先处理**"))
            elements.extend(_priority_blocks(clean_rows))
    elif entries and not work_items:
        elements.append(_note_box("没有卡人的待办，先把今天的会开完。", color="blue"))

    if work_items:
        if elements and elements[-1].get("tag") != "hr":
            elements.append(_hr())
        elements.append(_md("**要跟的活**"))
        elements.extend(_work_items_blocks(work_items))

    if pending:
        # Drop items already shown in 优先处理 to avoid duplication.
        shown_keys = {str(row.get("key") or "") for row in rows if row.get("key")}
        fresh_pending = [
            item for item in pending
            if str(item.get("key") or "") not in shown_keys
        ]
        if fresh_pending:
            elements.append(_hr())
            elements.append(
                _section_header("新收到的 @ 与指派", f"{len(fresh_pending)} 条", icon="at_filled")
            )
            elements.extend(_pending_blocks(fresh_pending))

    if not elements:
        elements.append(_note_box("没有必须立刻排的事。"))

    return {
        "config": _card_config("每日工作简报", today),
        "header": {
            "title": _plain("每日工作简报"),
            "subtitle": _plain(
                f"{cn_day(today).replace(' (', ' · ').rstrip(')')} · {len(entries)}场会 · 待回复{len(unreplied)}"
            ),
            "template": "indigo",
        },
        "elements": elements,
    }


def day_work_card(
    *,
    kind: str,
    day: date,
    entries: list[dict[str, Any]],
    followups: str = "",
    followup_items: list[dict[str, Any]] | None = None,
    task_lines: list[str] | None = None,
) -> dict[str, Any]:
    tasks = [line for line in (task_lines or []) if line]
    tomorrow = kind == "tomorrow"
    title = "明日安排" if tomorrow else "每日工作简报"
    heading_text = "明日日程" if tomorrow else "今日日程"
    section = (
        f"**明天安排** · {cn_day(day, paren=False)}"
        if tomorrow
        else f"**今天规划** · {cn_day(day, paren=False)}"
    )

    elements: list[dict[str, Any]] = []
    elements.append(_overview_columns(0, 0, len(entries)))
    elements.append(_hr())
    elements.append(_section_header(heading_text, cn_day(day, paren=False), icon="tab_calendar_colorful"))

    body_lines = [section]
    if entries:
        body_lines.append("**日程**")
        body_lines.extend(_agenda_line(e) for e in entries)
    if tasks:
        body_lines.append("**待办**")
        for line in tasks:
            body_lines.append(f"- {line}")
    if followup_items:
        body_lines.append("**要跟的活**")
        for item in followup_items[:6]:
            body_lines.append("- " + _task_text(item, show_source=False))
    elif followups.strip():
        body_lines.append("**要跟的活**")
        for line in followups.splitlines():
            if line.strip():
                body_lines.append(f"- {plain_im_text(line)}")

    if len(body_lines) > 1:
        elements.append(_md("\n".join(body_lines)))

    # Add done buttons for follow-ups in a clean row list below the markdown block.
    if followup_items:
        for item in followup_items[:6]:
            key = str(item.get("id") or item.get("key") or "").strip()
            if not key:
                continue
            elements.append(_hr())
            elements.extend(
                _row_with_action(
                    _task_text(item, show_source=True),
                    key,
                    "完成",
                    link=str(item.get("link") or "").strip(),
                    use_followup=True,
                )
            )

    if not tasks and entries and not followup_items and not followups.strip():
        footer = (
            "没有卡人的待办，先看明天的会。"
            if tomorrow
            else "没有卡人的待办，先把今天的会开完。"
        )
        elements.append(_note_box(footer, color="blue"))

    if not elements:
        elements.append(_note_box("这天没有日程，跟进账里也没有未闭环的活。"))

    return {
        "config": _card_config(title, day),
        "header": {
            "title": _plain(title),
            "subtitle": _plain(
                f"{cn_day(day).replace(' (', ' · ').rstrip(')')} · {len(entries)}场会"
            ),
            "template": "indigo",
            "icon": {"tag": "standard_icon", "token": "calendar_outlined"},
        },
        "elements": elements,
    }
