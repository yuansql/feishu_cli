from __future__ import annotations

from dataclasses import dataclass
import json
import re

from .resolved import assign_reply_body, looks_like_instruction_blob, looks_like_resolve


WAKE_PREFIXES = ("工作伙伴", "伙伴", "/wp", "wp")

_HELP_EXACT = {"帮助", "使用说明", "你能做什么", "help", "?", "你好", "hi", "hello"}
_AILY_EXACT = {"aily", "豆包工作伙伴", "功能对齐", "能力对齐", "对齐矩阵"}
_TODAY_EXACT = {
    "今天",
    "今日",
    "今日安排",
    "今天有什么安排",
    "today",
    "日程",
    "今日日程",
    "今天的任务",
    "今日的任务",
    "今日任务",
    "今天任务",
    "今日待办",
    "今天待办",
}
_BRIEF_EXACT = {"早报", "简报", "每日简报", "昨天小结", "今日规划", "brief"}
_TOMORROW_EXACT = {
    "明天",
    "明日",
    "明天安排",
    "明日安排",
    "tomorrow",
    "明天的任务",
    "明日的任务",
    "明天任务",
    "明日任务",
    "明天待办",
    "明日待办",
}
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
_DIGEST_EXACT = {"今日待跟进", "催办", "待跟进"}
_WEEKLY_TASKS_EXACT = {"生成本周任务", "本周任务", "生成任务清单"}
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
_PLAN_EXACT = {"任务规划", "任务拆解", "规划任务", "拆解任务", "plan"}
_TASK_CONTINUE_EXACT = {
    "继续执行",
    "继续任务",
    "接着做",
    "继续推进",
    "继续干",
    "下一步",
}
_TASK_STATUS_EXACT = {"任务进度", "进行到哪", "进行到哪了", "进度如何", "任务状态"}
_TASK_CANCEL_EXACT = {"取消任务", "停止任务", "终止任务", "别做了", "停掉任务"}
_TASK_CONFIRM_EXACT = {
    "确认写入",
    "确认执行",
    "确认同步",
    "执行写入",
    "确认创建待办",
}
_WRITE_DOC_HINTS = (
    "需要给我写文档",
    "给我写文档",
    "写成文档",
    "生成文档",
    "写一份文档",
    "写个文档",
    "写个这个",
    "帮我写个",
    "给我写个",
    "帮我写",
    "给我写",
    "写文档",
)


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
    text = (raw or "").strip()
    folded = re.sub(r"[？?。！!…]+$", "", text).strip()
    return folded if folded else text


def looks_like_today_recap(raw: str) -> bool:
    """Natural retrospective question about my day, not a named artifact."""
    text = _folded(raw)
    if not any(scope in text for scope in ("今天", "今日")):
        return False
    if any(noun in text for noun in ("文档", "文件", "链接", "接口")) and any(
        change in text for change in ("修改", "变化", "内容", "更新")
    ):
        return False
    asks_activity = bool(
        re.search(r"(?:干|做|忙|完成).{0,2}(?:什么|啥|哪些)", text)
    )
    asks_messages = (
        any(noun in text for noun in ("消息", "聊天", "群聊"))
        and any(verb in text for verb in ("读", "看", "翻", "查", "汇总"))
    )
    return asks_activity or asks_messages


def looks_like_tasks(raw: str) -> bool:
    q = _folded(raw)
    key = q.lower()
    if q in _TASKS_EXACT or key in _TASKS_EXACT:
        return True
    if q in {"任务"}:
        return True
    if "任务" in q and "我的" in q:
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
    folded = _folded(q)
    key = folded.lower()
    if folded in _TODAY_EXACT or key in _TODAY_EXACT:
        return False
    if folded in _TOMORROW_EXACT or key in _TOMORROW_EXACT:
        return False
    if looks_like_chat_find(q) or looks_like_resolve(q) or looks_like_tasks(q):
        return False
    if q in _DIGEST_EXACT or q in _WEEKLY_TASKS_EXACT:
        return False
    if looks_like_person_talk(q) or looks_like_task_done(q) or looks_like_plan(q):
        return False
    if looks_like_write_doc(q):
        return False
    if len(q) > 16:
        return False
    if any(hint in q for hint in _WEEKLY_HINTS):
        return False
    if any(ch in q for ch in "。！？\n"):
        return False
    return True


_TASK_DONE_PHRASES = (
    "删除这个待办",
    "删掉这个待办",
    "删除待办",
    "删掉待办",
    "完成这个待办",
    "完成待办",
    "勾掉这个待办",
    "勾掉待办",
    "删掉这个任务",
    "删除这个任务",
    "完成这个任务",
)
_TASK_DONE_VERBS = ("删除", "删掉", "完成", "勾掉", "关掉")


def looks_like_task_done(raw: str) -> bool:
    text = raw or ""
    if looks_like_resolve(text):
        return False
    if looks_like_instruction_blob(text):
        return False
    if any(phrase in text for phrase in _TASK_DONE_PHRASES):
        return True
    if any(verb in text for verb in _TASK_DONE_VERBS) and any(
        word in text for word in ("待办", "任务")
    ):
        return True
    return False


def task_done_hint(raw: str) -> str:
    q = raw or ""
    q = re.sub(r"\s+(?:回复|回)\s+.+$", " ", q)
    for phrase in _TASK_DONE_PHRASES + ("这个待办", "这个任务", "待办", "任务"):
        q = q.replace(phrase, " ")
    for verb in _TASK_DONE_VERBS:
        q = q.replace(verb, " ")
    q = re.sub(r"[（(]\d{4}-\d{2}-\d{2}[）)]", " ", q)
    return re.sub(r"\s+", " ", q).strip(" ：:，,")


def looks_like_chat_find(raw: str) -> bool:
    text = raw or ""
    if "聊天" in text and any(
        key in text for key in ("读取", "读", "查看", "看看", "看下", "聊天记录")
    ):
        return True
    if "群" not in text:
        return False
    return any(
        key in text
        for key in ("哪个", "那个", "哪一个", "什么群", "叫什么", "是哪个", "是那个")
    )


def chat_search_query(raw: str) -> str:
    q = raw or ""
    for noise in (
        "读取我的聊天",
        "读取聊天记录",
        "读取聊天",
        "读我的聊天",
        "看我的聊天",
        "我的聊天记录",
        "我的聊天",
        "聊天记录",
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
        "还是不行",
    ):
        q = q.replace(noise, " ")
    q = re.sub(r"[？?。！!，,、]", " ", q)
    return re.sub(r"\s+", " ", q).strip()


_NOT_PERSON = frozenset(
    {
        "这个",
        "那个",
        "今天",
        "明天",
        "本周",
        "这周",
        "待办",
        "群里",
        "上面",
        "刚才",
        "文档",
        "周报",
        "简报",
        "审批",
        "纪要",
    }
)
_PERSON_NOISE = ("问一下", "帮我看", "帮我查", "看看", "请问", "想问")
_PERSON_RES = (
    re.compile(r"^(.{2,16}?)的回复"),
    re.compile(r"^(.{2,16}?)回了[没吗]"),
    re.compile(r"^(.{2,16}?)怎么说"),
    re.compile(r"^(.{2,16}?)说了什么"),
    re.compile(r"^(.{2,16}?)怎么回"),
    re.compile(r"^(.{2,16}?)回得怎么样"),
    re.compile(r"^(.{2,16}?)态度如何"),
)
_CLASSIFY_ACTIONS = frozenset(
    {
        "today",
        "today_recap",
        "tomorrow",
        "tasks",
        "brief",
        "weekly",
        "inbox",
        "minutes",
        "approval",
        "chats",
        "search",
        "read",
        "person",
        "plan",
        "write_doc",
        "resolve",
        "help",
        "digest",
        "weekly_tasks",
        "task_cancel",
    }
)


def person_query(raw: str) -> str:
    q = _folded(raw)
    for noise in _PERSON_NOISE:
        q = q.replace(noise, "")
    q = q.strip(" ，,：:")
    for cre in _PERSON_RES:
        match = cre.search(q)
        if not match:
            continue
        name = re.sub(r"\s+", "", match.group(1)).strip("的")
        if 2 <= len(name) <= 16 and name not in _NOT_PERSON:
            return name
    return ""


def looks_like_person_talk(raw: str) -> bool:
    return bool(person_query(raw))


def parse_classified(raw: str) -> Intent | None:
    blob = (raw or "").strip()
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(blob[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action") or "").strip().lower()
    if action not in _CLASSIFY_ACTIONS:
        return None
    query = str(data.get("query") or "").strip()
    if action in {"search", "read", "person", "chats", "plan", "write_doc"} and not query:
        return None
    if action not in {"search", "read", "person", "chats", "weekly", "plan", "write_doc"}:
        query = ""
    return Intent(action=action, query=query)


def plan_query(raw: str) -> str:
    q = _folded(raw)
    for pattern in (
        r"^(?:任务模式|深度任务|开始任务)\s*[:：]?\s*(.+)$",
        r"^(?:规划|计划|拆解|任务规划|任务拆解|plan)\s*[:：]?\s*(.+)$",
        r"^(?:帮我|帮忙)?(?:规划|计划|拆解|安排)\s*(.+)$",
        r"^(?:帮我|帮忙)?把\s*(.+?)\s*(?:拆成|拆解成|分成)(?:可执行)?步骤$",
        r"^(.+?)(?:怎么推进|如何推进|怎么拆|如何拆解)$",
    ):
        match = re.match(pattern, q, re.I)
        if match:
            return match.group(1).strip(" ：:，,")
    return ""


def looks_like_plan(raw: str) -> bool:
    folded = _folded(raw)
    return folded in _PLAN_EXACT or bool(plan_query(raw))


def looks_like_task_continue(raw: str) -> bool:
    folded = _folded(raw)
    return folded in _TASK_CONTINUE_EXACT


def looks_like_task_status(raw: str) -> bool:
    folded = _folded(raw)
    if folded in _TASK_STATUS_EXACT:
        return True
    return "任务进度" in raw or "进行到哪" in raw


def looks_like_task_confirm(raw: str) -> bool:
    folded = _folded(raw)
    if folded in _TASK_CONFIRM_EXACT:
        return True
    return bool(re.match(r"^确认执行第\s*\d+\s*步", folded))


def looks_like_task_cancel(raw: str) -> bool:
    return _folded(raw) in _TASK_CANCEL_EXACT


def looks_like_write_doc(raw: str) -> bool:
    text = raw or ""
    if text in _WRITE_WEEKLY:
        return False
    return any(hint in text for hint in _WRITE_DOC_HINTS)


def write_doc_query(raw: str) -> str:
    q = raw or ""
    url_m = _URL_RE.search(q)
    url = url_m.group(0).rstrip(")。,，") if url_m else ""
    for hint in _WRITE_DOC_HINTS:
        q = q.replace(hint, " ")
    q = re.sub(r"(这个|那篇|这篇|一下|文档)", " ", q)
    q = re.sub(r"[？?。！!]+", " ", q)
    q = re.sub(r"\s+", " ", q).strip(" ：:，,")
    if url and q:
        return f"{q} {url}"
    return url or q


def parse_intent(text: str) -> Intent:
    source = (text or "").strip()
    reply = assign_reply_body(source)
    if reply and looks_like_resolve(strip_wake_prefix(reply)):
        return Intent(action="resolve", query=source)
    raw = strip_wake_prefix(source)
    if not raw:
        return Intent(action="help")

    folded = _folded(raw)
    key = folded.lower()
    if folded in _HELP_EXACT or key in _HELP_EXACT:
        return Intent(action="help")
    if folded in _AILY_EXACT or key in _AILY_EXACT:
        return Intent(action="aily")
    if folded in _DIGEST_EXACT or key in _DIGEST_EXACT:
        return Intent(action="digest")
    if folded in _WEEKLY_TASKS_EXACT:
        return Intent(action="weekly_tasks")
    if looks_like_today_recap(raw):
        return Intent(action="today_recap")
    if folded in _TODAY_EXACT or key in _TODAY_EXACT:
        return Intent(action="today")
    if folded in _BRIEF_EXACT or key in _BRIEF_EXACT:
        return Intent(action="brief")
    if folded in _TOMORROW_EXACT or key in _TOMORROW_EXACT:
        return Intent(action="tomorrow")
    if folded in _WRITE_WEEKLY:
        return Intent(action="write_weekly")
    if looks_like_write_doc(raw):
        if "周报" in raw:
            return Intent(action="write_weekly")
        return Intent(action="write_doc", query=write_doc_query(raw))
    if folded in _WEEKLY_EXACT:
        return Intent(action="weekly")
    if looks_like_plan(raw):
        return Intent(action="plan", query=plan_query(raw))
    if looks_like_task_status(raw):
        return Intent(action="task_status")
    if looks_like_task_confirm(raw):
        return Intent(action="task_confirm")
    if looks_like_task_cancel(raw):
        return Intent(action="task_cancel")
    if looks_like_task_continue(raw):
        return Intent(action="task_continue")
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
    if looks_like_task_done(raw):
        return Intent(action="task_done", query=task_done_hint(raw))
    if looks_like_next_week_talk(raw):
        return Intent(action="weekly", query="next")
    if looks_like_weekly_talk(raw):
        return Intent(action="weekly")
    if looks_like_chat_find(raw):
        return Intent(action="chats", query=chat_search_query(raw))
    if looks_like_person_talk(raw):
        return Intent(action="person", query=person_query(raw))

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
