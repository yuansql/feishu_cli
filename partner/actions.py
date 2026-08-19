from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any

from .formatters import (
    HELP_TEXT,
    _chat_tokens,
    _items,
    document_markdown,
    draft_doc_markdown,
    format_agenda,
    format_approvals,
    format_chats,
    format_clarify,
    format_doc,
    format_docs_search,
    format_lark_error,
    format_minutes,
    format_tasks,
    format_day_work,
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
from .aily import alignment_text
from .ids import WEEKLY_QUERY
from .inbox import recent_items
from .intents import Intent, looks_like_bare_search, parse_intent
from .lark import run_lark
from .hermes_setup import ensure_profile, profile_status_line
from .llm import (
    classify_intent,
    hermes_available,
    parse_fetch,
    rewrite_human,
    rewrite_partner,
    should_compose,
    should_partner,
    _is_usable_reply,
)
from .schedule import schedule_status_lines
from .watch import format_inbox_digest
from .resolved import ensure_pending_snapshot, resolve_text
from .session import load_turn, looks_like_followup, pick_index, save_turn
from .planner import plan_text
from .runner import active_task_for_chat, confirm_writes, continue_task, start_task, status_task

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


def _open_followup_text() -> str:
    from .followup import followups_for_command

    return followups_for_command()


def today_text() -> str:
    agenda = run_lark(["calendar", "+agenda"], as_identity="user")
    tasks = run_lark(
        ["task", "+get-my-tasks", "--complete=false", "--page-limit", "20"],
        as_identity="user",
    )
    return format_today(
        format_agenda(agenda),
        format_tasks(tasks),
        _open_followup_text(),
    )


def tomorrow_text() -> str:
    start, end = _day_bounds(1)
    agenda = _agenda_range(start, end)
    tasks = run_lark(
        ["task", "+get-my-tasks", "--complete=false", "--page-limit", "20"],
        as_identity="user",
    )
    return format_day_work(
        format_agenda(agenda, heading="明日日程", empty="明天没有日程。"),
        format_tasks(tasks),
        _open_followup_text(),
    )


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


def write_doc_text(query: str = "") -> str:
    asked = (query or "").strip()
    if not asked:
        return "说一下要写的标题，或把提纲文档链接发我。"
    url = ""
    title = asked
    from .intents import _URL_RE

    url_m = _URL_RE.search(asked)
    if url_m:
        url = url_m.group(0).rstrip(")。,，")
        leftover = _URL_RE.sub(" ", asked).strip()
        title = leftover or "未命名文档"
    if not url:
        docs = run_lark(
            ["docs", "+search", "--query", asked, "--page-size", "5"],
            as_identity="user",
        )
        if docs.get("ok") is False:
            return format_lark_error(docs)
        pairs = material_pairs(docs, limit=5)
        if not pairs or not pairs[0][1]:
            return f"没搜到《{asked}》提纲，换个标题或把链接发我。"
        title, url = pairs[0]
    fetched = run_lark(
        ["docs", "+fetch", "--doc", url, "--doc-format", "markdown", "--detail", "simple"],
        as_identity="user",
    )
    outline = document_markdown(fetched)
    extras: list[str] = []
    related = run_lark(
        ["docs", "+search", "--query", title, "--page-size", "5"],
        as_identity="user",
    )
    if related.get("ok"):
        for rel_title, rel_url in material_pairs(related, limit=5):
            if not rel_url or rel_url == url:
                continue
            extra_hit = run_lark(
                [
                    "docs",
                    "+fetch",
                    "--doc",
                    rel_url,
                    "--doc-format",
                    "markdown",
                    "--detail",
                    "simple",
                ],
                as_identity="user",
            )
            body = document_markdown(extra_hit)
            if body:
                extras.append(f"# {rel_title}\n{body}")
            if len(extras) >= 2:
                break
    markdown = draft_doc_markdown(title, outline, extras)
    new_title = f"{title} · {datetime.now(CN_TZ).date().isoformat()}"
    created = run_lark(
        [
            "docs",
            "+create",
            "--title",
            new_title,
            "--doc-format",
            "markdown",
            "--content",
            markdown,
        ],
        as_identity="user",
    )
    if created.get("ok"):
        link = _created_doc_link(created)
        extra = f"\n{link}" if link else ""
        return f"已生成云文档《{new_title}》。{extra}\n聊天里不贴正文，打开文档看。"
    return format_lark_error(created)


def task_rows(payload: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in _items(payload, "items", "tasks"):
        if not isinstance(item, dict):
            continue
        title = str(item.get("summary") or item.get("title") or "").strip()
        guid = str(item.get("guid") or item.get("task_id") or item.get("id") or "").strip()
        if title or guid:
            rows.append({"title": title, "guid": guid})
    return rows


def match_tasks(hint: str, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    needle = (hint or "").strip()
    open_rows = [row for row in rows if row.get("guid") or row.get("title")]
    if not needle:
        return open_rows
    hits: list[dict[str, str]] = []
    for row in open_rows:
        title = row.get("title") or ""
        if needle in title or (title and title in needle):
            hits.append(row)
    return hits


def tasks_bundle() -> tuple[str, list[dict[str, str]]]:
    payload = run_lark(
        ["task", "+get-my-tasks", "--complete=false", "--page-limit", "20"],
        as_identity="user",
    )
    return format_tasks(payload), task_rows(payload)


def tasks_text() -> str:
    return tasks_bundle()[0]


def complete_task_text(
    hint: str,
    *,
    session_items: list[dict[str, Any]] | None = None,
) -> str:
    payload = run_lark(
        ["task", "+get-my-tasks", "--complete=false", "--page-limit", "20"],
        as_identity="user",
    )
    if payload.get("ok") is False:
        return format_lark_error(payload)
    rows = task_rows(payload)
    extra = [
        {
            "title": str(item.get("title") or item.get("summary") or ""),
            "guid": str(item.get("guid") or ""),
        }
        for item in (session_items or [])
        if isinstance(item, dict)
    ]
    hits = match_tasks(hint, rows) or match_tasks(hint, extra)
    if not hits:
        if rows:
            listed = "\n".join(f"- {row['title']}" for row in rows[:8])
            return "没对上要勾的待办。当前未完成：\n" + listed
        return "没有未完成待办可勾。"
    if len(hits) > 1:
        listed = "\n".join(f"- {row['title']}" for row in hits[:8])
        return "对上好几条待办，把标题再说清楚点：\n" + listed
    item = hits[0]
    guid = item.get("guid") or ""
    title = item.get("title") or "这条"
    if not guid:
        return f"找到《{title}》但没有任务 ID，没法在飞书勾掉。"
    result = run_lark(
        ["task", "+complete", "--task-id", guid],
        as_identity="user",
    )
    if result.get("ok"):
        return f"已勾完成：{title}\n（飞书待办是标记完成，不是从回收站抹掉。）"
    return format_lark_error(result)


def create_task_item(summary: str, due: str = "") -> str:
    title = (summary or "").strip()
    if not title:
        return "待办标题不能为空。"
    if len(title) > 200:
        title = title[:200]
    args = ["task", "+create", "--summary", title, "--as", "user"]
    due_text = (due or "").strip()
    if due_text:
        args.extend(["--due", due_text])
    payload = run_lark(args, as_identity="user")
    if payload.get("ok"):
        return f"已创建飞书待办：{title}"
    return format_lark_error(payload)


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
    from .knowledge import search_prioritized

    return search_prioritized(query)


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


def _message_body(msg: dict[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, dict):
        text = content.get("text") or ""
    else:
        text = msg.get("text") or content or ""
    return str(text or "").replace("\n", " ").strip()


def _message_who(msg: dict[str, Any]) -> str:
    sender = msg.get("sender")
    if isinstance(sender, dict):
        return str(sender.get("name") or sender.get("sender_name") or "").strip()
    return str(msg.get("sender_name") or "").strip()


def _recent_messages(chat_id: str) -> list[dict[str, Any]]:
    if not chat_id:
        return []
    payload = run_lark(
        [
            "im",
            "+chat-messages-list",
            "--chat-id",
            chat_id,
            "--order",
            "desc",
            "--page-size",
            "15",
            "--no-reactions",
        ],
        as_identity="user",
    )
    if payload.get("ok") is False:
        return []
    hits = payload.get("data")
    if isinstance(hits, dict):
        hits = hits.get("messages") or hits.get("items") or []
    if not isinstance(hits, list):
        return []
    return [item for item in hits if isinstance(item, dict)]


def _person_open_id(name: str, chats: list[dict[str, Any]]) -> str:
    for chat in chats[:4]:
        cid = str(chat.get("chat_id") or "")
        if not cid:
            continue
        payload = run_lark(
            ["im", "+chat-members-list", "--chat-id", cid, "--page-size", "50"],
            as_identity="user",
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        users = data.get("users") if isinstance(data.get("users"), list) else []
        for user in users:
            if not isinstance(user, dict):
                continue
            if name not in str(user.get("name") or ""):
                continue
            oid = str(user.get("member_id") or user.get("open_id") or "")
            if oid.startswith("ou_"):
                return oid
    return ""


def _sender_messages(open_id: str) -> list[dict[str, Any]]:
    if not open_id:
        return []
    payload = run_lark(
        [
            "im",
            "+messages-search",
            "--sender",
            open_id,
            "--page-size",
            "10",
            "--no-reactions",
        ],
        as_identity="user",
    )
    if payload.get("ok") is False:
        return []
    hits = payload.get("data")
    if isinstance(hits, dict):
        hits = hits.get("messages") or hits.get("items") or []
    if not isinstance(hits, list):
        return []
    return [item for item in hits if isinstance(item, dict)]


def _format_msg_line(msg: dict[str, Any], name: str) -> str:
    who = _message_who(msg) or name
    body = _message_body(msg)
    if not body:
        return ""
    if body.startswith("![Image]"):
        body = "（图片）"
    when = str(msg.get("create_time") or "")
    if len(when) >= 16:
        when = when[5:16].replace("T", " ")
    clip = body[:160] + ("…" if len(body) > 160 else "")
    if when:
        return f"- {when} {who}：{clip}"
    return f"- {who}：{clip}"


def person_text(query: str) -> str:
    """Look up someone's recent IM replies. Never searches docs."""
    name = (query or "").strip()
    if not name:
        return "说一下是谁，我去翻最近怎么回的。"
    lines = [f"【{name}最近怎么说】"]
    inbox_hits = [
        item
        for item in recent_items(days=14)
        if name in str(item.get("sender_name") or "")
        or name in str(item.get("text") or "")
        or name in str(item.get("chat_name") or "")
    ]
    if inbox_hits:
        lines.append("收件箱里：")
        for item in inbox_hits[:5]:
            who = str(item.get("sender_name") or name).strip()
            where = str(item.get("chat_name") or "群").strip()
            text = str(item.get("text") or "").replace("\n", " ").strip()
            if len(text) > 120:
                text = text[:120] + "…"
            lines.append(f"- {who}（{where}）：{text}" if text else f"- {who}（{where}）")
    payload = run_lark(
        ["im", "+chat-search", "--query", name, "--page-size", "20"],
        as_identity="user",
    )
    chats = [chat for chat in _items(payload, "chats", "items") if isinstance(chat, dict)]
    if payload.get("ok") is False and not chats:
        err = format_lark_error(payload)
        if len(lines) == 1:
            return err
        lines.append(err)
        return "\n".join(lines)
    oid = _person_open_id(name, chats)
    shown = [_format_msg_line(msg, name) for msg in _sender_messages(oid)]
    shown = [line for line in shown if line][:8]
    if shown:
        lines.append("最近发的：")
        lines.extend(shown)
        return "\n".join(lines)
    found_msg = False
    for chat in chats[:3]:
        cid = str(chat.get("chat_id") or "")
        cname = str(chat.get("name") or "会话").strip()
        mode = str(chat.get("chat_mode") or chat.get("chat_type") or "").lower()
        local: list[str] = []
        for msg in _recent_messages(cid):
            who = _message_who(msg)
            sid = ""
            sender = msg.get("sender")
            if isinstance(sender, dict):
                sid = str(sender.get("id") or "")
            if not (
                "p2p" in mode
                or name in who
                or name in cname
                or (oid and sid == oid)
            ):
                continue
            line = _format_msg_line(msg, name)
            if line:
                local.append(line)
            if len(local) >= 8:
                break
        if not local:
            continue
        found_msg = True
        lines.append(f"{cname}：")
        lines.extend(local)
    if found_msg or inbox_hits:
        return "\n".join(lines)
    if chats:
        names = "、".join(
            str(chat.get("name") or "").strip()
            for chat in chats[:5]
            if chat.get("name")
        )
        return f"找到和「{name}」相关的会话（{names}），但最近没有可读的文字回复。"
    return (
        f"没找到「{name}」的会话或最近回复。"
        "我只能看你身份下搜得到的群/单聊；机器人不在的群看不见。"
        "也可以说「谁找我」。"
    )


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


def _task_lines(payload: dict[str, Any]) -> list[str]:
    if payload.get("ok") is False:
        return []
    lines: list[str] = []
    for item in _items(payload, "items", "tasks")[:20]:
        if not isinstance(item, dict):
            continue
        summary = str(item.get("summary") or item.get("title") or "(无标题)")
        due = item.get("due_at") or item.get("due") or ""
        due_s = str(due)[:10] if due else ""
        lines.append(f"{summary}（{due_s}）" if due_s else summary)
    return lines


def send_style_card(intent: Intent, chat_id: str) -> bool:
    """P2P 简报/今天/明天走 JSON 2.0 卡；失败则交给 dispatch 发文字。"""
    if not chat_id or intent.action not in {"brief", "today", "tomorrow"}:
        return False
    card: dict[str, Any] | None = None
    if intent.action == "brief":
        from .brief import collect_brief
        from .brief_card import brief_card

        card = brief_card(collect_brief())
    else:
        from .brief import agenda_entries
        from .brief_card import day_work_card
        from .followup import followups_for_command

        offset = 1 if intent.action == "tomorrow" else 0
        start, end = _day_bounds(offset)
        entries = agenda_entries(_agenda_range(start, end))
        tasks = run_lark(
            ["task", "+get-my-tasks", "--complete=false", "--page-limit", "20"],
            as_identity="user",
        )
        card = day_work_card(
            kind=intent.action,
            day=start.date(),
            entries=entries,
            followups=followups_for_command(),
            task_lines=_task_lines(tasks),
        )
    result = send_card(chat_id, card)
    if result != "已发送。":
        return False
    save_turn(
        chat_id,
        kind="action",
        query=intent.action,
        action=intent.action,
        pairs=[],
    )
    return True


def add_reaction(message_id: str, emoji: str = "OnIt") -> str:
    if not message_id:
        return "skip"
    payload = run_lark(
        [
            "im",
            "reactions",
            "create",
            "--message-id",
            message_id,
            "--data",
            json.dumps({"reaction_type": {"emoji_type": emoji}}, ensure_ascii=False),
        ],
        as_identity="bot",
    )
    if payload.get("ok"):
        return "ok"
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
    if intent.action == "aily":
        return alignment_text()
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
    if intent.action == "write_doc":
        return write_doc_text(intent.query)
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
    if intent.action == "person":
        return person_text(intent.query)
    if intent.action == "inbox":
        return inbox_text()
    if intent.action == "digest":
        from .followup import digest_text

        return digest_text()
    if intent.action == "weekly_tasks":
        from .bitable import weekly_tasks_text

        return weekly_tasks_text()
    if intent.action == "plan":
        return plan_text(intent.query, today_text())
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


def dispatch(
    intent: Intent,
    *,
    user_text: str = "",
    channel: str = "p2p",
    chat_id: str = "",
    force_facts: bool = False,
) -> str:
    if intent.action == "resolve":
        ensure_pending_snapshot()
        return resolve_text(intent.query or user_text)
    asked = user_text or (intent.query or "").strip() or intent.action
    if intent.action == "plan":
        goal = (intent.query or asked).strip()
        # IM 单聊走 TaskRunner；CLI / force_facts 仍出文本计划（不依赖 LLM）
        if chat_id and goal and not force_facts:
            return start_task(goal, chat_id)
        return plan_text(goal, today_text(), allow_llm=not force_facts)
    if intent.action == "task_continue":
        return continue_task(chat_id)
    if intent.action == "task_status":
        return status_task(chat_id)
    if intent.action == "task_confirm":
        return confirm_writes(chat_id)
    prev = load_turn(chat_id) if chat_id else None
    if chat_id and asked.strip() == "继续" and active_task_for_chat(chat_id):
        return continue_task(chat_id)
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
    if intent.action == "unknown":
        if not looks_like_bare_search(asked):
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
