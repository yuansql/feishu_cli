from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from ..compose.formatters import document_markdown, format_agenda, format_lark_error, format_tasks, format_day_work, format_today, format_weekly_from_doc, format_weekly_human, pick_personal_weekly
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

def weekly_text(focus: str='') -> str:
    start, end = _week_bounds()
    agenda = _agenda_range(start, end)
    tasks = run_lark(['task', '+get-my-tasks', '--complete=false', '--page-limit', '20'], as_identity='user')
    docs = run_lark(['docs', '+search', '--query', WEEKLY_QUERY, '--page-size', '5'], as_identity='user')
    title, url = pick_personal_weekly(docs)
    if not url:
        docs = run_lark(['docs', '+search', '--query', '周报', '--page-size', '5'], as_identity='user')
        title, url = pick_personal_weekly(docs)
    if url:
        fetched = run_lark(['docs', '+fetch', '--doc', url, '--doc-format', 'markdown', '--detail', 'simple'], as_identity='user')
        shaped = format_weekly_from_doc(document_markdown(fetched), start, end, tasks, source_title=title, source_url=url, focus=focus)
        if shaped:
            return _with_inbox(shaped)
    if focus == 'next':
        nstart, nend = _next_week_bounds()
        next_agenda = _agenda_range(nstart, nend)
        return _with_inbox(format_agenda(next_agenda, heading='下周日程', empty='下周日历还没记下会，周报里也没有下周计划。'))
    return _with_inbox(format_weekly_human(start, end, agenda, tasks, docs))

def _created_doc_link(payload: dict[str, Any]) -> str:
    data = payload.get('data') if isinstance(payload.get('data'), dict) else payload
    if not isinstance(data, dict):
        return ''
    doc = data.get('document') if isinstance(data.get('document'), dict) else data
    if not isinstance(doc, dict):
        return ''
    return str(doc.get('url') or data.get('url') or doc.get('doc_url') or '')

def write_weekly_text() -> str:
    """写周报：本周跨会话聊天证据 + 未完成待办（含上周结转），不搜部门周报文档。"""
    from ..compose.llm import draft_weekly_from_chats
    from .recap import collect_week_evidence, _fallback_summary
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
        fallback = _fallback_summary(bundle.context or '')
        polished = '【本周完成】\n' + (fallback or '- 聊天证据不足，暂无法归纳完成项。') + '\n\n【进行中与上周结转】\n' + (tasks_blob or '- 暂无未完成待办。') + '\n\n【问题与风险】\n- 暂无\n\n【下周计划】\n' + '- 按未完成待办与进行中事项继续推进'
    heading = f'{start.month}/{start.day}–{end.month}/{end.day} 周报（依据本周 {bundle.evidence_count} 条聊天证据 / 检索 {bundle.message_count} 条）'
    body = f'{heading}\n\n{polished.strip()}'
    title = f'周报 {start.date().isoformat()} ~ {end.date().isoformat()}'
    created = run_lark(['docs', '+create', '--title', title, '--doc-format', 'markdown', '--content', body], as_identity='user')
    if created.get('ok'):
        link = _created_doc_link(created)
        extra = f'\n{link}' if link else ''
        return f'已根据本周聊天与未完成待办生成云文档《{title}》。{extra}\n\n' + body
    return format_lark_error(created) + '\n\n先把摘要放这儿：\n' + body
