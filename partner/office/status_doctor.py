from __future__ import annotations

from datetime import datetime, timedelta, timezone
from ..core.ids import display_name, identity_hint, identity_ready, config_path
from ..core.lark import run_lark
from ..compose.hermes_setup import ensure_profile, profile_status_line
from ..compose.llm import hermes_available
from .messaging import _probe_ok
from .schedule import schedule_status_lines

CN_TZ = timezone(timedelta(hours=8))

def status_text() -> str:
    who_user = run_lark(['whoami'], as_identity='user')
    who_bot = run_lark(['whoami'], as_identity='bot')
    doctor = run_lark(['doctor'], as_identity=None)
    user = who_user.get('onBehalfOf') or {}
    identity_line = f'- 伙伴身份：{display_name()}（{config_path()}）' if identity_ready() else f'- 伙伴身份：未配置\n{identity_hint()}'
    lines = ['飞书工作伙伴状态', identity_line, f"- 应用：{who_user.get('appId') or who_bot.get('appId')}", f"- 用户：{user.get('userName') or who_user.get('identity')} ({user.get('openId') or ''})", f"- 用户 token：{who_user.get('tokenStatus')}", f"- 机器人：{who_bot.get('identity')} / {who_bot.get('tokenStatus')}", f"- doctor：{('ok' if doctor.get('ok') else doctor)}", f"- 飞书内写回复：{('本机 Hermes（单聊隔离档案+飞书 MCP 多轮；无 yolo；群里只润色）' if hermes_available() else '模板（未找到 hermes）')}", f'- {profile_status_line()}']
    lines.extend((f'- {item}' for item in schedule_status_lines()))
    return '\n'.join(lines)

def doctor_text() -> str:
    ensure_profile()
    from ..runtime.agent.settings import agent_config_status_text, ensure_agent_config

    ensure_agent_config()
    chunks = [status_text(), '', agent_config_status_text()]
    if not identity_ready():
        chunks.extend(['', '身份闸：FAIL', identity_hint()])
    chunks.extend(['', '能力探针：'])
    now = datetime.now(CN_TZ)
    message_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    message_end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    probes = [('待办', ['task', '+get-my-tasks', '--complete=false', '--page-limit', '1'], 'user'), ('日程', ['calendar', '+agenda'], 'user'), ('文档搜索', ['docs', '+search', '--query', 'test', '--page-size', '1'], 'user'), ('知识库', ['wiki', '+space-list'], 'user'), ('会话', ['im', '+chat-list'], 'user'), ('消息回顾', ['im', '+messages-search', '--query', '', '--start', message_start.isoformat(), '--end', message_end.isoformat(), '--page-size', '1', '--no-reactions'], 'user'), ('会议纪要', ['minutes', '+search', '--participant-ids', 'me', '--page-size', '1'], 'user'), ('审批', ['approval', 'tasks', 'query', '--topic', '1', '--page-size', '1'], 'user'), ('收消息事件', ['event', 'consume', 'im.message.receive_v1', '--dry-run'], 'bot'), ('卡片按钮事件', ['event', 'consume', 'card.action.trigger', '--dry-run'], 'bot')]
    missing: list[str] = []
    for name, args, ident in probes:
        payload = run_lark(args, as_identity=ident)
        ok, detail = _probe_ok(payload)
        chunks.append(f"- {name}：{('OK' if ok else 'FAIL')}")
        if not ok:
            err = payload.get('error') if isinstance(payload.get('error'), dict) else {}
            scopes = err.get('missing_scopes') or []
            if scopes:
                missing.extend((str(s) for s in scopes))
            elif detail:
                chunks.append('  ' + detail[:240])
            else:
                chunks.append('  ' + (err.get('message') or str(payload))[:200])
            if name == '卡片按钮事件':
                chunks.append('  文字「某群那条已处理」已可用；按钮要在开放平台订阅 card.action.trigger。')
    if missing:
        uniq = list(dict.fromkeys(missing))
        chunks.append('')
        chunks.append('缺 OAuth 权限（需你本人浏览器授权，不能代登）：')
        chunks.append('  ' + ' '.join(uniq))
        chunks.append(f'''`lark-cli auth login --scope "{' '.join(uniq)}"`''')
    return '\n'.join(chunks)
