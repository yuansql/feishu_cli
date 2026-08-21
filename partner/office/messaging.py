from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any
from ..compose.formatters import _chat_tokens, _items, format_chats, format_lark_error
from ..core.inbox import recent_items
from ..routing.intents import Intent
from ..core.lark import run_lark
from .watch import format_inbox_digest
from ..core.session import save_turn

CN_TZ = timezone(timedelta(hours=8))

def chats_text(query: str='') -> str:
    q = (query or '').strip()
    if q:
        seen: set[str] = set()
        merged: list[dict] = []
        for tok in _chat_tokens(q)[:5]:
            payload = run_lark(['im', '+chat-search', '--query', tok, '--disable-search-by-user', '--page-size', '20'], as_identity='user')
            for chat in _items(payload, 'chats', 'items'):
                if not isinstance(chat, dict):
                    continue
                cid = str(chat.get('chat_id') or '')
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                merged.append(chat)
        if merged:
            return format_chats({'ok': True, 'data': {'chats': merged}}, query=q)
    payload = run_lark(['im', '+chat-list', '--types=p2p,group', '--page-all', '--page-size', '50', '--sort', 'active_time'], as_identity='user')
    return format_chats(payload, query=q)

def _message_body(msg: dict[str, Any]) -> str:
    content = msg.get('content')
    if isinstance(content, dict):
        text = content.get('text') or ''
    else:
        text = msg.get('text') or content or ''
    return str(text or '').replace('\n', ' ').strip()

def _message_who(msg: dict[str, Any]) -> str:
    sender = msg.get('sender')
    if isinstance(sender, dict):
        return str(sender.get('name') or sender.get('sender_name') or '').strip()
    return str(msg.get('sender_name') or '').strip()

def _recent_messages(chat_id: str) -> list[dict[str, Any]]:
    if not chat_id:
        return []
    payload = run_lark(['im', '+chat-messages-list', '--chat-id', chat_id, '--order', 'desc', '--page-size', '15', '--no-reactions'], as_identity='user')
    if payload.get('ok') is False:
        return []
    hits = payload.get('data')
    if isinstance(hits, dict):
        hits = hits.get('messages') or hits.get('items') or []
    if not isinstance(hits, list):
        return []
    return [item for item in hits if isinstance(item, dict)]

def _person_open_id(name: str, chats: list[dict[str, Any]]) -> str:
    for chat in chats[:4]:
        cid = str(chat.get('chat_id') or '')
        if not cid:
            continue
        payload = run_lark(['im', '+chat-members-list', '--chat-id', cid, '--page-size', '50'], as_identity='user')
        data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
        users = data.get('users') if isinstance(data.get('users'), list) else []
        for user in users:
            if not isinstance(user, dict):
                continue
            if name not in str(user.get('name') or ''):
                continue
            oid = str(user.get('member_id') or user.get('open_id') or '')
            if oid.startswith('ou_'):
                return oid
    return ''

def _sender_messages(open_id: str) -> list[dict[str, Any]]:
    if not open_id:
        return []
    payload = run_lark(['im', '+messages-search', '--sender', open_id, '--page-size', '10', '--no-reactions'], as_identity='user')
    if payload.get('ok') is False:
        return []
    hits = payload.get('data')
    if isinstance(hits, dict):
        hits = hits.get('messages') or hits.get('items') or []
    if not isinstance(hits, list):
        return []
    return [item for item in hits if isinstance(item, dict)]

def _format_msg_line(msg: dict[str, Any], name: str) -> str:
    who = _message_who(msg) or name
    body = _message_body(msg)
    if not body:
        return ''
    if body.startswith('![Image]'):
        body = '（图片）'
    when = str(msg.get('create_time') or '')
    if len(when) >= 16:
        when = when[5:16].replace('T', ' ')
    clip = body[:160] + ('…' if len(body) > 160 else '')
    if when:
        return f'- {when} {who}：{clip}'
    return f'- {who}：{clip}'

def person_text(query: str) -> str:
    """Look up someone's recent IM replies. Never searches docs."""
    name = (query or '').strip()
    if not name:
        return '说一下是谁，我去翻最近怎么回的。'
    lines = [f'【{name}最近怎么说】']
    inbox_hits = [item for item in recent_items(days=14) if name in str(item.get('sender_name') or '') or name in str(item.get('text') or '') or name in str(item.get('chat_name') or '')]
    if inbox_hits:
        lines.append('收件箱里：')
        for item in inbox_hits[:5]:
            who = str(item.get('sender_name') or name).strip()
            where = str(item.get('chat_name') or '群').strip()
            text = str(item.get('text') or '').replace('\n', ' ').strip()
            if len(text) > 120:
                text = text[:120] + '…'
            lines.append(f'- {who}（{where}）：{text}' if text else f'- {who}（{where}）')
    payload = run_lark(['im', '+chat-search', '--query', name, '--page-size', '20'], as_identity='user')
    chats = [chat for chat in _items(payload, 'chats', 'items') if isinstance(chat, dict)]
    if payload.get('ok') is False and (not chats):
        err = format_lark_error(payload)
        if len(lines) == 1:
            return err
        lines.append(err)
        return '\n'.join(lines)
    oid = _person_open_id(name, chats)
    shown = [_format_msg_line(msg, name) for msg in _sender_messages(oid)]
    shown = [line for line in shown if line][:8]
    if shown:
        lines.append('最近发的：')
        lines.extend(shown)
        return '\n'.join(lines)
    found_msg = False
    for chat in chats[:3]:
        cid = str(chat.get('chat_id') or '')
        cname = str(chat.get('name') or '会话').strip()
        mode = str(chat.get('chat_mode') or chat.get('chat_type') or '').lower()
        local: list[str] = []
        for msg in _recent_messages(cid):
            who = _message_who(msg)
            sid = ''
            sender = msg.get('sender')
            if isinstance(sender, dict):
                sid = str(sender.get('id') or '')
            if not ('p2p' in mode or name in who or name in cname or (oid and sid == oid)):
                continue
            line = _format_msg_line(msg, name)
            if line:
                local.append(line)
            if len(local) >= 8:
                break
        if not local:
            continue
        found_msg = True
        lines.append(f'{cname}：')
        lines.extend(local)
    if found_msg or inbox_hits:
        return '\n'.join(lines)
    if chats:
        names = '、'.join((str(chat.get('name') or '').strip() for chat in chats[:5] if chat.get('name')))
        return f'找到和「{name}」相关的会话（{names}），但最近没有可读的文字回复。'
    return f'没找到「{name}」的会话或最近回复。我只能看你身份下搜得到的群/单聊；机器人不在的群看不见。也可以说「谁找我」。'

def inbox_text() -> str:
    return format_inbox_digest(recent_items(days=7))

def _with_inbox(body: str) -> str:
    items = recent_items(days=7)
    if not items:
        return body
    return body.rstrip() + '\n\n【有人找你】\n' + format_inbox_digest(items)

def send_text(chat_id: str, text: str, *, as_identity: str='bot') -> str:
    if not chat_id or not text:
        return '用法：发 oc_xxx 文本'
    payload = run_lark(['im', '+messages-send', '--chat-id', chat_id, '--text', text], as_identity=as_identity)
    if payload.get('ok'):
        return '已发送。'
    return format_lark_error(payload)

def send_card(chat_id: str, card: dict[str, Any], *, as_identity: str='bot') -> str:
    if not chat_id or not card:
        return '卡片缺少会话或内容'
    payload = run_lark(['im', '+messages-send', '--chat-id', chat_id, '--msg-type', 'interactive', '--content', json.dumps(card, ensure_ascii=False)], as_identity=as_identity)
    if payload.get('ok'):
        return '已发送。'
    return format_lark_error(payload)

def _task_lines(payload: dict[str, Any]) -> list[str]:
    if payload.get('ok') is False:
        return []
    lines: list[str] = []
    for item in _items(payload, 'items', 'tasks')[:20]:
        if not isinstance(item, dict):
            continue
        summary = str(item.get('summary') or item.get('title') or '(无标题)')
        due = item.get('due_at') or item.get('due') or ''
        due_s = str(due)[:10] if due else ''
        lines.append(f'{summary}（{due_s}）' if due_s else summary)
    return lines

def send_style_card(intent: Intent, chat_id: str) -> bool:
    """P2P 简报/今天/明天走 JSON 2.0 卡；失败则交给 dispatch 发文字。"""
    if not chat_id or intent.action not in {'brief', 'today', 'tomorrow'}:
        return False
    card: dict[str, Any] | None = None
    if intent.action == 'brief':
        from .brief import collect_brief
        from .brief_card import brief_card
        card = brief_card(collect_brief())
    else:
        from .brief import agenda_entries
        from .brief_card import day_work_card
        from .followup import followups_for_command
        offset = 1 if intent.action == 'tomorrow' else 0
        start, end = _day_bounds(offset)
        entries = agenda_entries(_agenda_range(start, end))
        tasks = run_lark(['task', '+get-my-tasks', '--complete=false', '--page-limit', '20'], as_identity='user')
        card = day_work_card(kind=intent.action, day=start.date(), entries=entries, followups=followups_for_command(), task_lines=_task_lines(tasks))
    result = send_card(chat_id, card)
    if result != '已发送。':
        return False
    save_turn(chat_id, kind='action', query=intent.action, action=intent.action, pairs=[])
    return True

def add_reaction(message_id: str, emoji: str='OnIt') -> str:
    if not message_id:
        return 'skip'
    payload = run_lark(['im', 'reactions', 'create', '--message-id', message_id, '--data', json.dumps({'reaction_type': {'emoji_type': emoji}}, ensure_ascii=False)], as_identity='bot')
    if payload.get('ok'):
        return 'ok'
    return format_lark_error(payload)

def _probe_ok(payload: dict[str, Any]) -> tuple[bool, str]:
    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    decision = data.get('decision') if isinstance(data, dict) else {}
    if isinstance(decision, dict) and decision.get('status') == 'blocked':
        bits: list[str] = []
        for pre in decision.get('preconditions') or []:
            if isinstance(pre, dict) and pre.get('status') == 'blocked':
                bits.append(str(pre.get('detail') or pre.get('hint') or ''))
        return (False, ' '.join((bit for bit in bits if bit)))
    if payload.get('ok'):
        return (True, '')
    err = payload.get('error') if isinstance(payload.get('error'), dict) else {}
    return (False, str(err.get('message') or payload)[:200])
