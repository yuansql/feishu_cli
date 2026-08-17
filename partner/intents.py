from __future__ import annotations

from dataclasses import dataclass
import re

from .resolved import looks_like_resolve


WAKE_PREFIXES = ("工作伙伴", "伙伴", "/wp", "wp")

_HELP_EXACT = {"帮助", "使用说明", "你能做什么", "help", "?", "你好", "hi", "hello"}
_TODAY_EXACT = {"今天", "今日", "今日安排", "今天有什么安排", "today", "日程", "今日日程"}
_BRIEF_EXACT = {"早报", "简报", "每日简报", "昨天小结", "今日规划", "brief"}
_TOMORROW_EXACT = {"明天", "明日", "明天安排", "明日安排", "tomorrow"}
_WEEKLY_EXACT = {
    "周报",
    "本周的周报",
    "本周周报",
    "这周周报",
    "本周安排",
    "这周安排",
    "本周日程",
    "这周日程",
    "本周计划",
    "这周计划",
    "本周",
}
_WRITE_WEEKLY = {"写周报", "生成周报", "出周报", "做周报"}
_WEEKLY_CONTINUE = {"继续", "再写", "改人话", "写人话", "像人写"}
_TASKS_EXACT = {"待办", "我的任务", "tasks", "todo", "待办事项"}
_MINUTES_EXACT = {"纪要", "会议纪要", "妙记", "minutes"}
_APPROVAL_EXACT = {"审批", "待审批", "我的审批", "approval"}
_CHATS_EXACT = {"群列表", "群", "chats", "会话"}
_INBOX_EXACT = {
    "谁找我",
    "关于我的",
    "收件箱",
    "inbox",
    "找我的",
    "找我的消息",
    "有人找我",
    "监视",
}
_URL_RE = re.compile(r"https://[^\s]*feishu\.cn/[^\s]+")
_WEEKLY_HINTS = ("人机", "像人写", "像人一样", "工作内容")
_WEEKLY_SCOPE = ("本周", "这周", "这星期")
_WEEKLY_TOPIC = ("工作", "内容", "安排", "总结", "查", "周报", "计划")
_NEXT_WEEK_EXACT = {
    "下周",
    "下周计划",
    "下周安排",
    "下周工作",
    "下周工作计划",
    "下星期计划",
    "下星期安排",
}


@dataclass(frozen=True)
class Intent:
    action: str
    query: str = ""
    chat_id: str = ""


def strip_wake_prefix(text: str) -> str:
    raw = (text or "").strip()
    raw = re.sub(r"@_user_\d+", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    lowered = raw.lower()
    for prefix in WAKE_PREFIXES:
        if raw.startswith(prefix):
            return raw[len(prefix) :].strip(" ：:，,")
        if lowered.startswith(prefix.lower()):
            return raw[len(prefix) :].strip(" ：:，,")
    return raw


def looks_like_wake(text: str) -> bool:
    """True only for 工作伙伴 / 伙伴 prefix — not because Feishu inserted @_user_1."""
    raw = (text or "").strip()
    lowered = raw.lower()
    for prefix in WAKE_PREFIXES:
        if raw.startswith(prefix) or lowered.startswith(prefix.lower()):
            return True
    cleaned = re.sub(r"@_user_\d+", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    lowered = cleaned.lower()
    for prefix in WAKE_PREFIXES:
        if cleaned.startswith(prefix) or lowered.startswith(prefix.lower()):
            return True
    return False


def _folded(raw: str) -> str:
    return re.sub(r"[？?。！!…]+$", "", (raw or "").strip()).strip()


def looks_like_tasks(raw: str) -> bool:
    q = _folded(raw)
    key = q.lower()
    if q in _TASKS_EXACT or key in _TASKS_EXACT:
        return True
    if q in {"今天的任务", "今日的任务", "今日任务", "今天任务", "任务"}:
        return True
    if "任务" in q and any(word in q for word in ("今天", "今日", "我的")):
        return True
    return False


def looks_like_next_week_talk(raw: str) -> bool:
    """Ask for my next-week plan — not a document title search."""
    if any(scope in raw for scope in _WEEKLY_SCOPE):
        return False
    if raw in _NEXT_WEEK_EXACT:
        return True
    if "下周" in raw and any(key in raw for key in ("计划", "安排", "工作")):
        return True
    return False


def looks_like_weekly_talk(raw: str) -> bool:
    """Natural-language weekly, including rewrite / continue — not a search query."""
    if raw in _WEEKLY_EXACT or raw in _WEEKLY_CONTINUE:
        return True
    if any(hint in raw for hint in _WEEKLY_HINTS):
        return True
    if "周报" in raw:
        return True
    if any(scope in raw for scope in _WEEKLY_SCOPE) and any(
        topic in raw for topic in _WEEKLY_TOPIC
    ):
        return True
    return False


def looks_like_bare_search(query: str) -> bool:
    """Unknown short keywords may search docs; complaint / rewrite sentences must not."""
    q = (query or "").strip()
    if not q or q in _WEEKLY_CONTINUE:
        return False
    if looks_like_next_week_talk(q) or looks_like_weekly_talk(q):
        return False
    if looks_like_chat_find(q) or looks_like_resolve(q) or looks_like_tasks(q):
        return False
    if len(q) > 24:
        return False
    if any(hint in q for hint in _WEEKLY_HINTS):
        return False
    if any(ch in q for ch in "。！？\n"):
        return False
    return True


def looks_like_chat_find(raw: str) -> bool:
    if "群" not in (raw or ""):
        return False
    return any(
        key in raw
        for key in ("哪个", "那个", "哪一个", "什么群", "叫什么", "是哪个", "是那个")
    )


def chat_search_query(raw: str) -> str:
    q = raw or ""
    for noise in (
        "我想问的是",
        "那个群是哪个",
        "那个群是那个",
        "是哪个群",
        "是那个群",
        "哪个群",
        "那个群",
        "哪一个群",
        "什么群",
        "是哪个",
        "是那个",
    ):
        q = q.replace(noise, " ")
    q = re.sub(r"[？?。！!，,、]", " ", q)
    return re.sub(r"\s+", " ", q).strip()


def parse_intent(text: str) -> Intent:
    raw = strip_wake_prefix(text)
    if not raw:
        return Intent(action="help")

    folded = _folded(raw)
    key = folded.lower()
    if folded in _HELP_EXACT or key in _HELP_EXACT:
        return Intent(action="help")
    if folded in _TODAY_EXACT or key in _TODAY_EXACT:
        return Intent(action="today")
    if folded in _BRIEF_EXACT or key in _BRIEF_EXACT:
        return Intent(action="brief")
    if folded in _TOMORROW_EXACT or key in _TOMORROW_EXACT:
        return Intent(action="tomorrow")
    if folded in _WRITE_WEEKLY:
        return Intent(action="write_weekly")
    if folded in _WEEKLY_EXACT:
        return Intent(action="weekly")
    if looks_like_tasks(raw):
        return Intent(action="tasks")
    if folded in _MINUTES_EXACT or key in _MINUTES_EXACT:
        return Intent(action="minutes")
    if folded in _APPROVAL_EXACT or key in _APPROVAL_EXACT:
        return Intent(action="approval")
    if folded in _CHATS_EXACT or key in _CHATS_EXACT:
        return Intent(action="chats")
    if raw in _INBOX_EXACT or key in _INBOX_EXACT or "谁找我" in raw or "找我的" in raw:
        return Intent(action="inbox")

    url_m = _URL_RE.search(raw)
    if url_m and raw.startswith("http"):
        return Intent(action="read", query=url_m.group(0).rstrip(")。,，"))

    search_m = re.match(r"^(搜索|搜|search)\s*[:：]?\s*(.+)$", raw, re.I)
    if search_m:
        return Intent(action="search", query=search_m.group(2).strip())

    if looks_like_resolve(raw):
        return Intent(action="resolve", query=raw)
    if looks_like_next_week_talk(raw):
        return Intent(action="weekly", query="next")
    if looks_like_weekly_talk(raw):
        return Intent(action="weekly")
    if looks_like_chat_find(raw):
        return Intent(action="chats", query=chat_search_query(raw))

    read_m = re.match(r"^(读|读取|read)\s+(.+)$", raw, re.I)
    if read_m:
        return Intent(action="read", query=read_m.group(2).strip())

    send_m = re.match(r"^(发|发送|send)\s+(oc_[a-zA-Z0-9]+)\s+(.+)$", raw, re.I)
    if send_m:
        return Intent(
            action="send",
            chat_id=send_m.group(2),
            query=send_m.group(3).strip(),
        )

    return Intent(action="unknown", query=raw)
