"""群聊防遗忘：本地跟进账。飞书表格是下游，失败不假装已写。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from typing import Any

from .events import InboundMessage, extract_inbound_message, should_reply
from .formatters import plain_im_text

CN_TZ = timezone(timedelta(hours=8))
DEFAULT_PATH = Path.home() / ".feishu-partner" / "followups.json"
DIGEST_STAMP = Path.home() / ".feishu-partner" / "digest-sent.on"
SCAN_EVERY = 180.0
CHAT_SYNC_EVERY = 3600.0
CHAT_WATCH_START_HOUR = 9
CHAT_WATCH_END_HOUR = 18
SYNC_STAMP = Path.home() / ".feishu-partner" / "user-chat-sync.ts"

DEFAULT_TEMPLATES = (
    {"标题": "填写任务清单", "启用": "是"},
    {"标题": "同步上周未闭环", "启用": "是"},
    {"标题": "核对表格艾特", "启用": "是"},
)

_WEEKDAY = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}
_ASSIGN_RE = re.compile(r"请([^\s，,。@]{2,4}?)(?:处理|跟进|确认|看一下|负责)")
_GIVE_RE = re.compile(r"交给([^\s，,。@]{2,4})")
_NOT_PERSON = frozenset(
    {"大家", "同事", "一下", "这个", "那个", "今天", "明天", "相关", "尽快", "帮忙", "群里"}
)
_OPEN = frozenset({"open", "snooze"})
_WORK_HINTS = (
    "研究下",
    "研究一下",
    "写份",
    "写一个",
    "写个",
    "写一下",
    "好好体验",
    "体验下",
    "一起做",
    "出个方案",
    "做一下",
    "跟一下",
)
_NOT_ASSIGN = frozenset({"在的", "好的", "收到", "嗯", "ok", "OK", "okay"})


def ledger_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    override = os.environ.get("FEISHU_PARTNER_FOLLOWUPS")
    if override:
        return Path(override).expanduser()
    return DEFAULT_PATH


def load_items(path: Path | None = None) -> list[dict[str, Any]]:
    dest = ledger_path(path)
    if not dest.exists():
        return []
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def open_followups_as_pending(path: Path | None = None) -> list[dict[str, Any]]:
    """Same shape as brief pending, so P2P「解决了」能销同事派活。"""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in load_items(path):
        if str(item.get("status") or "") not in _OPEN:
            continue
        key = str(item.get("id") or "")
        if not key:
            continue
        who = str(
            item.get("asker_name") or item.get("assignee_name") or item.get("chat_name") or "对方"
        )
        text = str(item.get("text") or "")
        fingerprint = f"{who}|{text.strip()}"
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        out.append(
            {
                "key": key,
                "chat_name": who,
                "text": text,
                "tag": "跟进",
            }
        )
    return out


def save_items(items: list[dict[str, Any]], path: Path | None = None) -> None:
    dest = ledger_path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps({"items": items}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def extract_due(text: str, today: date) -> date | None:
    raw = text or ""
    match = re.search(r"(\d{1,2})月(\d{1,2})[日号]", raw)
    if match:
        try:
            return date(today.year, int(match.group(1)), int(match.group(2)))
        except ValueError:
            return None
    if "后天" in raw:
        return today + timedelta(days=2)
    if "明天" in raw:
        return today + timedelta(days=1)
    if "今天" in raw:
        return today
    match = re.search(r"下[周星期]([一二三四五六日天])", raw)
    if match:
        return _weekday_on_week(today, _WEEKDAY[match.group(1)], weeks_ahead=1)
    match = re.search(r"(?:这|本)[周星期]([一二三四五六日天])", raw)
    if match:
        return _weekday_on_week(today, _WEEKDAY[match.group(1)], weeks_ahead=0)
    match = re.search(r"[周星期]([一二三四五六日天])", raw)
    if match:
        target = _WEEKDAY[match.group(1)]
        delta = (target - today.weekday()) % 7
        return today + timedelta(days=delta)
    return None


def _weekday_on_week(today: date, weekday: int, *, weeks_ahead: int) -> date:
    monday = today - timedelta(days=today.weekday()) + timedelta(days=7 * weeks_ahead)
    return monday + timedelta(days=weekday)


def extract_assignee_b(
    text: str,
    mentions: tuple[tuple[str, str], ...] = (),
    *,
    user_open_id: str,
    bot_open_id: str = "",
) -> tuple[str, str]:
    others = [
        (oid, name)
        for oid, name in (mentions or ())
        if oid and oid not in {user_open_id, bot_open_id}
    ]
    cleaned = re.sub(r"@_user_\d+", " ", text or "")
    name = ""
    match = _ASSIGN_RE.search(cleaned) or _GIVE_RE.search(cleaned)
    if match:
        cand = match.group(1).strip()
        if cand and cand not in _NOT_PERSON:
            name = cand
    if name:
        for oid, labeled in others:
            if labeled and (labeled == name or name in labeled or labeled in name):
                return labeled, oid
        return name, ""
    if user_open_id and any(oid == user_open_id for oid, _ in (mentions or ())):
        return "", user_open_id
    if others:
        oid, labeled = others[0]
        return labeled, oid
    return "", ""


def looks_like_work_assign(text: str) -> bool:
    raw = (text or "").strip()
    if len(raw) < 10 or raw in _NOT_ASSIGN:
        return False
    if raw.lower() in {"ok", "okay"}:
        return False
    return any(hint in raw for hint in _WORK_HINTS)


def _mentions_of(msg: InboundMessage) -> tuple[tuple[str, str], ...]:
    pairs = getattr(msg, "mentions", ()) or ()
    if pairs:
        return tuple(pairs)
    return tuple((oid, "") for oid in msg.mention_ids)


def ingest(
    msg: InboundMessage,
    *,
    user_open_id: str,
    bot_open_id: str = "",
    path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    if msg.sender_type in {"app", "bot"}:
        return None
    if bot_open_id and msg.sender_id == bot_open_id:
        return None
    if msg.chat_type not in {"group", "p2p"}:
        return None
    if msg.chat_type == "group" and should_reply(msg, bot_open_id):
        return None
    if msg.chat_type == "p2p" and msg.sender_id == user_open_id:
        return None
    now = now or datetime.now(CN_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    items = load_items(path)
    _touch_replies(items, msg, user_open_id=user_open_id, now=now)
    created: dict[str, Any] | None = None
    mid = msg.message_id or ""
    if mid and any(str(old.get("message_id") or "") == mid for old in items):
        save_items(items, path)
        return None
    pairs = _mentions_of(msg)
    if msg.sender_id == user_open_id:
        others = [
            (oid, name)
            for oid, name in pairs
            if oid and oid not in {user_open_id, bot_open_id}
        ]
        if others:
            oid, name = others[0]
            created = _new_item(
                msg,
                kind="self_transfer",
                asker_id=user_open_id,
                asker_name=getattr(msg, "sender_name", "") or "",
                assignee_id=oid,
                assignee_name=name,
                now=now,
            )
            created["last_user_at"] = now.isoformat(timespec="seconds")
    else:
        b_name, b_id = extract_assignee_b(
            msg.text,
            pairs,
            user_open_id=user_open_id,
            bot_open_id=bot_open_id,
        )
        about_user = bool(user_open_id and user_open_id in msg.mention_ids)
        if b_id and b_id != user_open_id:
            created = _new_item(
                msg,
                kind="assign_b",
                asker_id=msg.sender_id,
                asker_name=getattr(msg, "sender_name", "") or "",
                assignee_id=b_id,
                assignee_name=b_name,
                now=now,
            )
        elif b_name and b_id != user_open_id:
            created = _new_item(
                msg,
                kind="assign_b",
                asker_id=msg.sender_id,
                asker_name=getattr(msg, "sender_name", "") or "",
                assignee_id=b_id,
                assignee_name=b_name,
                now=now,
            )
        elif about_user:
            created = _new_item(
                msg,
                kind="inbox",
                asker_id=msg.sender_id,
                asker_name=getattr(msg, "sender_name", "") or "",
                assignee_id=user_open_id,
                assignee_name="",
                now=now,
            )
        elif looks_like_work_assign(msg.text) and (
            msg.chat_type == "p2p" or "你" in (msg.text or "")
        ):
            created = _new_item(
                msg,
                kind="direct",
                asker_id=msg.sender_id,
                asker_name=getattr(msg, "sender_name", "") or "",
                assignee_id=user_open_id,
                assignee_name="",
                now=now,
            )
    if created:
        who = str(
            created.get("asker_name") or created.get("assignee_name") or ""
        ).strip()
        text = str(created.get("text") or "").strip()
        for old in items:
            if str(old.get("status") or "") not in _OPEN:
                continue
            old_who = str(
                old.get("asker_name") or old.get("assignee_name") or ""
            ).strip()
            if old_who == who and str(old.get("text") or "").strip() == text:
                save_items(items, path)
                return None
        items.append(created)
    save_items(items, path)
    return created


def _new_item(
    msg: InboundMessage,
    *,
    kind: str,
    asker_id: str,
    asker_name: str,
    assignee_id: str,
    assignee_name: str,
    now: datetime,
) -> dict[str, Any]:
    today = now.astimezone(CN_TZ).date()
    due = extract_due(msg.text, today)
    mid = msg.message_id or f"{msg.chat_id}:{now.timestamp()}"
    return {
        "id": f"fu:{mid}",
        "kind": kind,
        "chat_id": msg.chat_id,
        "chat_name": msg.chat_name,
        "message_id": msg.message_id,
        "asker_id": asker_id,
        "asker_name": asker_name,
        "assignee_id": assignee_id,
        "assignee_name": assignee_name,
        "text": (msg.text or "").strip(),
        "due": due.isoformat() if due else "",
        "status": "open",
        "snooze_until": "",
        "created_at": now.isoformat(timespec="seconds"),
        "last_other_at": "",
        "last_user_at": "",
    }


def _touch_replies(
    items: list[dict[str, Any]],
    msg: InboundMessage,
    *,
    user_open_id: str,
    now: datetime,
) -> None:
    ts = now.isoformat(timespec="seconds")
    sender_name = getattr(msg, "sender_name", "") or ""
    for item in items:
        if str(item.get("status") or "") not in _OPEN:
            continue
        if str(item.get("chat_id") or "") != msg.chat_id:
            continue
        if msg.sender_id == user_open_id:
            item["last_user_at"] = ts
            continue
        assignee_id = str(item.get("assignee_id") or "")
        assignee_name = str(item.get("assignee_name") or "")
        asker_id = str(item.get("asker_id") or "")
        if msg.sender_id and msg.sender_id in {assignee_id, asker_id}:
            item["last_other_at"] = ts
        elif assignee_name and assignee_name in sender_name:
            item["last_other_at"] = ts


def _iso_week(value: str | date | None) -> tuple[int, int] | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        iso = value.isocalendar()
        return iso[0], iso[1]
    raw = str(value)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed_d = date.fromisoformat(raw[:10])
        except ValueError:
            return None
        iso = parsed_d.isocalendar()
        return iso[0], iso[1]
    iso = parsed.date().isocalendar()
    return iso[0], iso[1]


def _active(item: dict[str, Any], today: date) -> bool:
    status = str(item.get("status") or "")
    if status == "snooze":
        until = str(item.get("snooze_until") or "")
        return bool(until) and until <= today.isoformat()
    return status == "open"


def _is_due(item: dict[str, Any], today: date) -> bool:
    if str(item.get("due") or "") != today.isoformat():
        return False
    other = _iso_week(str(item.get("last_other_at") or "") or None)
    if other is None:
        return True
    replied = str(item.get("last_other_at") or "")[:10]
    return replied < today.isoformat()


def _is_chase(item: dict[str, Any], today: date) -> bool:
    prev = (today - timedelta(days=7)).isocalendar()
    cur = today.isocalendar()
    other = _iso_week(str(item.get("last_other_at") or "") or None)
    user = _iso_week(str(item.get("last_user_at") or "") or None)
    if other != (prev[0], prev[1]):
        return False
    return user != (cur[0], cur[1])


def digest_buckets(
    items: list[dict[str, Any]],
    today: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    due: list[dict[str, Any]] = []
    chase: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not _active(item, today):
            continue
        who = str(item.get("asker_name") or item.get("assignee_name") or "对方")
        text = str(item.get("text") or "").strip()
        fingerprint = f"{who}|{text}"
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        if _is_due(item, today):
            due.append(item)
        elif _is_chase(item, today):
            chase.append(item)
        else:
            other.append(item)
    return due, chase, other


def format_open_followups(
    items: list[dict[str, Any]],
    today: date | None = None,
) -> str:
    today = today or datetime.now(CN_TZ).date()
    lines: list[str] = []
    for item in items:
        if not _active(item, today):
            continue
        lines.append(_work_line(item))
    return "\n".join(lines)


def followups_for_command(*, path: Path | None = None, scan: bool = True) -> str:
    if scan:
        try:
            scan_recent_p2p(path=path)
        except Exception:
            pass
    return format_open_followups(load_items(path))


def _work_line(item: dict[str, Any]) -> str:
    kind = str(item.get("kind") or "")
    text = str(item.get("text") or "").replace("\n", " ").strip()
    if len(text) > 60:
        text = text[:60] + "…"
    if kind == "direct":
        who = str(item.get("asker_name") or "有人").strip() or "有人"
        return f"- {who}：{text}"
    return _digest_line(item)


def format_digest_text(
    due: list[dict[str, Any]],
    chase: list[dict[str, Any]],
    other: list[dict[str, Any]],
) -> str:
    if not due and not chase and not other:
        return "今天没有待跟进。"
    lines = ["今日待跟进"]
    if due:
        lines.append("今天要去问谁为什么没给答复")
        lines.extend(_digest_line(item) for item in due)
    if chase:
        lines.append("这周要去催谁")
        lines.extend(_digest_line(item) for item in chase)
    if other:
        lines.append("还在跟")
        lines.extend(_digest_line(item) for item in other)
    return "\n".join(lines)


def _digest_line(item: dict[str, Any]) -> str:
    who = str(item.get("assignee_name") or item.get("asker_name") or "对方")
    where = str(item.get("chat_name") or "群")
    text = plain_im_text(str(item.get("text") or ""))
    if len(text) > 60:
        text = text[:60] + "…"
    return f"- {who}（{where}）{text}"


def apply_action(
    act: str,
    key: str,
    *,
    path: Path | None = None,
    today: date | None = None,
) -> str:
    items = load_items(path)
    found: dict[str, Any] | None = None
    for item in items:
        if str(item.get("id") or "") == key:
            found = item
            break
    if found is None:
        return "没找到这条催办。"
    today = today or datetime.now(CN_TZ).date()
    if act == "fu_done":
        who = str(found.get("asker_name") or found.get("assignee_name") or "对方")
        text = str(found.get("text") or "").strip()
        for item in items:
            if str(item.get("status") or "") not in _OPEN:
                continue
            same_who = (
                str(item.get("asker_name") or item.get("assignee_name") or "对方") == who
            )
            same_text = str(item.get("text") or "").strip() == text
            if same_who and same_text:
                item["status"] = "done"
        save_items(items, path)
        return f"已记下，{who} 那条不再催。"
    who = str(found.get("assignee_name") or found.get("asker_name") or "对方")
    if act == "fu_snooze":
        found["status"] = "snooze"
        found["snooze_until"] = (today + timedelta(days=1)).isoformat()
        save_items(items, path)
        return f"好，明天再催 {who}。"
    if act == "fu_ignore":
        found["status"] = "ignore"
        save_items(items, path)
        return f"已忽略 {who} 那条。"
    return "这个按钮我不认。"


def record_mentions_user(
    fields: Any,
    *,
    names: tuple[str, ...] = (),
    user_open_id: str = "",
) -> bool:
    blob = json.dumps(fields, ensure_ascii=False)
    if user_open_id and user_open_id in blob:
        return True
    return any(name and name in blob for name in names)


def should_scan_bitable(last: float, now: float, every: float = SCAN_EVERY) -> bool:
    return now - last >= every


def in_chat_watch_window(now: datetime | None = None) -> bool:
    now = now or datetime.now(CN_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    hour = now.astimezone(CN_TZ).hour
    return CHAT_WATCH_START_HOUR <= hour < CHAT_WATCH_END_HOUR


def should_sync_user_chats(
    last: float, now: float, every: float = CHAT_SYNC_EVERY
) -> bool:
    return now - last >= every


def format_assign_push(item: dict[str, Any]) -> str:
    who = str(item.get("asker_name") or "有人").strip() or "有人"
    text = str(item.get("text") or "").replace("\n", " ").strip() or "(无正文)"
    if len(text) > 120:
        text = text[:120] + "…"
    return f"刚记下{who}派你的活：\n{text}"


def weekly_rows(
    templates: list[dict[str, Any]],
    followups: list[dict[str, Any]],
    today: date,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in templates:
        enabled = str(item.get("启用") or item.get("enabled") or "是")
        if enabled in {"否", "false", "0", "False"}:
            continue
        title = str(item.get("标题") or item.get("title") or "").strip()
        if not title:
            continue
        rows.append(
            {
                "标题": title,
                "类型": "例行",
                "对接人": "",
                "截止": "",
                "状态": "未完成",
                "来源": "模板",
            }
        )
    for item in followups:
        if str(item.get("status") or "") not in _OPEN:
            continue
        who = str(item.get("assignee_name") or "对方")
        text = str(item.get("text") or "").replace("\n", " ").strip()
        title = f"催{who}：{text[:40]}" if text else f"催{who}"
        rows.append(
            {
                "标题": title,
                "类型": "跟进",
                "对接人": who,
                "截止": str(item.get("due") or today.isoformat()),
                "状态": "未完成",
                "来源": str(item.get("kind") or "跟进"),
            }
        )
    return rows


def add_bitable_hit(
    *,
    who: str,
    title: str,
    record_id: str,
    table: str,
    path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(CN_TZ)
    items = load_items(path)
    key = f"fu:bitable:{record_id}"
    for old in items:
        if str(old.get("id") or "") == key:
            return old
    item = {
        "id": key,
        "kind": "bitable_at",
        "chat_id": "",
        "chat_name": table or "多维表",
        "message_id": record_id,
        "asker_id": "",
        "asker_name": who,
        "assignee_id": "",
        "assignee_name": who,
        "text": title,
        "due": now.date().isoformat(),
        "status": "open",
        "snooze_until": "",
        "created_at": now.isoformat(timespec="seconds"),
        "last_other_at": "",
        "last_user_at": "",
    }
    items.append(item)
    save_items(items, path)
    return item


def is_weekday(day: date) -> bool:
    return day.weekday() < 5


def already_pushed_digest(now: datetime | None = None) -> bool:
    now = now or datetime.now(CN_TZ)
    if not DIGEST_STAMP.exists():
        return False
    return DIGEST_STAMP.read_text(encoding="utf-8").strip() == now.astimezone(CN_TZ).date().isoformat()


def mark_pushed_digest(now: datetime | None = None) -> None:
    now = now or datetime.now(CN_TZ)
    DIGEST_STAMP.parent.mkdir(parents=True, exist_ok=True)
    DIGEST_STAMP.write_text(
        now.astimezone(CN_TZ).date().isoformat() + "\n", encoding="utf-8"
    )


def digest_payload(today: date | None = None, path: Path | None = None) -> dict[str, Any]:
    today = today or datetime.now(CN_TZ).date()
    due, chase, other = digest_buckets(load_items(path), today)
    return {
        "today": today.isoformat(),
        "due": due,
        "chase": chase,
        "other": other,
        "text": format_digest_text(due, chase, other),
    }


def _chat_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return []
    for key in ("items", "chats", "items_list"):
        raw = data.get(key)
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
    return []


def _msg_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return []
    for key in ("messages", "items"):
        raw = data.get(key)
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
    return []


def _inbound_from_history(
    msg: dict[str, Any],
    *,
    chat_id: str,
    chat_name: str,
    chat_type: str,
) -> InboundMessage | None:
    content = msg.get("content")
    if isinstance(content, dict):
        text = str(content.get("text") or "")
    else:
        text = str(msg.get("text") or content or "")
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "chat_type": chat_type,
        "chat_name": chat_name,
        "content": text,
        "message_id": str(msg.get("message_id") or msg.get("id") or ""),
        "sender_type": str(msg.get("sender_type") or "user"),
        "sender_id": str(msg.get("sender_id") or ""),
        "sender_name": str(msg.get("sender_name") or chat_name),
    }
    sender = msg.get("sender")
    if isinstance(sender, dict):
        payload["sender"] = sender
        payload["sender_name"] = str(
            sender.get("name") or payload["sender_name"]
        )
    return extract_inbound_message(payload)


def load_chat_sync_since(now: datetime) -> datetime:
    if not SYNC_STAMP.exists():
        return now - timedelta(hours=1)
    raw = SYNC_STAMP.read_text(encoding="utf-8").strip()
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return now - timedelta(hours=1)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ)


def mark_chat_synced(now: datetime) -> None:
    SYNC_STAMP.parent.mkdir(parents=True, exist_ok=True)
    SYNC_STAMP.write_text(now.isoformat(timespec="seconds") + "\n", encoding="utf-8")


def sync_user_chats(
    *,
    start: datetime,
    path: Path | None = None,
    now: datetime | None = None,
    limit_chats: int = 8,
    page_size: int = 15,
) -> list[dict[str, Any]]:
    """Pull recent chats as the logged-in user — aily-style, not messages-search."""
    from .ids import BOT_OPEN_ID, P2P_CHAT_ID, USER_OPEN_ID
    from .lark import run_lark

    now = now or datetime.now(CN_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    now = now.astimezone(CN_TZ)
    if start.tzinfo is None:
        start = start.replace(tzinfo=CN_TZ)
    listed = run_lark(
        [
            "im",
            "+chat-list",
            "--types=p2p,group",
            "--page-size",
            str(max(limit_chats, 8)),
            "--sort",
            "active_time",
        ],
        as_identity="user",
    )
    created: list[dict[str, Any]] = []
    start_iso = start.astimezone(CN_TZ).isoformat()
    for chat in _chat_items(listed)[:limit_chats]:
        cid = str(chat.get("chat_id") or "")
        if not cid or cid == P2P_CHAT_ID:
            continue
        mode = str(chat.get("chat_mode") or chat.get("chat_type") or "p2p")
        chat_type = "p2p" if mode == "p2p" else "group"
        name = str(chat.get("name") or chat.get("chat_name") or "")
        payload = run_lark(
            [
                "im",
                "+chat-messages-list",
                "--chat-id",
                cid,
                "--start",
                start_iso,
                "--order",
                "desc",
                "--page-size",
                str(page_size),
                "--no-reactions",
            ],
            as_identity="user",
        )
        for raw in _msg_items(payload):
            msg = _inbound_from_history(
                raw, chat_id=cid, chat_name=name, chat_type=chat_type
            )
            if msg is None:
                continue
            item = ingest(
                msg,
                user_open_id=USER_OPEN_ID,
                bot_open_id=BOT_OPEN_ID,
                path=path,
                now=now,
            )
            if item is not None:
                created.append(item)
    return created


def scan_recent_p2p(*, path: Path | None = None, now: datetime | None = None) -> int:
    now = now or datetime.now(CN_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    now = now.astimezone(CN_TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return len(
        sync_user_chats(
            start=start, path=path, now=now, limit_chats=15, page_size=30
        )
    )


def digest_text(
    path: Path | None = None,
    today: date | None = None,
    *,
    scan_p2p: bool = True,
) -> str:
    if scan_p2p:
        try:
            scan_recent_p2p(path=path)
        except Exception:
            pass
    data = digest_payload(today=today, path=path)
    return str(data["text"])


def push_digest(*, force: bool = False, now: datetime | None = None) -> str:
    from .actions import send_card, send_text
    from .followup_card import digest_card
    from .ids import P2P_CHAT_ID

    now = now or datetime.now(CN_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    now = now.astimezone(CN_TZ)
    today = now.date()
    if not is_weekday(today):
        return "周末不推待跟进。"
    from .brief import claim_daily_stamp

    if not claim_daily_stamp(DIGEST_STAMP, now, force=force):
        return "今日待跟进已推过。"
    try:
        scan_recent_p2p(now=now)
    except Exception:
        pass
    data = digest_payload(today=today)
    card = digest_card(
        due=data["due"],
        chase=data["chase"],
        other=data["other"],
        today=today,
    )
    card_res = send_card(P2P_CHAT_ID, card, as_identity="bot")
    if card_res == "已发送。":
        return "已推送今日待跟进。\n\n" + data["text"]
    result = send_text(P2P_CHAT_ID, data["text"], as_identity="bot")
    if result != "已发送。":
        return card_res + "\n" + result + "\n\n" + data["text"]
    return "卡片没发出，已改发文字。\n" + card_res + "\n\n" + data["text"]
