from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from ..compose.formatters import (
    document_markdown,
    format_agenda,
    format_lark_error,
    format_tasks,
    format_day_work,
    format_today,
    format_weekly_from_doc,
    format_weekly_human,
    format_weekly_retrospective,
    pick_personal_weekly,
)
from ..core.ids import WEEKLY_QUERY
from ..core.lark import run_lark

CN_TZ = timezone(timedelta(hours=8))

def _week_bounds(now: datetime | None=None) -> tuple[datetime, datetime]:
    now = now or datetime.now(CN_TZ)
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7) - timedelta(seconds=1)
    return (start, end)

def _day_bounds(offset_days: int, now: datetime | None=None) -> tuple[datetime, datetime]:
    now = now or datetime.now(CN_TZ)
    day = (now + timedelta(days=offset_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (day, day.replace(hour=23, minute=59, second=59))

def _agenda_range(start: datetime, end: datetime) -> dict[str, Any]:
    return run_lark(['calendar', '+agenda', '--start', start.isoformat(), '--end', end.isoformat()], as_identity='user')

def _open_followup_text() -> str:
    from .followup import followups_for_command
    return followups_for_command()

def today_text() -> str:
    agenda = run_lark(['calendar', '+agenda'], as_identity='user')
    tasks = run_lark(['task', '+get-my-tasks', '--complete=false', '--page-limit', '20'], as_identity='user')
    return format_today(format_agenda(agenda), format_tasks(tasks), _open_followup_text())

def tomorrow_text() -> str:
    start, end = _day_bounds(1)
    agenda = _agenda_range(start, end)
    tasks = run_lark(['task', '+get-my-tasks', '--complete=false', '--page-limit', '20'], as_identity='user')
    return format_day_work(format_agenda(agenda, heading='明日日程', empty='明天没有日程。'), format_tasks(tasks), _open_followup_text())

def _next_week_bounds(now: datetime | None=None) -> tuple[datetime, datetime]:
    start, end = _week_bounds(now)
    return (start + timedelta(days=7), end + timedelta(days=7))

def _prev_week_bounds(now: datetime | None=None) -> tuple[datetime, datetime]:
    start, end = _week_bounds(now)
    return (start - timedelta(days=7), end - timedelta(days=7))


def weekly_text(focus: str='') -> str:
    from .messaging import _with_inbox
    from ..routing.resolved import resolved_in_window

    if focus == 'last':
        start, end = _prev_week_bounds()
        agenda = _agenda_range(start, end)
        tasks = run_lark(['task', '+get-my-tasks', '--complete=true', '--page-limit', '30'], as_identity='user')
        resolved = resolved_in_window(start, end)
        return _with_inbox(format_weekly_retrospective(start, end, agenda, tasks, resolved, focus='last'))

    start, end = _week_bounds()
    # --- cross-source aggregation (like Doubao) ---
    from .recap import collect_week_evidence, curated_work_buckets
    from ..compose.llm import draft_weekly_from_chats

    bundle = collect_week_evidence(start, end)
    agenda = _agenda_range(start, end)
    tasks = run_lark(['task', '+get-my-tasks', '--complete=false', '--page-limit', '20'], as_identity='user')
    minutes = run_lark(['minutes', '+search', '--participant-ids', 'me', '--start', start.date().isoformat(), '--page-size', '8'], as_identity='user')

    # Build narrative summary from multiple sources
    tasks_blob = format_tasks(tasks)
    if tasks.get('ok') is False:
        tasks_blob = format_lark_error(tasks)

    # Read minutes details (summary/chapters) for richer context
    minutes_context: list[str] = []
    if minutes.get('ok'):
        from ..compose.formatters import _items
        mins = [m for m in _items(minutes, 'minutes', 'items', 'list') if isinstance(m, dict)]
        from .docs_io import minutes_detail_text
        for m in mins[:3]:
            token = m.get('minute_token') or m.get('token') or ''
            if token:
                detail = minutes_detail_text(token)
                if detail:
                    title = m.get('title') or m.get('topic') or '会议纪要'
                    minutes_context.append(f"【{title}】\n{detail[:600]}")

    # Try LLM synthesis first
    facts = (
        f"周期：{start.date().isoformat()} ~ {end.date().isoformat()}\n"
        f"本周检索消息 {bundle.message_count} 条，工作相关证据 {bundle.evidence_count} 条。\n\n"
        f"【本周聊天证据】\n{(bundle.context or '（本周几乎没有可作周报的工作聊天）')[:5000]}\n\n"
        f"【当前未完成待办（含上周结转）】\n{tasks_blob[:2000]}"
    )
    if minutes_context:
        facts += "\n\n【本周会议纪要摘要】\n" + "\n\n".join(minutes_context)[:2000]
    polished = draft_weekly_from_chats(facts, timeout=60)

    if polished and "【本周完成】" in polished:
        lines = [
            f"本周工作盘点（{_slash_date(start)} 周一 – {_slash_date(end)} 周五，截至现在）",
            "",
            polished.strip(),
        ]
        # Append related minutes links if available
        if minutes.get('ok'):
            from ..compose.formatters import _items
            mins = [m for m in _items(minutes, 'minutes', 'items', 'list') if isinstance(m, dict)]
            if mins:
                lines.append("")
                lines.append("相关妙记：")
                for m in mins[:5]:
                    title = m.get('title') or m.get('topic') or '(无主题)'
                    url = m.get('url') or m.get('share_url') or ''
                    lines.append(f"- {title}  {url}".rstrip())
        return _with_inbox("\n".join(lines))

    # Fallback: structured like Doubao when LLM unavailable
    done, progress, pending = curated_work_buckets(bundle.context or "")

    lines = [
        f"本周工作盘点（{_slash_date(start)} 周一 – {_slash_date(end)} 周五，截至现在）",
        "",
    ]

    # Meetings from calendar
    if agenda.get('ok') is not False:
        from ..compose.formatters import _items
        events = [e for e in _items(agenda, 'events', 'items', 'calendar_events') if isinstance(e, dict)]
        if events:
            lines.append(f"一、参加/待开的会议（日历日程 {len(events)} 场）")
            for item in events[:10]:
                day = _event_day(item)
                title = _event_title(item)
                when = _when(item.get('start_time') or item.get('start'))
                extra = f"（{when}）" if when else ""
                lines.append(f"- {day} {title}{extra}".strip())
            lines.append("")

    # Core work from chat evidence
    lines.append("二、核心工作")
    if done:
        for i, item in enumerate(done[:6], 1):
            lines.append(f"{i}. {item}")
    elif progress:
        for i, item in enumerate(progress[:6], 1):
            lines.append(f"{i}. {item}")
    else:
        lines.append("- 聊天证据不足，暂无法归纳本周完成项。")
    lines.append("")

    # Pending / open tasks
    lines.append("三、待推进")
    if progress:
        for item in progress[:6]:
            lines.append(f"- {item}")
    if tasks_blob and "没有未完成" not in tasks_blob:
        for line in tasks_blob.splitlines()[:6]:
            s = line.strip(" -•\t")
            if s and "未完成" not in s:
                lines.append(f"- {s}")
    if len(lines) == 0 or lines[-1] == "三、待推进":
        lines.append("- 暂无明确待推进事项。")
    lines.append("")

    # Next week plan
    lines.append("四、下周计划")
    plans = [f"跟进：{p}" for p in pending[:3]] if pending else []
    if not plans:
        plans = ["按未完成待办与进行中事项继续推进"]
    for p in plans:
        lines.append(f"- {p}")
    lines.append("")

    # Related minutes
    if minutes.get('ok'):
        from ..compose.formatters import _items
        mins = [m for m in _items(minutes, 'minutes', 'items', 'list') if isinstance(m, dict)]
        if mins:
            lines.append("相关妙记：")
            for m in mins[:5]:
                title = m.get('title') or m.get('topic') or '(无主题)'
                url = m.get('url') or m.get('share_url') or ''
                lines.append(f"- {title}  {url}".rstrip())

    return _with_inbox("\n".join(lines))

def _created_doc_link(payload: dict[str, Any]) -> str:
    data = payload.get('data') if isinstance(payload.get('data'), dict) else payload
    if not isinstance(data, dict):
        return ''
    doc = data.get('document') if isinstance(data.get('document'), dict) else data
    if not isinstance(doc, dict):
        return ''
    return str(doc.get('url') or data.get('url') or doc.get('doc_url') or '')

def write_weekly_text(query: str = "") -> str:
    """写周报。有 wiki/docx 链接 → 原地填部门模板；否则用聊天证据新建云文档。"""
    from ..compose.llm import draft_weekly_from_chats
    from .recap import collect_week_evidence, curated_work_buckets, _fallback_summary
    from .weekly_fill import extract_weekly_doc_url, fill_department_weekly

    doc_url = extract_weekly_doc_url(query or "")
    if doc_url:
        return fill_department_weekly(doc_url)

    start, end = _week_bounds()
    last_start = start - timedelta(days=7)
    last_end = start - timedelta(seconds=1)
    bundle = collect_week_evidence(start, end)
    if bundle.error:
        return f'写周报需要跨会话读消息，但取数失败：\n{bundle.error}\n请 `feishu doctor` 看是否缺 `search:message`，补权限后重试。'
    open_tasks = run_lark(['task', '+get-my-tasks', '--complete=false', '--page-limit', '30'], as_identity='user')
    tasks_blob = format_tasks(open_tasks)
    if open_tasks.get('ok') is False:
        tasks_blob = format_lark_error(open_tasks)
    facts = f"周期：{start.date().isoformat()} ~ {end.date().isoformat()}\n上周结转参考窗：{last_start.date().isoformat()} ~ {last_end.date().isoformat()}\n本周检索消息 {bundle.message_count} 条，工作相关证据 {bundle.evidence_count} 条。\n\n【本周聊天证据】\n{(bundle.context or '（本周几乎没有可作周报的工作聊天）')[:7000]}\n\n【当前未完成待办（含上周结转）】\n{tasks_blob[:2500]}"
    polished = draft_weekly_from_chats(facts)
    if not polished:
        done, progress, pending = curated_work_buckets(bundle.context or "")
        if not done and not progress:
            fallback = _fallback_summary(bundle.context or "")
            polished = (
                "【本周完成】\n"
                + (fallback.split("【推进中】")[0].replace("【今天确认做过】", "").strip() or "- 聊天证据不足，暂无法归纳完成项。")
                + "\n\n【进行中与上周结转】\n"
                + (tasks_blob or "- 暂无未完成待办。")
                + "\n\n【问题与风险】\n- 暂无\n\n【下周计划】\n- 按未完成待办与进行中事项继续推进"
            )
        else:
            def bullets(rows: list[str], empty: str) -> str:
                return "\n".join(f"- {r}" for r in rows) if rows else f"- {empty}"

            mid = list(progress)
            if tasks_blob and "没有未完成" not in tasks_blob:
                for line in tasks_blob.splitlines():
                    s = line.strip(" -•\t")
                    if s and "【" not in s and s not in mid:
                        mid.append(s)

            polished = (
                "【本周完成】\n"
                + bullets(done, "聊天证据不足，暂无法归纳完成项。")
                + "\n\n【进行中与上周结转】\n"
                + bullets(mid, "暂无未完成待办。")
                + "\n\n【问题与风险】\n"
                + bullets(pending, "暂无")
                + "\n\n【下周计划】\n"
                + bullets(
                    [f"跟进：{p}" for p in pending[:3]]
                    or ["按未完成待办与进行中事项继续推进"],
                    "暂无",
                )
            )
    heading = f'{start.month}/{start.day}–{end.month}/{end.day} 周报（依据本周 {bundle.evidence_count} 条聊天证据 / 检索 {bundle.message_count} 条）'
    body = f'{heading}\n\n{polished.strip()}'
    title = f'周报 {start.date().isoformat()} ~ {end.date().isoformat()}'
    created = run_lark(['docs', '+create', '--title', title, '--doc-format', 'markdown', '--content', body], as_identity='user')
    if created.get('ok'):
        link = _created_doc_link(created)
        extra = f'\n{link}' if link else ''
        tip = (
            "\n\n若要填部门周报模板，请发：wiki/docx 链接 +「填写周报」"
            "（会写入你的人名节，不再另开文档）。"
        )
        return f'已根据本周聊天与未完成待办生成云文档《{title}》。{extra}\n\n' + body + tip
    return format_lark_error(created) + '\n\n先把摘要放这儿：\n' + body
