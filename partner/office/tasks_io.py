from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any
from ..compose.formatters import _items, format_lark_error, format_tasks
from ..core.lark import run_lark

CN_TZ = timezone(timedelta(hours=8))

def task_rows(payload: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in _items(payload, 'items', 'tasks'):
        if not isinstance(item, dict):
            continue
        title = str(item.get('summary') or item.get('title') or '').strip()
        guid = str(item.get('guid') or item.get('task_id') or item.get('id') or '').strip()
        if title or guid:
            rows.append({'title': title, 'guid': guid})
    return rows

def match_tasks(hint: str, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    needle = (hint or '').strip()
    open_rows = [row for row in rows if row.get('guid') or row.get('title')]
    if not needle:
        return open_rows
    hits: list[dict[str, str]] = []
    for row in open_rows:
        title = row.get('title') or ''
        if needle in title or (title and title in needle):
            hits.append(row)
    return hits

def tasks_bundle() -> tuple[str, list[dict[str, str]]]:
    payload = run_lark(['task', '+get-my-tasks', '--complete=false', '--page-limit', '20'], as_identity='user')
    return (format_tasks(payload), task_rows(payload))

def tasks_text() -> str:
    return tasks_bundle()[0]

def complete_task_text(hint: str, *, session_items: list[dict[str, Any]] | None=None) -> str:
    payload = run_lark(['task', '+get-my-tasks', '--complete=false', '--page-limit', '20'], as_identity='user')
    if payload.get('ok') is False:
        return format_lark_error(payload)
    rows = task_rows(payload)
    extra = [{'title': str(item.get('title') or item.get('summary') or ''), 'guid': str(item.get('guid') or '')} for item in session_items or [] if isinstance(item, dict)]
    hits = match_tasks(hint, rows) or match_tasks(hint, extra)
    if not hits:
        if rows:
            listed = '\n'.join((f"- {row['title']}" for row in rows[:8]))
            return '没对上要勾的待办。当前未完成：\n' + listed
        return '没有未完成待办可勾。'
    if len(hits) > 1:
        listed = '\n'.join((f"- {row['title']}" for row in hits[:8]))
        return '对上好几条待办，把标题再说清楚点：\n' + listed
    item = hits[0]
    guid = item.get('guid') or ''
    title = item.get('title') or '这条'
    if not guid:
        return f'找到《{title}》但没有任务 ID，没法在飞书勾掉。'
    result = run_lark(['task', '+complete', '--task-id', guid], as_identity='user')
    if result.get('ok'):
        return f'已勾完成：{title}\n（飞书待办是标记完成，不是从回收站抹掉。）'
    return format_lark_error(result)

def create_task_item(summary: str, due: str='') -> str:
    title = (summary or '').strip()
    if not title:
        return '待办标题不能为空。'
    if len(title) > 200:
        title = title[:200]
    args = ['task', '+create', '--summary', title, '--as', 'user']
    due_text = (due or '').strip()
    if due_text:
        args.extend(['--due', due_text])
    payload = run_lark(args, as_identity='user')
    if payload.get('ok'):
        return f'已创建飞书待办：{title}'
    return format_lark_error(payload)
