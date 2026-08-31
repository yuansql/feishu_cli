"""Local ledger: P2P/card 「已处理」→ 明早简报不再列。飞书权威失败不假装销账。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CN_TZ = timezone(timedelta(hours=8))
_DEFAULT_RESOLVED = Path.home() / ".feishu-partner" / "resolved.jsonl"
_DEFAULT_PENDING = Path.home() / ".feishu-partner" / "pending.json"

# Bump this whenever the resolution semantics change (e.g., third_party_ack).
# Old entries written under an earlier epoch will expire and let threads be
# re-judged by the current logic.
LOGIC_EPOCH = 1
RESOLVE_TTL_DAYS = 14

_DONE = (
    "已经解决了",
    "已经处理",
    "已经完成",
    "已解决",
    "解决了",
    "已处理",
    "已完成",
    "完成了",
    "搞定了",
    "搞定",
    "不用催了",
    "不用催",
    "处理完了",
    "处理掉了",
    "销了",
)
_HINT_NOISE = (
    "已经解决了",
    "已经处理",
    "已经完成",
    "已解决",
    "解决了",
    "这块",
    "这个我",
    "这个",
    "回 ",
    "那条",
    "那件事",
    "已处理",
    "已完成",
    "完成了",
    "搞定了",
    "搞定",
    "不用催了",
    "不用催",
    "处理完了",
    "处理掉了",
    "销了",
    "进行中",
    "未完成",
    "未回复",
    "已追问未答完",
    "待处理",
    "待回复",
    "（",
    "）",
    "(",
    ")",
    "·",
)
_SECTION_DONE_RE = re.compile(
    r"待处理\s*/\s*待回复|待处理|待回复"
)
_NUMBERED_ITEM_RE = re.compile(r"(?m)^\s*\d+[\.、]\s*")
_DONE_WITH_URL_RE = re.compile(
    r"(已经完成|已经处理|已完成|完成了|搞定了|搞定|(?<![未不])完成).{0,40}https://|"
    r"https://.{0,200}(?<![未不])(已经完成|已经处理|已完成|完成了|搞定了|搞定|完成)"
)
_INSTRUCTION_MARKERS = (
    "周报接收人",
    "只写到",
    "发一下看看情况",
    "写到今天",
)


def assign_reply_body(raw: str) -> str:
    """Extract the new sentence from lark-cli's flattened reply payload."""
    head, separator, body = (raw or "").partition("\n\n")
    if (
        not separator
        or not head.startswith("刚记下")
        or "派你的活：\n" not in head
    ):
        return ""
    return body.strip()


def looks_like_instruction_blob(raw: str) -> bool:
    """Weekly/plan constraints that mention「已完成」as status, not ledger close."""
    text = (raw or "").strip()
    if not text:
        return False
    if _SECTION_DONE_RE.search(text) and any(word in text for word in _DONE):
        return False
    if len(_NUMBERED_ITEM_RE.findall(text)) >= 2:
        return True
    return any(marker in text for marker in _INSTRUCTION_MARKERS)


def looks_like_done_with_evidence(raw: str) -> bool:
    """「…完成 https://…docx」— close followup with doc proof."""
    text = (raw or "").strip()
    if not text:
        return False
    if any(mark in text for mark in ("吗", "？", "?", "有没有", "是不是")):
        return False
    if "feishu.cn/" not in text:
        return False
    return bool(_DONE_WITH_URL_RE.search(text.replace("\n", " ")))


def looks_like_resolve(raw: str) -> bool:
    text = (raw or "").strip()
    if not text:
        return False
    if any(mark in text for mark in ("吗", "？", "?", "有没有", "是不是")):
        return False
    if looks_like_instruction_blob(text):
        return False
    if looks_like_done_with_evidence(text):
        return True
    return any(word in text for word in _DONE)


_UNRESOLVE_MARKS = (
    "没解决",
    "没完成",
    "没搞定",
    "没处理好",
    "还没有解决",
    "还没有完成",
    "还没好",
    "还没处理",
    "搞错了",
    "错了",
    "撤",
    "撤销",
    "重新打开",
    "恢复",
)


def looks_like_unresolve(raw: str) -> bool:
    """User says a resolved item is actually not done — reopen it."""
    text = (raw or "").strip()
    if not text:
        return False
    # A bare question like "这个没解决吗？" is asking, not reopening.
    if any(mark in text for mark in ("吗", "？", "?")):
        return False
    return any(mark in text for mark in _UNRESOLVE_MARKS)


def quoted_brief_pending(
    raw: str, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """回引简报「待处理 / 待回复」整段 + 已解决 → 销简报 pending，不误销同名跟进账。"""
    text = raw or ""
    if not looks_like_resolve(text) or not _SECTION_DONE_RE.search(text):
        return []
    open_items = [
        item
        for item in items
        if item.get("key") and not is_resolved(str(item.get("key") or ""))
    ]
    if not open_items:
        return []
    leftover = resolve_hint(text)
    leftover = re.sub(r"\d+\s*项?", " ", leftover)
    leftover = re.sub(r"[/\s]+", "", leftover)
    if not leftover:
        return open_items
    blob = re.sub(r"\s+", " ", text)
    hits: list[dict[str, Any]] = []
    for item in open_items:
        snippet = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if not snippet:
            continue
        if snippet[:12] in blob or (len(snippet) >= 8 and snippet[:8] in blob):
            hits.append(item)
    return hits


def looks_like_pending_section_done(raw: str) -> bool:
    """「待处理 / 待回复（2项）已经完成」→ 整批销简报 pending。"""
    return bool(quoted_brief_pending(raw, load_pending()))


def resolve_hint(raw: str) -> str:
    q = raw or ""
    for noise in _HINT_NOISE:
        q = q.replace(noise, " ")
    return re.sub(r"\s+", " ", q).strip()


def pending_key(*, message_id: str = "", chat_id: str = "", text: str = "") -> str:
    mid = (message_id or "").strip()
    if mid:
        return "om:" + mid
    basis = f"{chat_id}|{(text or '').strip()[:80]}"
    return "fp:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def resolved_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_RESOLVED")
    if override:
        return Path(override).expanduser()
    return _DEFAULT_RESOLVED


def pending_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_PENDING")
    if override:
        return Path(override).expanduser()
    return _DEFAULT_PENDING


def load_resolved() -> list[dict[str, Any]]:
    path = resolved_path()
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def resolved_in_window(
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    rows = load_resolved()
    if start is None and end is None:
        return rows
    out: list[dict[str, Any]] = []
    for row in rows:
        ts = _parse_resolved_ts(row.get("ts"))
        if ts is None:
            continue
        if start is not None and ts < start:
            continue
        if end is not None and ts > end:
            continue
        out.append(row)
    return out


def _parse_resolved_ts(raw: Any) -> datetime | None:
    text = str(raw or "")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def is_resolved(key: str) -> bool:
    token = (key or "").strip()
    if not token:
        return False
    now = datetime.now(CN_TZ)
    ttl = timedelta(days=RESOLVE_TTL_DAYS)
    for row in load_resolved():
        if str(row.get("key") or "") != token:
            continue
        # Lease check: stale semantics (old epoch) or age beyond TTL stop
        # suppressing the thread, allowing re-judgement by current logic.
        when = _parse_resolved_ts(row.get("decided_at") or row.get("ts"))
        epoch = int(row.get("logic_epoch", 0))
        if when is None:
            continue
        if epoch < LOGIC_EPOCH:
            continue
        if now - when > ttl:
            continue
        return True
    return False


def gc_resolved(*, dry_run: bool = False) -> tuple[int, int]:
    """Remove expired resolved rows (old epoch or beyond TTL). Returns (kept, dropped)."""
    rows = load_resolved()
    now = datetime.now(CN_TZ)
    ttl = timedelta(days=RESOLVE_TTL_DAYS)
    kept_rows: list[dict[str, Any]] = []
    dropped = 0
    for row in rows:
        when = _parse_resolved_ts(row.get("decided_at") or row.get("ts"))
        epoch = int(row.get("logic_epoch", 0))
        if when is None or epoch < LOGIC_EPOCH or now - when > ttl:
            dropped += 1
            continue
        kept_rows.append(row)
    if not dry_run and dropped:
        dest = resolved_path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in kept_rows
        )
        dest.write_text(text, encoding="utf-8")
    return len(kept_rows), dropped


def mark_resolved(
    key: str,
    *,
    source: str,
    chat_name: str = "",
    snippet: str = "",
    tag: str = "",
    link: str = "",
) -> bool:
    token = (key or "").strip()
    if not token:
        return False
    if is_resolved(token):
        drop_pending(token)
        return True
    dest = resolved_path()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(CN_TZ)
        row = {
            "key": token,
            "source": source,
            "chat_name": chat_name,
            "snippet": snippet[:160],
            "tag": tag,
            "link": link,
            "ts": now.isoformat(timespec="seconds"),
            "decided_at": now.isoformat(timespec="seconds"),
            "logic_epoch": LOGIC_EPOCH,
        }
        with dest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return False
    if not is_resolved(token):
        return False
    drop_pending(token)
    return True


def drop_pending(key: str) -> None:
    """Aily-like: 销账 = 从待处理本子拿掉，不只写 skip 名单。"""
    token = (key or "").strip()
    if not token:
        return
    items = load_pending()
    kept = [item for item in items if str(item.get("key") or "") != token]
    if len(kept) != len(items):
        save_pending(kept)


def unresolve(keys: set[str] | list[str]) -> int:
    """Physically delete rows for ``keys`` from the resolved ledger."""
    tokens = {str(k or "").strip() for k in keys if str(k or "").strip()}
    if not tokens:
        return 0
    rows = load_resolved()
    kept = [row for row in rows if str(row.get("key") or "") not in tokens]
    removed = len(rows) - len(kept)
    if removed:
        dest = resolved_path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in kept
        )
        dest.write_text(text, encoding="utf-8")
    return removed


def unresolve_by_hint(hint: str) -> tuple[int, list[dict[str, Any]]]:
    """Re-open resolved items that match ``hint``.

    Only rows that are still alive under current logic-epoch/TTL are
    considered, so stale ghosts are not accidentally resurrected.
    """
    needle = (hint or "").strip()
    rows = [
        row for row in load_resolved()
        if is_resolved(str(row.get("key") or ""))
    ]
    if not rows:
        return 0, []
    matches: list[dict[str, Any]] = []
    for row in rows:
        chat = str(row.get("chat_name") or "").strip()
        snippet = str(row.get("snippet") or "").strip()
        key = str(row.get("key") or "").strip()
        if not key:
            continue
        labels = [chat, snippet, key]
        if any(needle in label or (label and label in needle) for label in labels if label):
            matches.append(row)
    if len(matches) > 1:
        # Be conservative: ambiguous reopen requests are not auto-batch.
        return -1, matches
    removed = 0
    if matches:
        removed = unresolve([row["key"] for row in matches])
    return removed, matches


def save_pending(items: list[dict[str, Any]]) -> None:
    dest = pending_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "items": items,
    }
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_pending() -> list[dict[str, Any]]:
    dest = pending_path()
    if not dest.exists():
        return []
    try:
        payload = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        items = payload.get("items") or []
        if isinstance(items, list):
            return [row for row in items if isinstance(row, dict)]
    return []


def match_pending(hint: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    open_items = [
        item
        for item in items
        if item.get("key") and not is_resolved(str(item.get("key") or ""))
    ]
    needle = (hint or "").strip()
    if not needle:
        return open_items
    text_hits: list[dict[str, Any]] = []
    name_hits: list[dict[str, Any]] = []
    for item in open_items:
        name = str(item.get("chat_name") or "")
        who = str(item.get("sender_name") or "")
        text = str(item.get("text") or "").strip()
        if text and (text in needle or needle in text):
            text_hits.append(item)
        elif any(
            needle in label or (label and label in needle)
            for label in (name, who)
            if label
        ):
            name_hits.append(item)
    return text_hits or name_hits


def _wants_pending_detail(raw: str) -> bool:
    text = raw or ""
    if any(mark in text for mark in ("详细", "展开", "具体", "谁提的", "谁派的", "谁提交")):
        return True
    # 回引简报「1. 谁（群）…」整行，即使没写详细也当看详情
    if re.match(r"^\s*\d+\s*[\.、．]", text) and (
        "进行中" in text or "未回复" in text or "待确认" in text or "（" in text
    ):
        return True
    return False


def _pending_detail_hint(raw: str) -> str:
    q = raw or ""
    for noise in (
        "详细些",
        "详细点",
        "再详细点",
        "再详细",
        "详细一下",
        "详细",
        "展开说说",
        "展开一下",
        "展开",
        "具体些",
        "具体点",
        "具体一下",
        "具体",
        "不知道你在说什么",
        "听不懂",
        "什么意思",
        "再说清楚",
        "这个是谁提的",
        "是谁提的",
        "谁提的",
        "谁派的",
        "谁提交的",
        "谁提交",
    ):
        q = q.replace(noise, " ")
    q = re.sub(r"^\s*\d+\s*[\.、．]\s*", "", q)
    q = re.sub(r"[（(](?:进行中|未完成|未回复)[^）)]*[）)]", " ", q)
    return re.sub(r"\s+", " ", q).strip(" ：:，,。.!！?")


def _pending_message_id(item: dict[str, Any]) -> str:
    mid = str(item.get("message_id") or "").strip()
    if mid.startswith("om_"):
        return mid
    key = str(item.get("key") or "").strip()
    if key.startswith("om:"):
        return key[3:]
    return ""


def _thin_pending_body(body: str) -> bool:
    text = (body or "").strip()
    if not text:
        return True
    return bool(re.fullmatch(r"(@\S+\s*)*(!\[[^\]]*\]\([^)]*\)\s*)+", text))


def _display_msg_text(raw: str) -> str:
    from ..compose.formatters import plain_im_text

    text = plain_im_text(raw or "")

    def _image_repl(match: re.Match[str]) -> str:
        url = (match.group(1) or "").strip()
        if url.startswith("http://") or url.startswith("https://"):
            return f"[图片] {url}"
        return "[图片]"

    text = re.sub(r"!\[[^\]]*\]\(([^)]*)\)", _image_repl, text)
    return re.sub(r"\s+", " ", text).strip()


def _msg_plain(msg: dict[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, dict):
        raw = content.get("text") or ""
    elif isinstance(content, str):
        raw = content
    else:
        raw = msg.get("text") or ""
    return _display_msg_text(str(raw or ""))


def _msg_who(msg: dict[str, Any]) -> str:
    sender = msg.get("sender")
    if isinstance(sender, dict):
        return str(sender.get("name") or sender.get("sender_name") or "").strip()
    return str(msg.get("sender_name") or "").strip()


def _msg_app_link(msg: dict[str, Any]) -> str:
    return str(msg.get("message_app_link") or msg.get("app_link") or "").strip()


def _format_thread_line(msg: dict[str, Any]) -> str:
    """谁：正文；能拿到的链接都带上（正文 URL + message_app_link）。"""
    who = _msg_who(msg) or "对方"
    text = _msg_plain(msg)
    app_link = _msg_app_link(msg)
    if not text and not app_link:
        return ""
    line = f"{who}：{text or '[无文字]'}"
    # 正文里已有的 http(s) 不再重复；跳转链单独挂
    if app_link and app_link not in line:
        line += f" {app_link}"
    return line


def _is_image_only_text(body: str) -> bool:
    text = (body or "").strip()
    if not text:
        return True
    if re.fullmatch(r"(@\S+\s*)*(!\[[^\]]*\]\([^)]*\)\s*)+", text):
        return True
    return bool(re.fullmatch(r"(@\S+\s*)*(\[图片\](?:\s+https?://\S+)?\s*)+", text))


def _refresh_pending_message(item: dict[str, Any]) -> tuple[str, str]:
    """mget 原消息；摘要只剩图时再拉邻近对话。返回 (正文, 原消息跳转链)。"""
    from ..compose.formatters import plain_im_text
    from ..core.lark import run_lark

    stored = plain_im_text(str(item.get("text") or ""))
    mid = _pending_message_id(item)
    chat_id = str(item.get("chat_id") or "").strip()
    link = str(item.get("link") or "").strip()
    refreshed = stored
    origin: dict[str, Any] | None = None
    if mid:
        payload = run_lark(
            ["im", "+messages-mget", "--message-ids", mid, "--no-reactions"],
            as_identity="user",
        )
        if payload.get("ok") is not False:
            data = payload.get("data")
            msgs = data.get("messages") if isinstance(data, dict) else None
            if isinstance(msgs, list) and msgs and isinstance(msgs[0], dict):
                origin = msgs[0]
                got = _msg_plain(origin)
                if got:
                    refreshed = got
                app = _msg_app_link(origin)
                if app:
                    link = app
    thread = _nearby_thread_lines(chat_id, mid) if chat_id else []
    thin = _is_image_only_text(refreshed) or _thin_pending_body(stored)
    if thin and thread:
        return "原消息含图片，邻近对话：\n" + "\n".join(thread), link
    if thin:
        return "（原消息主要是图片，邻近对话也没拉到。）", link
    if thread:
        return refreshed + "\n\n邻近对话：\n" + "\n".join(thread), link
    return refreshed, link


def _nearby_thread_lines(chat_id: str, message_id: str, *, after: int = 6) -> list[str]:
    """原消息 + 之后几条（时间正序）。读不到摘要时靠邻近对话补语境。"""
    from ..core.lark import run_lark

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
            "30",
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
    rows = [item for item in hits if isinstance(item, dict)]
    mid = (message_id or "").strip()
    idx = None
    if mid:
        for i, msg in enumerate(rows):
            if str(msg.get("message_id") or "") == mid:
                idx = i
                break
    if idx is None:
        return []
    # rows 为 desc：取自身 + 更新的 after 条，再正序
    window = list(reversed(rows[max(0, idx - after) : idx + 1]))
    lines: list[str] = []
    for msg in window:
        line = _format_thread_line(msg)
        if line:
            lines.append(line)
    return lines[: after + 1]


def _format_pending_detail(item: dict[str, Any]) -> str:
    who = str(item.get("sender_name") or "").strip() or "对方"
    where = str(item.get("chat_name") or "群").strip()
    tag = str(item.get("tag") or "").strip()
    kind = str(item.get("kind") or "")
    key = str(item.get("key") or "")
    if kind == "bitable_at" or key.startswith("fu:bitable:"):
        from ..office.bitable import reread_bitable_detail

        body = reread_bitable_detail(
            record_id=str(item.get("message_id") or key),
            table_label=where,
        )
        if not body:
            body = str(item.get("text") or "（多维表再读失败，摘要不够。）")
        lines = [f"【跟进详情】{who} · {where}", body]
        if tag:
            lines.append(f"账本状态：{tag}")
        lines.append("处理完回「已处理」。")
        return "\n".join(lines)
    body, link = _refresh_pending_message(item)
    if not link:
        link = str(item.get("link") or "").strip()
    lines = [f"【待回复详情】{who} · {where}", f"内容：{body}"]
    if tag:
        lines.append(f"状态：{tag}")
    if link:
        lines.append(f"跳转：{link}")
    lines.append("处理完回「已处理」或点卡片「已处理」。")
    return "\n".join(lines)


def _detail_candidate_pool() -> list[dict[str, Any]]:
    """简报 pending + 跟进账，统一给「详细些」对号。"""
    ensure_pending_snapshot()
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in load_pending():
        key = str(item.get("key") or "")
        if not key or is_resolved(key) or key in seen:
            continue
        seen.add(key)
        pool.append(item)
    from ..office.followup import load_items

    for item in load_items():
        if str(item.get("status") or "") not in {"open", "snooze"}:
            continue
        key = str(item.get("id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        pool.append(
            {
                "key": key,
                "kind": str(item.get("kind") or ""),
                "chat_id": str(item.get("chat_id") or ""),
                "chat_name": str(item.get("chat_name") or ""),
                "sender_name": str(
                    item.get("asker_name") or item.get("assignee_name") or ""
                ),
                "text": str(item.get("text") or ""),
                "tag": "跟进",
                "message_id": str(item.get("message_id") or ""),
                "link": "",
            }
        )
    return pool


def pending_detail_text(raw: str) -> str | None:
    """回引简报待回复/今日待跟进 + 详细些 → 展开账本；多维表摘要不够就再读记录。"""
    if not _wants_pending_detail(raw):
        return None
    items = _detail_candidate_pool()
    if not items:
        return None
    idx_match = re.match(r"^\s*(\d+)\s*[\.、．]", raw or "")
    idx = int(idx_match.group(1)) if idx_match else None
    if idx is None:
        from ..core.session import pick_index

        idx = pick_index(raw)
    hint = _pending_detail_hint(raw)
    hits = match_pending(hint, items) if hint else []
    chosen: dict[str, Any] | None = None
    if idx is not None and 1 <= idx <= len(items):
        candidate = items[idx - 1]
        hit_keys = {str(h.get("key") or "") for h in hits}
        if not hits or str(candidate.get("key") or "") in hit_keys:
            chosen = candidate
        elif len(hits) == 1:
            chosen = hits[0]
    elif len(hits) == 1:
        chosen = hits[0]
    elif not hint and len(items) == 1:
        chosen = items[0]
    elif len(hits) > 1:
        lines = ["对上好几条，回序号或点名哪一条："]
        for i, item in enumerate(hits[:5], 1):
            lines.append(f"{i}. {pending_line(item)}")
        return "\n".join(lines)
    if chosen is None:
        return None
    return _format_pending_detail(chosen)


def pending_line(item: dict[str, Any]) -> str:
    where = str(item.get("chat_name") or "群")
    text = str(item.get("text") or "")
    tag = str(item.get("tag") or "未完成")
    line = f"{where}：{text}（{tag}）"
    link = str(item.get("link") or "").strip()
    if link:
        line += f" {link}"
    return line


def _list_items(items: list[dict[str, Any]]) -> str:
    lines = []
    for item in items[:8]:
        lines.append("- " + pending_line(item))
    return "\n".join(lines)


def inbox_as_pending() -> list[dict[str, Any]]:
    from ..core.inbox import recent_items

    out: list[dict[str, Any]] = []
    for item in recent_items(days=7):
        raw = (item.get("text") or "").replace("\n", " ").strip()
        if not raw:
            continue
        text = raw if len(raw) <= 72 else raw[:72] + "…"
        out.append(
            {
                "key": pending_key(
                    message_id=str(item.get("message_id") or ""),
                    chat_id=str(item.get("chat_id") or ""),
                    text=text,
                ),
                "chat_id": str(item.get("chat_id") or ""),
                "chat_name": str(item.get("chat_name") or "群"),
                "text": text,
                "tag": "inbox",
            }
        )
    return out


def ensure_pending_snapshot() -> None:
    if load_pending():
        return
    # tests point these env vars at temp files; never hit live Feishu.
    if os.environ.get("FEISHU_PARTNER_PENDING") or os.environ.get("FEISHU_PARTNER_RESOLVED"):
        return
    from ..office.brief import snapshot_pending

    snapshot_pending()


def resolve_text(raw: str) -> str:
    from ..office.followup import apply_action, format_assign_push, open_followups_as_pending

    items = load_pending()
    hint = resolve_hint(raw)
    followups = open_followups_as_pending()

    # Re-opening path: user realises an item was wrongly marked resolved.
    if looks_like_unresolve(raw):
        removed, matches = unresolve_by_hint(hint)
        if removed == 1:
            name = str(matches[0].get("chat_name") or "那条")
            return f"已撤销「已处理」，{name} 那条会重新出现在简报里。"
        if removed > 1:
            lines = ["撤销了好几条："]
            for row in matches[:5]:
                lines.append(f"- {row.get('chat_name') or '那条'}：{row.get('snippet') or ''}")
            return "\n".join(lines)
        if removed == -1:
            lines = ["对上好几条，说清楚撤销哪一条（群名/原话片段）："]
            for row in matches[:5]:
                lines.append(f"- {row.get('chat_name') or '那条'}：{row.get('snippet') or ''}")
            return "\n".join(lines)
        return "没在已处理里找到能撤销的项。它可能已过期或已被清理。"

    quoted = quoted_brief_pending(raw, items)
    if quoted:
        closed = 0
        for item in quoted:
            key = str(item.get("key") or "")
            if mark_resolved(
                key,
                source="text",
                chat_name=str(item.get("chat_name") or ""),
                snippet=str(item.get("text") or ""),
                tag=str(item.get("tag") or ""),
                link=str(item.get("link") or ""),
            ):
                closed += 1
        if not closed:
            return "没记下，请再说一遍「已处理」。"
        return f"已记下，待处理 / 待回复共 {closed} 条，明早简报不再催。"
    reply = assign_reply_body(raw)
    if reply:
        quoted_head = (raw or "").partition("\n\n")[0]
        hits = [
            item
            for item in followups
            if format_assign_push(
                {
                    "asker_name": item.get("chat_name"),
                    "text": item.get("text"),
                }
            )
            == quoted_head
        ]
        if len(hits) == 1:
            item = hits[0]
            result = apply_action("fu_done", str(item.get("key") or ""))
            if not result.startswith("已记下"):
                return result
            who = str(item.get("chat_name") or "对方").strip()
            task = str(item.get("text") or "").replace("\n", " ").strip()
            if len(task) > 72:
                task = task[:72] + "…"
            return f"已读回引，{who}「{task}」这条已解决，不再催。"
        if len(hits) > 1:
            return "回引内容对上多条重复任务，先不销账：\n" + _list_items(hits)
        return "我读到了你回复的原消息，但它已经不在待处理里；没有动其他条。"
    weak = not hint
    if weak:
        hits = match_pending("", items)
        if not hits:
            hits = match_pending("", followups)
        if not hits:
            hits = match_pending("", inbox_as_pending())
    else:
        hits = match_pending(hint, items)
        if not hits:
            hits = match_pending(hint, followups)
        if not hits:
            hits = match_pending(hint, inbox_as_pending())
    if not hits:
        leftover = (
            match_pending("", items)
            or followups
            or match_pending("", inbox_as_pending())
        )
        if leftover:
            return "没对上待处理。当前还有：\n" + _list_items(leftover)
        return "没对上待处理，本地也没有今早那批待办。先说「简报」我再列一次。"
    if len(hits) > 1:
        return "对上好几条，再说清楚点（人名、群名或原文几个字）：\n" + _list_items(hits)
    item = hits[0]
    key = str(item.get("key") or "")
    name = str(item.get("chat_name") or "那条")
    if key.startswith("fu:"):
        return apply_action("fu_done", key)
    if not mark_resolved(
        key,
        source="text",
        chat_name=name,
        snippet=str(item.get("text") or ""),
        tag=str(item.get("tag") or ""),
        link=str(item.get("link") or ""),
    ):
        return "没记下，请再说一遍「已处理」。"
    return f"已记下，明早简报不再列 {name} 那条。"


def confirm_card(key: str) -> str:
    items = load_pending()
    name = "那条"
    tag = ""
    link = ""
    for item in items:
        if str(item.get("key") or "") == key:
            name = str(item.get("chat_name") or name)
            tag = str(item.get("tag") or "")
            link = str(item.get("link") or "")
            break
    snippet = ""
    for item in items:
        if str(item.get("key") or "") == key:
            snippet = str(item.get("text") or "")
            break
    if not mark_resolved(
        key, source="card", chat_name=name, snippet=snippet, tag=tag, link=link
    ):
        return "没记下，请再说一遍「已处理」。"
    return f"已记下，明早简报不再列 {name} 那条。"


def pending_card(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Interactive card: 已处理 buttons only. Not aily 今日/未结束 filters."""
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": "点「已处理」或在单聊说「某群那条已处理」，明早简报不再催。",
            },
        }
    ]
    for item in items[:5]:
        key = str(item.get("key") or "")
        title = str(item.get("chat_name") or "群")
        body = str(item.get("text") or "")[:80]
        tag = str(item.get("tag") or "")
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{title}**\n{body}" + (f"\n{tag}" if tag else ""),
                },
            }
        )
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "已处理"},
                        "type": "primary",
                        "value": {"act": "done", "key": key},
                    }
                ],
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "待处理 · 点已处理明早不催"},
        },
        "elements": elements,
    }

