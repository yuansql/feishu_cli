from __future__ import annotations

"""Public office actions + IM dispatch facade.

Domain implementations live under partner.office.*; this module re-exports them
so existing `partner.actions` call sites and test patches stay stable.
"""

from .office.calendar_views import (
    _week_bounds,
    _day_bounds,
    _agenda_range,
    _open_followup_text,
    today_text,
    tomorrow_text,
    _next_week_bounds,
    weekly_text,
    _created_doc_link,
    write_weekly_text,
)
from .office.status_doctor import status_text, doctor_text
from .office.tasks_io import (
    task_rows,
    match_tasks,
    tasks_bundle,
    tasks_text,
    complete_task_text,
    create_task_item,
)
from .office.docs_io import (
    write_doc_text,
    docs_search_text,
    search_text,
    read_text,
    minutes_text,
    approval_text,
    analyze_result,
    analyze_text,
)
from .office.messaging import (
    chats_text,
    chat_history_text,
    who_profile_text,
    _message_body,
    _message_who,
    _recent_messages,
    _person_open_id,
    _sender_messages,
    _format_msg_line,
    person_text,
    inbox_text,
    _with_inbox,
    send_text,
    send_card,
    _task_lines,
    send_style_card,
    add_reaction,
    _probe_ok,
)

from datetime import datetime, timedelta, timezone
import re
from typing import Any

from .compose.formatters import (
    help_text,
    format_clarify,
    format_lark_error,
    looks_like_clarify,
    material_pairs,
)
from .office.brief import brief_text
from .ops.aily import alignment_text
from .runtime.artifact import (
    apply_document_edit,
    artifact_turn,
    close_artifact,
    load_artifact,
    observe_document,
    prepare_document_edit,
)
from .routing.intents import Intent, looks_like_bare_search, parse_intent
from .compose.llm import (
    classify_intent,
    parse_fetch,
    rewrite_human,
    rewrite_partner,
    should_compose,
    should_partner,
    hermes_available,
    hermes_partner_turn,
    _is_usable_reply,
)
from .routing.resolved import ensure_pending_snapshot, resolve_text
from .core.session import load_turn, looks_like_followup, pick_index, save_turn
from .runtime.planner import plan_text
from .office.recap import looks_like_recap_followup, today_recap
from .runtime.runner import (
    active_task_for_chat,
    cancel_task,
    confirm_writes,
    continue_task,
    start_task,
    status_task,
)
from .core.inbox import recent_items

CN_TZ = timezone(timedelta(hours=8))

def _facts_for(intent: Intent) -> str:
    if intent.action == "aily":
        return alignment_text()
    if intent.action == "help":
        return help_text()
    if intent.action == "today":
        return today_text()
    if intent.action == "brief":
        return brief_text()
    if intent.action == "tomorrow":
        return tomorrow_text()
    if intent.action == "weekly":
        return weekly_text(focus=intent.query if intent.query == "next" else "")
    if intent.action == "write_weekly":
        return write_weekly_text(intent.query)
    if intent.action == "identity":
        from .core.ids import USER_NAMES, display_name

        name = display_name() or (USER_NAMES[0] if USER_NAMES else "")
        if not name:
            return "还没记住你的名字。请本机跑一次 `feishu setup --name 你的名字`。"
        return (
            f"你是{name}。"
            "我是你的飞书工作伙伴（本机 feishu CLI），只服务你的单聊与已授权办公能力；"
            "不会把你认成文档搜索结果里的别的主题。"
        )
    if intent.action == "who":
        return who_profile_text(intent.query)
    if intent.action == "person":
        return person_text(intent.query)
    if intent.action == "tasks":
        return tasks_text()
    if intent.action == "minutes":
        return minutes_text()
    if intent.action == "approval":
        return approval_text()
    if intent.action == "search":
        return search_text(intent.query)
    if intent.action == "read":
        return read_text(intent.query)
    if intent.action == "chats":
        return chats_text(intent.query)
    if intent.action == "chat_history":
        return chat_history_text(intent.query)
    if intent.action == "inbox":
        return inbox_text()
    if intent.action == "digest":
        from .office.followup import digest_text

        return digest_text()
    if intent.action == "weekly_tasks":
        from .office.bitable import weekly_tasks_text

        return weekly_tasks_text()
    if intent.action == "memory":
        from .office.memory import memory_context_for_goal

        return memory_context_for_goal(intent.query or "工作") or "（本地记忆暂无相关片段）"
    if intent.action == "knowledge":
        from .office.knowledge import knowledge_answer

        return knowledge_answer(intent.query or "")
    if intent.action == "today_recap":
        return today_recap(intent.query or "我今天干了什么").text
    if intent.action == "plan":
        return plan_text(intent.query, today_text())
    if intent.action == "send":
        return send_text(intent.chat_id, intent.query)
    query = (intent.query or "").strip()
    if query:
        return analyze_text(query)
    return help_text()


def partner_reply(user_text: str, facts: str, intent: Intent | None = None) -> str:
    # ponytail: Hermes never gets a shell; extra facts come from our allowlist only.
    gathered = facts
    asked = user_text or (intent.query if intent else "") or (intent.action if intent else "")
    for _ in range(4):
        spoken = rewrite_partner(asked, gathered)
        fetch = parse_fetch(spoken)
        if fetch:
            name, query = fetch
            extra = _facts_for(Intent(action=name, query=query))
            gathered = f"{gathered}\n\n【补充·{name}】\n{extra}"
            continue
        if spoken and _is_usable_reply(spoken, limit=2500):
            return spoken
        return gathered
    spoken = rewrite_human(asked, gathered)
    if spoken and _is_usable_reply(spoken, limit=2500):
        return spoken
    return gathered


def _continue_turn(prev: dict[str, Any], raw: str) -> str:
    pairs = [
        (str(item[0]), str(item[1]))
        for item in (prev.get("pairs") or [])
        if isinstance(item, (list, tuple)) and len(item) >= 2
    ]
    idx = pick_index(raw)
    if idx is not None and 1 <= idx <= len(pairs):
        title, url = pairs[idx - 1]
        if url:
            return read_text(url)
        return f"第{idx}份《{title}》没有链接，回别的序号或说「搜 标题」。"
    orig = parse_intent(str(prev.get("query") or ""))
    if orig.action not in {"unknown", "help", ""}:
        body = _facts_for(orig)
        return "刚才那句我理解成搜文档了。按你第一句：\n\n" + body
    last_action = str(prev.get("action") or "")
    if last_action and last_action not in {"unknown", "help"}:
        return _facts_for(Intent(action=last_action, query=str(prev.get("query") or "")))
    if pairs:
        lines = ["接着刚才那几份："]
        for index, (title, _url) in enumerate(pairs, 1):
            lines.append(f"{index}. {title}")
        lines.append("回序号我读那一份。若要待办或今天，直接说「待办」或「今天」。")
        return "\n".join(lines)
    return "上一句我没留住。再说一次：今天、待办，还是某份文档？"


def _unknown_nudge(prev: dict[str, Any] | None) -> str:
    extra = ""
    if prev and prev.get("query"):
        extra = f"你上一句在问「{prev.get('query')}」。"
    return (extra + "直接说：今天 / 待办 / 删掉某条待办 / 搜 关键词。").strip()


def _task_done_reply_suffix(raw: str) -> str:
    matched = re.search(r"\s+(?:回复|回)\s+(.+)$", raw or "")
    if not matched:
        return ""
    return re.sub(r"\s+", " ", matched.group(1)).strip(" ：:，,")


def _today_recap_reply(
    chat_id: str,
    asked: str,
    *,
    previous: dict[str, Any] | None = None,
    refresh: bool = True,
) -> str:
    recap = today_recap(
        asked,
        previous_context=str((previous or {}).get("context") or ""),
        refresh=refresh,
    )
    if chat_id:
        save_turn(
            chat_id,
            kind="action",
            query=str((previous or {}).get("query") or asked),
            action="today_recap",
            context=recap.context,
            result=recap.text,
        )
    return recap.text


def dispatch(
    intent: Intent,
    *,
    user_text: str = "",
    channel: str = "p2p",
    chat_id: str = "",
    force_facts: bool = False,
) -> str:
    asked = user_text or (intent.query or "").strip() or intent.action
    artifact = (
        load_artifact(chat_id)
        if chat_id and channel == "p2p" and not force_facts
        else None
    )
    artifact_action = artifact_turn(artifact, asked)
    if artifact_action == "close":
        return close_artifact(chat_id)
    if artifact_action == "confirm":
        return apply_document_edit(chat_id)
    if artifact_action == "revise":
        return prepare_document_edit(
            chat_id,
            asked,
            work_facts=weekly_text(),
        )
    if intent.action == "resolve":
        ensure_pending_snapshot()
        return resolve_text(intent.query or user_text)
    if intent.action == "today_recap":
        return _today_recap_reply(
            chat_id,
            asked,
        )
    if intent.action == "who":
        materials = who_profile_text(intent.query)
        if channel == "p2p" and not force_facts and hermes_available():
            spoken = hermes_partner_turn(asked, seed_facts=materials)
            if spoken:
                if chat_id:
                    save_turn(chat_id, kind="action", query=asked, action="who")
                return spoken
        # No Hermes: return search materials without the LLM instruction footer.
        trimmed = materials.rsplit("请根据以上材料", 1)[0].strip()
        if chat_id:
            save_turn(chat_id, kind="action", query=asked, action="who")
        return trimmed or materials
    if (
        intent.action == "write_doc"
        and chat_id
        and channel == "p2p"
        and not force_facts
        and "feishu.cn/" in asked
        and any(word in asked for word in ("写到", "填到", "改", "补充", "更新"))
    ):
        return prepare_document_edit(
            chat_id,
            asked,
            work_facts=weekly_text(),
        )
    if intent.action == "plan":
        goal = (intent.query or asked).strip()
        # Hermes-first for complex plan; TaskRunner/LangGraph remains fallback.
        if chat_id and goal and not force_facts and channel == "p2p":
            if hermes_available():
                spoken = hermes_partner_turn(
                    f"请根据飞书材料帮我拆解并推进这个目标（只读、不要写入）：{goal}",
                    seed_facts=today_text()[:2000],
                )
                if spoken:
                    save_turn(
                        chat_id,
                        kind="action",
                        query=asked,
                        action="plan",
                    )
                    return spoken
            return start_task(goal, chat_id, background=True)
        return plan_text(goal, today_text(), allow_llm=not force_facts)
    if intent.action == "task_continue":
        return continue_task(chat_id)
    if intent.action == "task_status":
        return status_task(chat_id)
    if intent.action == "task_confirm":
        return confirm_writes(chat_id)
    if intent.action == "task_cancel":
        return cancel_task(chat_id)
    from .routing.mode_router import route_request
    from .runtime.workflow import run_workflow

    route = route_request(asked, intent)
    if route.mode == "workflow" and route.workflow_id and not force_facts:
        return run_workflow(route.workflow_id, goal=asked, chat_id=chat_id)
    if route.mode == "knowledge" and route.query and not force_facts:
        from .office.knowledge import knowledge_answer

        return knowledge_answer(route.query, chat_id=chat_id)
    prev = load_turn(chat_id) if chat_id else None
    if chat_id and asked.strip() == "继续" and active_task_for_chat(chat_id):
        return continue_task(chat_id)
    if (
        prev
        and prev.get("action") == "today_recap"
        and looks_like_recap_followup(asked)
    ):
        return _today_recap_reply(
            chat_id,
            asked,
            previous=prev,
            refresh=False,
        )
    if intent.action == "task_done":
        reply = complete_task_text(
            intent.query,
            session_items=list((prev or {}).get("items") or []),
        )
        suffix = _task_done_reply_suffix(asked)
        if suffix and reply.startswith("已勾完成"):
            reply += f"\n\n{suffix}"
        if chat_id:
            save_turn(
                chat_id,
                kind="action",
                query=asked,
                action="task_done",
                items=list((prev or {}).get("items") or []),
            )
        return reply
    if prev and looks_like_followup(asked):
        steal_weekly = intent.action == "weekly" and asked.strip() in {
            "继续",
            "再写",
            "改人话",
            "写人话",
            "像人写",
        }
        if intent.action == "unknown" or (
            steal_weekly and prev.get("action") not in {"weekly", "write_weekly"}
        ):
            reply = _continue_turn(prev, asked)
            save_turn(
                chat_id,
                kind=str(prev.get("kind") or "action"),
                query=str(prev.get("query") or asked),
                action=str(prev.get("action") or intent.action),
                pairs=prev.get("pairs") or [],
                items=list(prev.get("items") or []),
            )
            return reply
    if intent.action == "unknown" and looks_like_followup(asked) and not prev:
        return "上一轮我没接上。直接说「今天」「待办」，或「搜 关键词」。"
    pairs: list[tuple[str, str]] = []
    items: list[dict[str, str]] = []
    if intent.action == "unknown" and channel == "p2p" and not force_facts:
        refined = classify_intent(asked)
        if refined is not None and refined.action not in {"unknown", ""}:
            intent = refined
    if intent.action == "today_recap":
        return _today_recap_reply(
            chat_id,
            asked,
        )
    if intent.action == "unknown":
        # Complex unknown → Hermes; short keyword unknown → doc search.
        if not looks_like_bare_search(asked):
            if channel == "p2p" and not force_facts and hermes_available():
                spoken = hermes_partner_turn(asked)
                if spoken:
                    if chat_id:
                        save_turn(
                            chat_id,
                            kind="action",
                            query=asked,
                            action="hermes",
                        )
                    return spoken
            return _unknown_nudge(prev)
        facts, pairs = analyze_result((intent.query or asked).strip())
        kind = "clarify" if looks_like_clarify(facts) else "action"
        if chat_id:
            save_turn(chat_id, kind=kind, query=asked, action="unknown", pairs=pairs)
        if looks_like_clarify(facts):
            return facts
    elif intent.action == "tasks":
        facts, items = tasks_bundle()
        if chat_id:
            save_turn(chat_id, kind="action", query=asked, action="tasks", items=items)
    else:
        facts = _facts_for(intent)
        if (
            intent.action == "read"
            and chat_id
            and channel == "p2p"
            and not force_facts
            and facts
            and not any(
                marker in facts
                for marker in ("飞书权威失败", "文档是空的", "读不到正文", "缺权限")
            )
        ):
            observe_document(chat_id, intent.query, facts)
        if chat_id:
            save_turn(chat_id, kind="action", query=asked, action=intent.action, pairs=[])
    if force_facts:
        return facts
    if should_partner(channel, intent.action):
        return partner_reply(asked, facts, intent) or facts
    if not should_compose(intent.action):
        return facts
    spoken = rewrite_human(asked, facts)
    return spoken or facts


