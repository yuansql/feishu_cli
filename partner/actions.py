from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any

from .formatters import (
    HELP_TEXT,
    _chat_tokens,
    _items,
    document_markdown,
    format_agenda,
    format_approvals,
    format_chats,
    format_clarify,
    format_doc,
    format_docs_search,
    format_lark_error,
    format_minutes,
    format_tasks,
    format_today,
    format_topic_brief,
    format_weekly_from_doc,
    format_weekly_human,
    format_wiki_spaces,
    looks_like_clarify,
    material_pairs,
    pick_personal_weekly,
)
from .brief import brief_text
from .ids import WEEKLY_QUERY
from .inbox import recent_items
from .intents import Intent, parse_intent
from .lark import run_lark
from .hermes_setup import ensure_profile, profile_status_line
from .llm import (
    hermes_available,
    parse_fetch,
    rewrite_human,
    rewrite_partner,
    should_compose,
    should_partner,
)
from .schedule import schedule_status_lines
from .watch import format_inbox_digest
from .resolved import ensure_pending_snapshot, resolve_text
from .session import load_turn, looks_like_followup, pick_index, save_turn

CN_TZ = timezone(timedelta(hours=8))


def _week_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(CN_TZ)
    start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(days=7) - timedelta(seconds=1)
    return start, end


def _day_bounds(offset_days: int, now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(CN_TZ)
    day = (now + timedelta(days=offset_days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return day, day.replace(hour=23, minute=59, second=59)


def _agenda_range(start: datetime, end: datetime) -> dict[str, Any]:
    return run_lark(
        [
            "calendar",
            "+agenda",
            "--start",
            start.isoformat(),
            "--end",
            end.isoformat(),
        ],
        as_identity="user",
    )


def status_text() -> str:
    who_user = run_lark(["whoami"], as_identity="user")
    who_bot = run_lark(["whoami"], as_identity="bot")
    doctor = run_lark(["doctor"], as_identity=None)
    user = who_user.get("onBehalfOf") or {}
    lines = [
        "飞书工作伙伴状态",
        f"- 应用：{who_user.get('appId') or who_bot.get('appId')}",
        f"- 用户：{user.get('userName') or who_user.get('identity')} ({user.get('openId') or ''})",
        f"- 用户 token：{who_user.get('tokenStatus')}",
        f"- 机器人：{who_bot.get('identity')} / {who_bot.get('tokenStatus')}",
        f"- doctor：{'ok' if doctor.get('ok') else doctor}",
        f"- 飞书内写回复：{'本机 Hermes（单聊隔离档案+飞书 MCP，无 yolo；群里只润色）' if hermes_available() else '模板（未找到 hermes）'}",
        f"- {profile_status_line()}",
    ]
    lines.extend(f"- {item}" for item in schedule_status_lines())
    return "\n".join(lines)


def doctor_text() -> str:
    ensure_profile()
    chunks = [status_text(), "", "能力探针："]
    probes = [
        ("待办", ["task", "+get-my-tasks", "--complete=false", "--page-limit", "1"], "user"),
        ("日程", ["calendar", "+agenda"], "user"),
        ("文档搜索", ["docs", "+search", "--query", "test", "--page-size", "1"], "user"),
        ("知识库", ["wiki", "+space-list"], "user"),
        ("会话", ["im", "+chat-list"], "user"),
        ("会议纪要", ["minutes", "+search", "--participant-ids", "me", "--page-size", "1"], "user"),
        ("审批", ["approval", "tasks", "query", "--topic", "1", "--page-size", "1"], "user"),
        (
            "收消息事件",
            ["event", "consume", "im.message.receive_v1", "--dry-run"],
            "bot",
        ),
        (
            "卡片按钮事件",
            ["event", "consume", "card.action.trigger", "--dry-run"],
            "bot",
        ),
    ]
    missing: list[str] = []
    for name, args, ident in probes:
        payload = run_lark(args, as_identity=ident)
        ok, detail = _probe_ok(payload)
        chunks.append(f"- {name}：{'OK' if ok else 'FAIL'}")
        if not ok:
            err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            scopes = err.get("missing_scopes") or []
            if scopes:
                missing.extend(str(s) for s in scopes)
            elif detail:
                chunks.append("  " + detail[:240])
            else:
                chunks.append("  " + (err.get("message") or str(payload))[:200])
            if name == "卡片按钮事件":
                chunks.append(
                    "  文字「某群那条已处理」已可用；按钮要在开放平台订阅 card.action.trigger。"
                )
    if missing:
        uniq = list(dict.fromkeys(missing))
        chunks.append("")
        chunks.append("缺 OAuth 权限（需你本人浏览器授权，不能代登）：")
        chunks.append("  " + " ".join(uniq))
        chunks.append(
            f'`lark-cli auth login --scope "{" ".join(uniq)}"`'
        )
    return "\n".join(chunks)


def today_text() -> str:
    agenda = run_lark(["calendar", "+agenda"], as_identity="user")
    tasks = run_lark(
        ["task", "+get-my-tasks", "--complete=false", "--page-limit", "20"],
        as_identity="user",
    )
    return format_today(format_agenda(agenda), format_tasks(tasks))


def tomorrow_text() -> str:
    start, end = _day_bounds(1)
    agenda = _agenda_range(start, end)
    return format_agenda(agenda, heading="明日日程", empty="明天没有日程。")


def _next_week_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    start, end = _week_bounds(now)
    return start + timedelta(days=7), end + timedelta(days=7)


def weekly_text(focus: str = "") -> str:
    start, end = _week_bounds()
    agenda = _agenda_range(start, end)
    tasks = run_lark(
        ["task", "+get-my-tasks", "--complete=false", "--page-limit", "20"],
        as_identity="user",
    )
    docs = run_lark(
        ["docs", "+search", "--query", WEEKLY_QUERY, "--page-size", "5"],
        as_identity="user",
    )
    title, url = pick_personal_weekly(docs)
    if not url:
        docs = run_lark(
            ["docs", "+search", "--query", "周报", "--page-size", "5"],
            as_identity="user",
        )
        title, url = pick_personal_weekly(docs)
    if url:
        fetched = run_lark(
            [
                "docs",
                "+fetch",
                "--doc",
                url,
                "--doc-format",
                "markdown",
                "--detail",
                "simple",
            ],
            as_identity="user",
        )
        shaped = format_weekly_from_doc(
            document_markdown(fetched),
            start,
            end,
            tasks,
            source_title=title,
            source_url=url,
            focus=focus,
        )
        if shaped:
            return _with_inbox(shaped)
    if focus == "next":
        nstart, nend = _next_week_bounds()
        next_agenda = _agenda_range(nstart, nend)
        return _with_inbox(
            format_agenda(
                next_agenda,
                heading="下周日程",
                empty="下周日历还没记下会，周报里也没有下周计划。",
            )
        )
    return _with_inbox(format_weekly_human(start, end, agenda, tasks, docs))


def _created_doc_link(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return ""
    doc = data.get("document") if isinstance(data.get("document"), dict) else data
    if not isinstance(doc, dict):
        return ""
    return str(
        doc.get("url")
        or data.get("url")
        or doc.get("doc_url")
        or ""
    )


def write_weekly_text() -> str:
    body = weekly_text()
    start, end = _week_bounds()
    title = f"周报 {start.date().isoformat()} ~ {end.date().isoformat()}"
    created = run_lark(
        [
            "docs",
            "+create",
            "--title",
            title,
            "--doc-format",
            "markdown",
            "--content",
            body,
        ],
        as_identity="user",
    )
    if created.get("ok"):
        link = _created_doc_link(created)
        extra = f"\n{link}" if link else ""
        return f"已生成云文档《{title}》。{extra}\n\n" + body
    return format_lark_error(created) + "\n\n先把摘要放这儿：\n" + body


def tasks_text() -> str:
    payload = run_lark(
        ["task", "+get-my-tasks", "--complete=false", "--page-limit", "20"],
        as_identity="user",
    )
    return format_tasks(payload)


def analyze_result(query: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (reply, title/url pairs). Several hits → ask which. Never dump search lists."""
    if not query:
        return HELP_TEXT, []
    docs = run_lark(
        ["docs", "+search", "--query", query, "--page-size", "5"],
        as_identity="user",
    )
    if docs.get("ok") is False:
        return format_clarify(query, []), []
    pairs = material_pairs(docs, limit=4)
    if len(pairs) != 1 or not pairs[0][1]:
        return format_clarify(query, [title for title, _url in pairs]), pairs
    title, url = pairs[0]
    fetched = run_lark(
        [
            "docs",
            "+fetch",
            "--doc",
            url,
            "--doc-format",
            "markdown",
            "--detail",
            "simple",
        ],
        as_identity="user",
    )
    return format_topic_brief(query, title, url, document_markdown(fetched)), pairs


def analyze_text(query: str) -> str:
    text, _pairs = analyze_result(query)
    return text


def minutes_text() -> str:
    start = (datetime.now(CN_TZ) - timedelta(days=14)).date().isoformat()
    payload = run_lark(
        [
            "minutes",
            "+search",
            "--participant-ids",
            "me",
            "--start",
            start,
            "--page-size",
            "8",
        ],
        as_identity="user",
    )
    return format_minutes(payload)


def approval_text() -> str:
    payload = run_lark(
        ["approval", "tasks", "query", "--topic", "1", "--page-size", "15"],
        as_identity="user",
    )
    return format_approvals(payload)


def search_text(query: str) -> str:
    if not query:
        return "请给出关键词，例如：搜 周报"
    docs = run_lark(
        ["docs", "+search", "--query", query, "--page-size", "10"],
        as_identity="user",
    )
    if docs.get("ok"):
        return format_docs_search(docs, query=query)
    wiki = run_lark(["wiki", "+space-list"], as_identity="user")
    fallback = format_wiki_spaces(wiki, query=query)
    return (
        format_lark_error(docs)
        + "\n\n已降级为知识库空间名筛选（不是全文检索）：\n"
        + fallback
    )


def read_text(doc: str) -> str:
    if not doc:
        return "请给出文档链接或 token，例如：读 https://..."
    payload = run_lark(
        ["docs", "+fetch", "--doc", doc, "--doc-format", "markdown", "--detail", "simple"],
        as_identity="user",
    )
    return format_doc(payload)


def chats_text(query: str = "") -> str:
    q = (query or "").strip()
    if q:
        seen: set[str] = set()
        merged: list[dict] = []
        for tok in _chat_tokens(q)[:5]:
            payload = run_lark(
                [
                    "im",
                    "+chat-search",
                    "--query",
                    tok,
                    "--disable-search-by-user",
                    "--page-size",
                    "20",
                ],
                as_identity="user",
            )
            for chat in _items(payload, "chats", "items"):
                if not isinstance(chat, dict):
                    continue
                cid = str(chat.get("chat_id") or "")
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                merged.append(chat)
        if merged:
            return format_chats({"ok": True, "data": {"chats": merged}}, query=q)
    payload = run_lark(
        [
            "im",
            "+chat-list",
            "--types=p2p,group",
            "--page-all",
            "--page-size",
            "50",
            "--sort",
            "active_time",
        ],
        as_identity="user",
    )
    return format_chats(payload, query=q)


def inbox_text() -> str:
    return format_inbox_digest(recent_items(days=7))


def _with_inbox(body: str) -> str:
    items = recent_items(days=7)
    if not items:
        return body
    return body.rstrip() + "\n\n【有人找你】\n" + format_inbox_digest(items)


def send_text(chat_id: str, text: str, *, as_identity: str = "bot") -> str:
    if not chat_id or not text:
        return "用法：发 oc_xxx 文本"
    payload = run_lark(
        ["im", "+messages-send", "--chat-id", chat_id, "--text", text],
        as_identity=as_identity,
    )
    if payload.get("ok"):
        return "已发送。"
    return format_lark_error(payload)


def send_card(chat_id: str, card: dict[str, Any], *, as_identity: str = "bot") -> str:
    if not chat_id or not card:
        return "卡片缺少会话或内容"
    payload = run_lark(
        [
            "im",
            "+messages-send",
            "--chat-id",
            chat_id,
            "--msg-type",
            "interactive",
            "--content",
            json.dumps(card, ensure_ascii=False),
        ],
        as_identity=as_identity,
    )
    if payload.get("ok"):
        return "已发送。"
    return format_lark_error(payload)


def _probe_ok(payload: dict[str, Any]) -> tuple[bool, str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    decision = data.get("decision") if isinstance(data, dict) else {}
    if isinstance(decision, dict) and decision.get("status") == "blocked":
        bits: list[str] = []
        for pre in decision.get("preconditions") or []:
            if isinstance(pre, dict) and pre.get("status") == "blocked":
                bits.append(str(pre.get("detail") or pre.get("hint") or ""))
        return False, " ".join(bit for bit in bits if bit)
    if payload.get("ok"):
        return True, ""
    err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    return False, str(err.get("message") or payload)[:200]


def _facts_for(intent: Intent) -> str:
    if intent.action == "help":
        return HELP_TEXT
    if intent.action == "today":
        return today_text()
    if intent.action == "brief":
        return brief_text()
    if intent.action == "tomorrow":
        return tomorrow_text()
    if intent.action == "weekly":
        return weekly_text(focus=intent.query if intent.query == "next" else "")
    if intent.action == "write_weekly":
        return write_weekly_text()
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
    if intent.action == "inbox":
        return inbox_text()
    if intent.action == "send":
        return send_text(intent.chat_id, intent.query)
    query = (intent.query or "").strip()
    if query:
        return analyze_text(query)
    return HELP_TEXT


def partner_reply(user_text: str, facts: str, intent: Intent | None = None) -> str:
    # ponytail: Hermes never gets a shell; extra facts come from our allowlist only.
    gathered = facts
    asked = user_text or (intent.query if intent else "") or (intent.action if intent else "")
    for _ in range(4):
        spoken = rewrite_partner(asked, gathered)
        fetch = parse_fetch(spoken)
        if not fetch:
            return spoken or gathered
        name, query = fetch
        extra = _facts_for(Intent(action=name, query=query))
        gathered = f"{gathered}\n\n【补充·{name}】\n{extra}"
    spoken = rewrite_human(asked, gathered)
    return spoken or gathered


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


def dispatch(
    intent: Intent,
    *,
    user_text: str = "",
    channel: str = "p2p",
    chat_id: str = "",
) -> str:
    if intent.action == "resolve":
        ensure_pending_snapshot()
        return resolve_text(intent.query or user_text)
    asked = user_text or (intent.query or "").strip() or intent.action
    prev = load_turn(chat_id) if chat_id else None
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
            )
            return reply
    if intent.action == "unknown" and looks_like_followup(asked) and not prev:
        return "上一轮我没接上。直接说「今天」「待办」，或「搜 关键词」。"
    pairs: list[tuple[str, str]] = []
    if intent.action == "unknown":
        facts, pairs = analyze_result((intent.query or asked).strip())
        kind = "clarify" if looks_like_clarify(facts) else "action"
        if chat_id:
            save_turn(chat_id, kind=kind, query=asked, action="unknown", pairs=pairs)
        if looks_like_clarify(facts):
            return facts
    else:
        facts = _facts_for(intent)
        if chat_id:
            save_turn(chat_id, kind="action", query=asked, action=intent.action, pairs=[])
    if should_partner(channel, intent.action):
        return partner_reply(asked, facts, intent) or facts
    if not should_compose(intent.action):
        return facts
    spoken = rewrite_human(asked, facts)
    return spoken or facts
