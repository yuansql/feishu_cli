from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any


def _items(payload: dict[str, Any], *keys: str) -> list[Any]:
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        data = payload if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _strip_highlight(text: str) -> str:
    return html.unescape(re.sub(r"</?h>", "", text or ""))


def plain_im_text(text: str) -> str:
    """IM HTML → one-line markdown. Card markdown cannot render raw <p>."""
    raw = html.unescape(text or "")
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</p>", "\n", raw)
    raw = re.sub(r"<[^>]+>", "", raw)
    return re.sub(r"[ \t]+", " ", raw.replace("\n", " ")).strip()


def _when(value: Any) -> str:
    if isinstance(value, dict):
        raw = value.get("datetime") or value.get("time") or value.get("timestamp") or ""
    else:
        raw = value or ""
    text = str(raw)
    if "T" in text and len(text) >= 16:
        return text[11:16]
    return text


def format_lark_error(payload: dict[str, Any]) -> str:
    err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    scopes = err.get("missing_scopes") or []
    msg = err.get("message") or payload.get("message") or "飞书接口失败"
    lines = ["飞书权威失败，本地不假装成功。", f"原因：{msg}"]
    if scopes:
        lines.append("缺权限：" + ", ".join(str(s) for s in scopes))
        lines.append(
            "补权限（需亲爱的本人在浏览器点授权，妾身不能代登）：\n"
            f'`lark-cli auth login --scope "{" ".join(str(s) for s in scopes)}"`'
        )
    hint = err.get("hint")
    if hint and "auth login" not in "\n".join(lines):
        lines.append(str(hint))
    return "\n".join(lines)


def format_tasks(payload: dict[str, Any]) -> str:
    if payload.get("ok") is False:
        return format_lark_error(payload)
    items = _items(payload, "items", "tasks")
    if not items:
        return "没有未完成待办。"
    lines = [f"未完成待办 {len(items)} 条："]
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary") or item.get("title") or "(无标题)"
        due = item.get("due_at") or item.get("due") or ""
        due_s = str(due)[:10] if due else "无截止日期"
        lines.append(f"- {summary}（{due_s}）")
    return "\n".join(lines)


def format_minutes(payload: dict[str, Any]) -> str:
    if payload.get("ok") is False:
        return format_lark_error(payload)
    items = _items(payload, "minutes", "items", "list")
    if not items:
        return "最近没有搜到你参与的会议纪要。"
    lines = [f"最近会议纪要 {len(items)} 条："]
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("topic") or item.get("name") or "(无主题)"
        when = str(item.get("start_time") or item.get("create_time") or item.get("time") or "")[:16]
        url = str(item.get("url") or item.get("share_url") or "")
        extra = f"  {url}" if url else ""
        lines.append(f"- {when} {title}{extra}".strip())
    return "\n".join(lines)


def format_approvals(payload: dict[str, Any]) -> str:
    if payload.get("ok") is False:
        return format_lark_error(payload)
    items = _items(payload, "tasks", "items", "list")
    if not items:
        return "没有待办审批。"
    lines = [f"待办审批 {len(items)} 条："]
    for item in items[:15]:
        if not isinstance(item, dict):
            continue
        title = (
            item.get("title")
            or item.get("approval_name")
            or item.get("definition_name")
            or "(无标题)"
        )
        status = item.get("status") or item.get("topic") or ""
        lines.append(f"- {title}" + (f"（{status}）" if status else ""))
    return "\n".join(lines)


def format_agenda(
    payload: dict[str, Any],
    *,
    heading: str = "今日日程",
    empty: str = "今天没有日程。",
) -> str:
    if payload.get("ok") is False:
        return format_lark_error(payload)
    items = _items(payload, "events", "items", "calendar_events")
    if not items:
        return empty
    lines = [f"{heading} {len(items)} 条："]
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary") or item.get("title") or "(无主题)"
        start = _when(item.get("start_time") or item.get("start"))
        end = _when(item.get("end_time") or item.get("end"))
        when = f"{start}–{end}" if start and end else start or end
        date = ""
        st = item.get("start_time") or item.get("start")
        if isinstance(st, dict):
            dt = str(st.get("datetime") or "")
            if len(dt) >= 10:
                date = dt[:10] + " "
        lines.append(f"- {date}{summary}（{when}）")
    return "\n".join(lines)


def format_wiki_spaces(payload: dict[str, Any], query: str = "") -> str:
    if not payload.get("ok", True) and payload.get("error"):
        return format_lark_error(payload)
    spaces = _items(payload, "spaces")
    q = (query or "").strip().lower()
    matched = []
    for space in spaces:
        if not isinstance(space, dict):
            continue
        blob = f"{space.get('name') or ''} {space.get('description') or ''}"
        if q and q not in blob.lower():
            continue
        matched.append(space)
    if not matched:
        return f"知识库里没有匹配「{query}」的空间。" if q else "没有可见知识库。"
    lines = [f"知识库空间 {len(matched)} 个" + (f"（筛选：{query}）" if q else "") + "："]
    for space in matched[:20]:
        name = space.get("name") or "(未命名)"
        desc = (space.get("description") or "").strip().replace("\n", " ")
        extra = f" — {desc[:40]}" if desc else ""
        lines.append(f"- {name}{extra}")
    return "\n".join(lines)


def format_docs_search(payload: dict[str, Any], query: str = "") -> str:
    if payload.get("ok") is False:
        return format_lark_error(payload)
    items = _items(payload, "results", "items", "docs", "nodes")
    if not items:
        return f"文档搜索「{query}」无结果。" if query else "文档搜索无结果。"
    lines = [f"文档搜索「{query}」{len(items)} 条："]
    for item in items[:15]:
        if not isinstance(item, dict):
            continue
        title, url = _doc_title_url(item)
        lines.append(f"- {title}  {url}".rstrip())
    return "\n".join(lines)


def material_pairs(payload: dict[str, Any], limit: int = 4) -> list[tuple[str, str]]:
    items = _items(payload, "results", "items", "docs", "nodes")
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        title, url = _doc_title_url(item)
        if not title or title == "(无标题)" or title in seen:
            continue
        seen.add(title)
        pairs.append((title, url))
        if len(pairs) >= limit:
            break
    return pairs


def material_titles(payload: dict[str, Any], limit: int = 4) -> list[str]:
    return [title for title, _url in material_pairs(payload, limit=limit)]


def looks_like_clarify(text: str) -> bool:
    return any(
        marker in (text or "")
        for marker in ("你想问哪件", "要我读重点", "没对上具体材料")
    )


def format_topic_brief(query: str, title: str, url: str, markdown: str) -> str:
    body = (markdown or "").strip()
    if len(body) > 1200:
        body = body[:1200] + "\n…"
    lines = [
        f"关于「{query}」，材料是《{title}》。",
        "下面是文档要点（给改写用，不是搜索列表）：",
        body or "（正文空）",
    ]
    if url:
        lines.append(url)
    return "\n".join(lines)


def format_clarify(query: str, titles: list[str]) -> str:
    q = (query or "").strip() or "这个"
    if not titles:
        return f"「{q}」我没对上具体材料。你是想问进度、接口，还是某份文档？"
    if len(titles) == 1:
        return f"「{q}」我对上《{titles[0]}》。要我读重点，还是你指的是别的？"
    lines = [f"「{q}」对上好几块，你想问哪件？"]
    for index, title in enumerate(titles[:4], 1):
        lines.append(f"{index}. {title}")
    lines.append("回个序号或再说具体一点就行。")
    return "\n".join(lines)


def format_docs_materials(payload: dict[str, Any], query: str = "", limit: int = 5) -> str:
    """Quiet material list for analysis — never the user-facing「文档搜索」dump."""
    if payload.get("ok") is False:
        return format_lark_error(payload)
    items = _items(payload, "results", "items", "docs", "nodes")
    if not items:
        return f"没有读到和「{query}」直接相关的文档。" if query else "没有读到相关文档。"
    lines = [f"【相关材料】{query}：" if query else "【相关材料】"]
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        title, url = _doc_title_url(item)
        key = title + url
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {title}" + (f"  {url}" if url else ""))
        if len(lines) >= limit + 1:
            break
    return "\n".join(lines)


def _chat_tokens(query: str) -> list[str]:
    q = query or ""
    for word in ("测试", "发版", "提测"):
        if word in q:
            q = q.replace(word, f" {word} ")
    skip = {"人员", "需要", "写完", "交给"}
    raw = [
        tok
        for tok in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,8}", q)
        if tok not in skip
    ]
    extra = [tok[-2:] for tok in raw if len(tok) >= 4]
    out: list[str] = []
    for tok in raw + extra:
        if tok in skip or tok in out:
            continue
        out.append(tok)
    return out


def format_chats(payload: dict[str, Any], query: str = "") -> str:
    if not payload.get("ok", True) and payload.get("error"):
        return format_lark_error(payload)
    chats = _items(payload, "chats", "items")
    if not chats:
        return "没有会话。"
    tokens = _chat_tokens(query)
    scored: list[tuple[int, dict[str, Any]]] = []
    for chat in chats:
        if not isinstance(chat, dict):
            continue
        name = str(chat.get("name") or "")
        score = sum(len(tok) for tok in tokens if tok in name)
        if score:
            scored.append((score, chat))
    shown = [chat for _score, chat in sorted(scored, key=lambda item: -item[0])]
    if tokens and shown:
        label = " / ".join(tokens[:4])
        lines = [f"名字里带「{label}」的会话 {len(shown)} 个："]
        use = shown[:20]
    else:
        if tokens:
            lines = [f"没找到名字带「{' / '.join(tokens[:4])}」的群。全部会话 {len(chats)} 个："]
        else:
            lines = [f"会话 {len(chats)} 个："]
        use = [chat for chat in chats if isinstance(chat, dict)][:30]
    for chat in use:
        name = chat.get("name") or "(未命名)"
        cid = chat.get("chat_id") or ""
        mode = chat.get("chat_mode") or chat.get("chat_type") or ""
        lines.append(f"- {name}  `{cid}`  {mode}")
    return "\n".join(lines)


def document_markdown(payload: dict[str, Any]) -> str:
    if payload.get("ok") is False:
        return ""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return ""
    nested = data.get("document") if isinstance(data.get("document"), dict) else {}
    content = (
        data.get("markdown")
        or data.get("content")
        or data.get("text")
        or data.get("doc")
        or nested.get("markdown")
        or nested.get("content")
        or nested.get("text")
    )
    if isinstance(content, dict):
        content = content.get("markdown") or content.get("text") or str(content)
    return _clean_feishu_md(str(content or ""))


def format_doc(payload: dict[str, Any]) -> str:
    if payload.get("ok") is False:
        return format_lark_error(payload)
    text = document_markdown(payload)
    if not text:
        return "文档是空的，或当前身份读不到正文。"
    if len(text) > 3500:
        return text[:3500] + "\n…(截断)"
    return text


def format_today(agenda_text: str, tasks_text: str, followups: str = "") -> str:
    return format_day_work(agenda_text, tasks_text, followups)


def format_day_work(agenda_text: str, tasks_text: str, followups: str = "") -> str:
    agenda = (agenda_text or "").strip()
    tasks = (tasks_text or "").strip()
    work = (followups or "").strip()
    empty_agenda = agenda in {"明天没有日程。", "今天没有日程。"}
    empty_tasks = tasks == "没有未完成待办。"
    parts: list[str] = []
    if agenda and not empty_agenda:
        parts.append("【日程】\n" + agenda)
    if tasks and not empty_tasks:
        parts.append("【待办】\n" + tasks)
    if work:
        parts.append("【要跟的活】\n" + work)
    if not parts:
        return "飞书待办是空的，这天也没有日程；跟进账里也没有未闭环的活。"
    return "\n\n".join(parts)


_STANDUP_NAMES = {"早会", "站会", "晨会", "每日站会", "晨会站会"}


def _slash_date(value: datetime) -> str:
    return f"{value.month}/{value.day}"


def _event_day(item: dict[str, Any]) -> str:
    st = item.get("start_time") or item.get("start")
    raw = ""
    if isinstance(st, dict):
        raw = str(st.get("datetime") or "")
    elif st:
        raw = str(st)
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return f"{int(raw[5:7])}/{int(raw[8:10])}"
    return ""


def _event_title(item: dict[str, Any]) -> str:
    return str(item.get("summary") or item.get("title") or "").strip() or "一场会"


def _is_standup(title: str) -> bool:
    return title in _STANDUP_NAMES or title.endswith("早会") or title.endswith("站会")


def _doc_title_url(item: dict[str, Any]) -> tuple[str, str]:
    meta = item.get("result_meta") if isinstance(item.get("result_meta"), dict) else {}
    title = _strip_highlight(
        str(
            item.get("title")
            or item.get("name")
            or item.get("title_highlighted")
            or meta.get("title")
            or "(无标题)"
        )
    )
    url = str(
        item.get("url")
        or meta.get("url")
        or item.get("docs_token")
        or item.get("token")
        or meta.get("token")
        or ""
    )
    return title, url


def _owner_name_hit(title: str) -> bool:
    from ..core.ids import USER_NAMES

    return any(name and name in title for name in USER_NAMES)


def _weekly_doc_refs(payload: dict[str, Any], limit: int = 2) -> list[tuple[str, str]]:
    items = _items(payload, "results", "items", "docs", "nodes")
    scored: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        title, url = _doc_title_url(item)
        key = title + url
        if key in seen:
            continue
        seen.add(key)
        score = 0
        if _owner_name_hit(title):
            score += 2
        if "周报" in title:
            score += 1
        scored.append((score, title, url))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [(title, url) for _, title, url in scored[:limit]]


def format_weekly_human(
    start: datetime,
    end: datetime,
    agenda_payload: dict[str, Any],
    tasks_payload: dict[str, Any],
    docs_payload: dict[str, Any] | None = None,
) -> str:
    """First-person weekly from calendar + tasks. Docs are optional references only."""
    errors: list[str] = []
    lines = [
        f"{_slash_date(start)}–{_slash_date(end)} 这周我这边的情况：",
        "",
        "【工作内容】",
    ]
    events: list[dict[str, Any]] = []
    if agenda_payload.get("ok") is False:
        errors.append(format_lark_error(agenda_payload))
    else:
        events = [item for item in _items(agenda_payload, "events", "items", "calendar_events") if isinstance(item, dict)]

    standups = [item for item in events if _is_standup(_event_title(item))]
    others = [item for item in events if item not in standups]
    if standups:
        days = "、".join(day for item in standups if (day := _event_day(item)))
        if days:
            lines.append(f"- 日常早会过了几轮（{days}）")
        else:
            lines.append("- 日常早会过了几轮")
    for item in others:
        day = _event_day(item)
        when = _when(item.get("start_time") or item.get("start"))
        stamp = day if day else when
        extra = f"（{when}）" if day and when else ""
        prefix = f"{stamp} " if stamp else ""
        lines.append(f"- {prefix}开了「{_event_title(item)}」{extra}".rstrip())
    if not standups and not others and agenda_payload.get("ok") is not False:
        lines.append("- 日历这周没记下会，不等于没干活，只是日程是空的。")

    lines += ["", "【待推进】"]
    if tasks_payload.get("ok") is False:
        errors.append(format_lark_error(tasks_payload))
        lines.append("- 待办这次没读到，上面是接口原因。")
    else:
        tasks = [item for item in _items(tasks_payload, "items", "tasks") if isinstance(item, dict)]
        if not tasks:
            lines.append("- 待办里目前是清的。")
        else:
            for item in tasks[:8]:
                summary = item.get("summary") or item.get("title") or "(无标题)"
                due = item.get("due_at") or item.get("due") or ""
                due_s = str(due)[:10] if due else "还没写截止日期"
                lines.append(f"- {summary}，截止 {due_s}")

    lines += ["", "【下周工作计划】"]
    if tasks_payload.get("ok") is not False and _items(tasks_payload, "items", "tasks"):
        lines.append("- 先把还挂着的待办往前推；新会以日历为准。")
    else:
        lines.append("- 新会以日历为准；有需要再补待办。")

    refs = _weekly_doc_refs(docs_payload or {})
    if refs:
        lines += ["", "【可对照材料】"]
        for title, url in refs:
            lines.append(f"- {title}" + (f"  {url}" if url else ""))

    if errors:
        lines += ["", "（接口说明）"]
        lines.extend(errors)
    return "\n".join(lines)


def pick_personal_weekly(docs_payload: dict[str, Any]) -> tuple[str, str]:
    for title, url in _weekly_doc_refs(docs_payload, limit=5):
        if _owner_name_hit(title) and url:
            return title, url
    return "", ""


def first_doc_ref(docs_payload: dict[str, Any], prefer: str = "") -> tuple[str, str]:
    items = _items(docs_payload, "results", "items", "docs", "nodes")
    ranked: list[tuple[int, str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title, url = _doc_title_url(item)
        if not url:
            continue
        score = 1
        if prefer and prefer.lower() in title.lower():
            score += 2
        ranked.append((score, title, url))
    ranked.sort(key=lambda row: row[0], reverse=True)
    if ranked:
        return ranked[0][1], ranked[0][2]
    return "", ""


def _clean_feishu_md(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<title>.*?</title>", "", text, flags=re.I | re.S)
    text = re.sub(
        r"<cite\b[^>]*\btitle=\"([^\"]+)\"[^>]*>\s*</cite>",
        r"\1",
        text,
        flags=re.I,
    )
    text = re.sub(r"</?(?:callout|cite|ol|li|ul|h\d|b|strong|p|div|span)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"^>\s*部分内容由豆包生成\s*$", "", text, flags=re.M)
    text = re.sub(r"^\|.+\|$", "", text, flags=re.M)
    text = re.sub(r"^---+\s*$", "", text, flags=re.M)
    text = re.sub(r"- \[[ xX]\]\s*", "- ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _draft_bullets(md: str, limit: int = 12) -> list[str]:
    out: list[str] = []
    for line in (md or "").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or text.startswith("!["):
            continue
        text = re.sub(r"^[-*]\s+", "", text)
        if len(text) < 8:
            continue
        out.append(text[:160])
        if len(out) >= limit:
            break
    return out


def _section_kind(title: str) -> str:
    if any(key in title for key in ("外观", "尺寸", "做工", "配色", "手感")):
        return "look"
    if any(key in title for key in ("网速", "网络", "4G", "WiFi", "wifi", "流量", "时延")):
        return "net"
    return "feat"


def _line_kind(line: str) -> str:
    if any(key in line for key in ("外观", "电量", "配色", "尺寸", "正面图", "做工")):
        return "look"
    if any(key in line for key in ("网速", "4G", "WiFi", "wifi", "下载", "时延", "随身WiFi")):
        return "net"
    return "feat"


def draft_doc_markdown(
    title: str,
    outline: str = "",
    extras: list[str] | None = None,
    *,
    today: str = "",
) -> str:
    """Fill a 提纲 with existing doc bullets. Chat must not receive this raw."""
    heads = [
        item.strip()
        for item in re.findall(r"(?m)^#{1,3}\s+(.+)$", outline or "")
        if item.strip()
    ]
    if not heads:
        heads = ["产品外观", "产品网速", "产品附带功能"]
    buckets: dict[str, list[str]] = {"look": [], "net": [], "feat": []}
    for extra in extras or []:
        for line in _draft_bullets(_clean_feishu_md(extra)):
            kind = _line_kind(line)
            bucket = buckets[kind]
            if line not in bucket and len(bucket) < 8:
                bucket.append(line)
    day = today or datetime.now().date().isoformat()
    lines = [
        f"# {title}",
        "",
        f"日期：{day}",
        "说明：按提纲起草。材料来自飞书已有文档，不含本次实机跑分；待测项单独标明。",
        "",
    ]
    for head in heads:
        lines.append(f"## {head}")
        items = buckets.get(_section_kind(head) or "feat") or []
        if items:
            lines.extend(f"- {item}" for item in items[:8])
        else:
            lines.append("- 待实测补：这一段提纲下还没有现成材料。")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _section_after(md: str, keywords: tuple[str, ...], stops: tuple[str, ...]) -> str:
    start_m = None
    for match in re.finditer(r"(?m)^(#{1,3})\s+(.+)$", md):
        title = match.group(2)
        if any(key in title for key in keywords):
            start_m = match
            break
    if start_m is None:
        return ""
    end = len(md)
    for match in re.finditer(r"(?m)^(#{1,3})\s+(.+)$", md):
        if match.start() <= start_m.start():
            continue
        level = len(match.group(1))
        title = match.group(2)
        if level == 1 or any(key in title for key in stops) or re.match(r"[一二三四五六七八九十]、", title):
            end = match.start()
            break
    return md[start_m.end() : end].strip()


def _project_titles(work: str) -> list[str]:
    titles = re.findall(r"(?m)^##\s+(?:\d+\.\s*)?(.+)$", work)
    return [item.strip() for item in titles if item.strip()]


def _demote_projects(work: str) -> str:
    def repl(match: re.Match[str]) -> str:
        title = re.sub(r"^\d+\.\s*", "", match.group(1).strip())
        return f"**{title}**"

    return re.sub(r"(?m)^##\s+(.+)$", repl, work).strip()


def _is_filler_project(title: str) -> bool:
    return any(key in title for key in ("日常协作", "日常支持", "其他事项"))


def _short_title(title: str) -> str:
    text = re.sub(r"^\d+\.\s*", "", title).strip()
    text = re.sub(r"(项目)?(开发与提测|开发与联调|需求推进|沟通与调整)$", "", text)
    return text.strip(" ，、") or title.strip()


def _compress_projects(work: str) -> str:
    """Keep title + first bullet; skip 日常协作 filler. Doubao-short, not a doc paste."""
    blocks = re.split(r"\n(?=##\s+)", work.strip())
    out: list[str] = []
    for block in blocks:
        heading = re.match(r"^##\s+(?:\d+\.\s*)?(.+)$", block.strip(), re.M)
        if heading is None:
            continue
        title = heading.group(1).strip()
        if _is_filler_project(title):
            continue
        bullets = [
            line for line in block.splitlines() if line.lstrip().startswith("-")
        ]
        chunk = [f"**{_short_title(title)}**"]
        if bullets:
            chunk.append(bullets[0].strip())
        out.append("\n".join(chunk))
    return "\n\n".join(out)


def format_weekly_from_doc(
    raw_md: str,
    start: datetime,
    end: datetime,
    tasks_payload: dict[str, Any] | None = None,
    source_title: str = "",
    source_url: str = "",
    focus: str = "",
) -> str:
    """Reshape the deployer's own weekly doc into a Doubao-style chat reply."""
    cleaned = _clean_feishu_md(raw_md)
    if len(cleaned) < 80:
        return ""
    work = _section_after(cleaned, ("本周完成", "完成工作"), ("问题", "风险", "下周"))
    issues = _section_after(cleaned, ("问题", "风险"), ("下周", "本周完成"))
    nxt = _section_after(cleaned, ("下周计划", "下周工作"), ("本周完成", "问题"))
    titles = [item for item in _project_titles(work) if not _is_filler_project(item)]
    if not titles and not work and not nxt:
        return ""

    if focus == "next":
        if not nxt:
            return ""
        lines = ["下周我这边打算：", "", "【下周工作计划】", nxt]
        if source_title or source_url:
            ref = source_title or "周报"
            lines += ["", f"材料：《{ref}》"]
            if source_url:
                lines.append(source_url)
        text = "\n".join(lines).strip()
        if len(text) > 3500:
            return text[:3500] + "\n…(截断)"
        return text

    lines = [
        f"{_slash_date(start)}–{_slash_date(end)} 这周我这边的情况：",
        "",
        "【工作内容】",
    ]
    if titles:
        lines.append("这周主要是" + "、".join(_short_title(item) for item in titles) + "。")
    elif work:
        lines.append(work[:400].strip())
    else:
        lines.append("按自己那份周报整理如下。")

    compact = _compress_projects(work) if work else ""
    if compact:
        lines += ["", "【重点项目与进度】", compact]
    if issues:
        lines += ["", "【问题与风险】", issues]
    if nxt:
        lines += ["", "【下周工作计划】", nxt]

    blob = work + "\n" + nxt
    extra: list[str] = []
    tasks = [
        item
        for item in _items(tasks_payload or {}, "items", "tasks")
        if isinstance(item, dict)
    ]
    for item in tasks[:8]:
        summary = str(item.get("summary") or item.get("title") or "").strip()
        if not summary:
            continue
        key = re.sub(r"（.*?）", "", summary)[:6]
        if key and key in blob:
            continue
        due = item.get("due_at") or item.get("due") or ""
        due_s = str(due)[:10] if due else "还没写截止日期"
        extra.append(f"- {summary}，截止 {due_s}")
    if extra:
        lines += ["", "【待推进】", *extra]

    if source_title or source_url:
        ref = source_title or "周报"
        lines += ["", f"材料：《{ref}》"]
        if source_url:
            lines.append(source_url)

    text = "\n".join(lines).strip()
    if len(text) > 3500:
        return text[:3500] + "\n…(截断)"
    return text


def help_text() -> str:
    from ..core.ids import display_name, identity_ready

    who = display_name() if identity_ready() else "你"
    return f"""我是{who}的飞书工作伙伴（本地独立运行，功能对标 Aily，不对接 Aily）。

单聊直接说；群里请 @我，或以「工作伙伴」「伙伴」开头。

直接说事即可，例如：
- 今天 / 今天的任务 / 明天 / 明天的任务 / 简报  （单聊发卡片：日程可点、已接受绿色；空待办不会盖掉账里的活）
- 我今天干了什么 / 读今天消息  （分页读取当天跨会话消息，归纳确认做过/推进中/待确认；「继续确认」沿用上下文）
- 本周的周报 / 下周计划
- 任务模式 A6上线前检查 / 规划 写周报  （后台先观察再动态规划；可说「任务进度」「取消任务」「继续执行」；写入仍需确认）
- 本地 HTML 报告 / 生成本地报告  （落盘 ~/.feishu-partner/reports/；上传飞书需确认）
- 早报 / 简报  （昨天小结：推进/待回复/长期待办；今天：日程+TOP5；每天 09:00 也会推）
- 待办 / 审批 / 会议纪要
- 删掉某条待办  （先说待办，再贴标题 +「删除这个待办」；飞书是勾完成）
- 搜 请假制度
- 写周报  （无链接：新建云文档摘要；有部门周报 wiki/docx 链接 +「填写周报」：原地写入你的人名节）
- 你知道我是谁吗
- 给我写个这个 / 写文档  （按提纲生成云文档，聊天只回链接，不甩原文）
- 贴飞书文档链接  （我帮你读）
- 贴链接说「写到…下面」 （先给草稿；「写进去」原地修改并回读；「多一点」续改；「结束」关闭）
- 群列表
- 聊天记录 / 读我和飞书 CLI 的聊天记录
- 谁找我  （机器人所在群里 @你 或点名）
- 今日待跟进 / 催办  （截止日期、对接人、你转交的催办）
- 生成本周任务  （写入多维表「本周任务」+ 私聊清单；周一 09:05 也会跑）
- 张三的回复如何 / 李四怎么说  （按人找会话和最近消息，不搜文档）
- 邱俊立是谁  （飞书搜索+通讯录后归纳；不是甩聊天）
- 你知道我是谁吗 / 吴梦晨是谁  （认你自己）
- 回 APP沟通群那条已处理  （明早简报不再催；09:00 卡片按钮同一本账）
- 能力对齐 / aily  （看官方文档加权差距；未跑 Aily 真实验收不会冒充达标）

工作日 09:00 会再推一张「今日待跟进」卡（已完成 / 明天再说 / 忽略）。
群里你 @别人会按对方建催办；「请王五处理」跟王五，不跟开口的人。
同事单聊派活：开放平台不给机器人推人-人单聊。后台只在 09:00–18:00 每小时用你的登录身份拉最近会话（监视链路不是 messages-search）；记下新派活会私聊你。只有你主动问「我今天干了什么 / 读今天消息」时，才按当天范围跨会话搜索一次做工作回顾。你说「今天 / 明天任务」时也会当场拉最近会话做成【要跟的活】。
表格艾特：先 `feishu followup --setup-tables`，再把要扫的表写进本机 bitable.json 的 scan_tables（3 分钟轮询，不是 messages-search）。

只有「搜 关键词」或很短的主题词才搜文档。问某人回复、删待办、详细点、听不懂不会拿去搜。
单聊里复杂句由本机 Hermes 经白名单 MCP 调你已授权的飞书 CLI 取数（不碰终端、不写飞书）；短指令（今天/待办…）本仓硬路径。材料不够时隔离 Hermes 可再取数，不会在群里调工具。
群里@你或点名指派，机器人在那个群且 serve 开着才会记/推。

不会做：aily 工作台/额度/虚拟电脑、Hermes YOLO 接群。电脑没开 `feishu serve` 时，飞书里不会回。
换人部署先跑：`feishu setup --name 你的名字`（身份写本机 config，不进 git）。
"""


# Backward-compatible name for imports that expect a string snapshot.
HELP_TEXT = help_text()
