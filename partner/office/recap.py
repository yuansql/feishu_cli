"""Agent-style day recap: observe messages, analyze evidence, keep context."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any

from ..compose.formatters import _items, format_lark_error
from ..core.ids import P2P_CHAT_ID, USER_OPEN_ID
from ..core.lark import run_lark

CN_TZ = timezone(timedelta(hours=8))
_ACKS = frozenset(
    {
        "好",
        "好的",
        "嗯",
        "行",
        "可以",
        "收到",
        "ok",
        "OK",
        "对",
        "对的",
        "[赞]",
    }
)
_WORK_HINTS = (
    "A6",
    "A8",
    "APP",
    "AI中台",
    "接口",
    "预发",
    "测试",
    "上线",
    "环境",
    "终端",
    "代码",
    "仓库",
    "UI",
    "bug",
    "修复",
    "需求",
    "项目",
    "数据",
    "同步",
    "打包",
    "软件包",
    "升级",
    "文档",
    "周报",
    "scene",
    "Vue",
)
_DONE_HINTS = ("已完成", "已经完成", "改完", "修好", "已修复", "已上线", "可以用了")
_PENDING_HINTS = ("还没", "没有上", "等", "待", "需要", "一会", "试试", "确认")
_NON_WORK_HINTS = (
    "薅羊毛",
    "会员",
    "记忆 skill",
    "WorkBuddy",
    "捡钱",
    "手机号邮箱",
    "模型比较垃圾",
    "点升级",
)


@dataclass(frozen=True)
class EvidenceBundle:
    context: str
    message_count: int
    evidence_count: int
    error: str = ""


@dataclass(frozen=True)
class DayRecap:
    text: str
    context: str
    message_count: int
    evidence_count: int


def _message_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return []
    rows = data.get("messages") or data.get("items") or []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, dict):
        raw = content.get("text") or content.get("content") or ""
    else:
        raw = content or message.get("text") or ""
    text = re.sub(r"</?p[^>]*>", " ", str(raw))
    return re.sub(r"\s+", " ", text).strip()


def _sender(message: dict[str, Any]) -> tuple[str, str]:
    sender = message.get("sender")
    if not isinstance(sender, dict):
        return "", ""
    return (
        str(sender.get("id") or sender.get("open_id") or ""),
        str(sender.get("name") or sender.get("sender_name") or ""),
    )


def _when(message: dict[str, Any]) -> datetime:
    raw = str(message.get("create_time") or "")
    if raw.isdigit():
        value = int(raw)
        if value > 10_000_000_000:
            value //= 1000
        return datetime.fromtimestamp(value, CN_TZ)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=CN_TZ)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ)


def _is_substantive(text: str) -> bool:
    compact = (text or "").strip(" ：:，,。！!?？")
    if compact in _ACKS or len(compact) < 4:
        return False
    if re.fullmatch(r"(?:!\[[^\]]*\]\([^)]+\)|\[Image:[^\]]+\])", compact):
        return False
    if "今天" in compact and (
        re.search(r"(?:干|做|忙|完成).{0,2}(?:什么|啥|哪些)", compact)
        or (
            any(noun in compact for noun in ("消息", "聊天", "群聊"))
            and any(verb in compact for verb in ("读", "看", "翻", "查", "汇总"))
        )
    ):
        return False
    return True


def _evidence_score(message: dict[str, Any]) -> int:
    body = _message_text(message)
    sender_id, _name = _sender(message)
    score = 4 if sender_id == USER_OPEN_ID else 0
    score += 3 * sum(hint in body for hint in _DONE_HINTS)
    score += 2 * sum(hint in body for hint in _PENDING_HINTS)
    score += min(6, sum(hint in body for hint in _WORK_HINTS))
    return score


def _chat_names() -> dict[str, str]:
    payload = run_lark(
        [
            "im",
            "+chat-list",
            "--types",
            "p2p,group",
            "--sort",
            "active_time",
            "--page-size",
            "100",
            "--page-limit",
            "5",
        ],
        as_identity="user",
        timeout=120,
    )
    names: dict[str, str] = {}
    for chat in _items(payload, "chats", "items"):
        if not isinstance(chat, dict):
            continue
        chat_id = str(chat.get("chat_id") or "")
        name = str(chat.get("name") or chat.get("chat_name") or "").strip()
        if chat_id and name:
            names[chat_id] = name
    return names


def collect_today_evidence(now: datetime | None = None) -> EvidenceBundle:
    current = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    end = current.replace(hour=23, minute=59, second=59, microsecond=0)
    return collect_message_evidence(start, end, label=f"日期：{current.date().isoformat()}")


def collect_week_evidence(
    start: datetime,
    end: datetime,
) -> EvidenceBundle:
    label = f"周期：{start.date().isoformat()} ~ {end.date().isoformat()}"
    return collect_message_evidence(start, end, label=label, evidence_cap=140)


def collect_message_evidence(
    start: datetime,
    end: datetime,
    *,
    label: str = "",
    evidence_cap: int = 90,
) -> EvidenceBundle:
    names = _chat_names()
    payload = run_lark(
        [
            "im",
            "+messages-search",
            "--query",
            "",
            "--start",
            start.isoformat(),
            "--end",
            end.isoformat(),
            "--page-size",
            "50",
            "--page-all",
            "--no-reactions",
        ],
        as_identity="user",
        timeout=180,
    )
    if payload.get("ok") is False:
        return EvidenceBundle("", 0, 0, format_lark_error(payload))
    messages = sorted(_message_rows(payload), key=_when)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for message in messages:
        if message.get("deleted"):
            continue
        chat_id = str(message.get("chat_id") or "")
        if not chat_id:
            continue
        grouped[chat_id].append(message)

    candidates: list[tuple[int, str, list[dict[str, Any]]]] = []
    for chat_id, rows in grouped.items():
        name = names.get(chat_id) or str(rows[0].get("chat_name") or "会话")
        agent_chat = chat_id == P2P_CHAT_ID or "飞书 CLI" in name
        self_indices = [
            index
            for index, message in enumerate(rows)
            if _sender(message)[0] == USER_OPEN_ID
            and _is_substantive(_message_text(message))
        ]
        if not self_indices:
            continue
        selected: set[int] = set()
        for index in self_indices:
            selected.add(index)
            if agent_chat:
                continue
            for nearby in (index - 1, index + 1):
                if 0 <= nearby < len(rows):
                    selected.add(nearby)
        raw_chosen = [rows[index] for index in sorted(selected)]
        had_non_work = any(
            hint in _message_text(message)
            for message in raw_chosen
            for hint in _NON_WORK_HINTS
        )
        chosen = [
            rows[index]
            for index in sorted(selected)
            if not any(
                hint in _message_text(rows[index])
                for hint in _NON_WORK_HINTS
            )
        ]
        if not chosen:
            continue
        joined = " ".join(_message_text(row) for row in chosen)
        # 写周报时跳过与机器人单聊里的「写周报/帮助」自指噪声（「周报」词本身不算业务）
        work_hints_substantive = tuple(h for h in _WORK_HINTS if h != "周报")
        if agent_chat and not any(hint in joined for hint in work_hints_substantive):
            continue
        work_hits = sum(hint in joined for hint in _WORK_HINTS)
        if (
            not agent_chat
            and had_non_work
            and not any(
                hint in joined
                for hint in ("预发", "A6", "A8", "接口", "项目", "文档", "周报")
            )
        ):
            continue
        done_hits = sum(hint in joined for hint in _DONE_HINTS)
        score = min(len(self_indices), 5) + 5 * work_hits + 3 * done_hits
        candidates.append((score, name, chosen))

    lines = [
        label or f"周期：{start.date().isoformat()} ~ {end.date().isoformat()}",
        f"检索消息总数：{len(messages)}",
    ]
    evidence_count = 0
    for _score, chat_name, rows in sorted(
        candidates,
        key=lambda item: item[0],
        reverse=True,
    )[:16]:
        chunk: list[str] = []
        limit = 10 if "飞书 CLI" in chat_name else 12
        ranked = sorted(rows, key=_evidence_score, reverse=True)[:limit]
        for message in sorted(ranked, key=_when):
            body = _message_text(message)
            if not body:
                continue
            sender_id, sender_name = _sender(message)
            role = "我" if sender_id == USER_OPEN_ID else (sender_name or "对方")
            message_id = str(message.get("message_id") or "")
            evidence_count += 1
            chunk.append(
                f"[E{evidence_count:03d} id={message_id}]"
                f"[{_when(message):%m-%d %H:%M}][{role}] {body[:220]}"
            )
            if len(chunk) >= limit or evidence_count >= evidence_cap:
                break
        if not chunk:
            continue
        lines.append(f"\n## {chat_name}")
        lines.extend(chunk)
        if evidence_count >= evidence_cap:
            break
    return EvidenceBundle(
        "\n".join(lines).strip(),
        len(messages),
        evidence_count,
    )


def _fallback_summary(context: str) -> str:
    current_chat = "会话"
    records: list[tuple[str, str, str]] = []
    for raw in (context or "").splitlines():
        if raw.startswith("## "):
            current_chat = raw[3:].strip() or "会话"
            continue
        match = re.match(r"\[E\d+ id=[^\]]*\]\[[^\]]+\]\[([^\]]+)\]\s*(.+)", raw)
        if not match:
            continue
        role, body = match.groups()
        if any(hint in body for hint in _NON_WORK_HINTS):
            continue
        body = re.sub(r"\[[^\]]+\]\(https?://[^)]+\)", "（附链接）", body)
        body = re.sub(r"https?://\S+", "（附链接）", body)
        body = re.sub(r"!?\[Image[^\]]*\](?:\([^)]+\))?", "（图片）", body)
        body = re.sub(r"\s+", " ", body).strip()
        body = body[:150] + ("…" if len(body) > 150 else "")
        if body:
            records.append((current_chat, role, body))

    blob = "\n".join(body for _chat, _role, body in records)
    by_chat: dict[str, str] = {}
    for chat, _role, body in records:
        by_chat[chat] = f"{by_chat.get(chat, '')}\n{body}".strip()
    done: list[str] = []
    progress: list[str] = []
    pending: list[str] = []

    def add(bucket: list[str], item: str) -> None:
        if item and item not in bucket:
            bucket.append(item)

    if re.search(r"M8p.{0,120}(?:已经完成|已完成|（跟进）\s*完成)", blob, re.I):
        add(done, "M8p 体验总结已完成，并附有文档结果")
    if "待处理 / 待回复（2项） 已经完成" in blob:
        add(done, "2 项待处理/待回复已经完成")
    if "本周周报已整理完成" in blob:
        add(done, "本周周报已整理完成并发出查看链接")
    if any(
        "A8" in body and "改完" in body and "可以用了" in body
        for _chat, _role, body in records
    ):
        add(done, "A8 接口修改已完成，测试环境已验证可用")
    if any(
        "scene" in chat_blob and "我这边改了" in chat_blob and "可以用了" in chat_blob
        for chat_blob in by_chat.values()
    ):
        add(done, "scene 参数调整已完成，测试环境已验证可用")

    if "技术方案" in blob and "进行中" in blob:
        add(
            progress,
            "AI 中台切换技术方案仍在研究，范围包括录音、转写和会议纪要链路",
        )
        add(pending, "技术方案研究是否已经形成可执行结论")
    if "入职第二个月" in blob or "试用期考核" in blob:
        add(
            progress,
            "已整理试用期“入职第二个月”的工作安排与完成情况素材，并要求在原文档内修改",
        )
        add(pending, "试用期文档是否已经按要求原地写入")
    if "预发" in blob and any(
        marker in blob
        for marker in ("数据不同步", "预发包", "终端软件", "还没上", "没有上", "尚未上线")
    ):
        add(
            progress,
            "协调 APP/终端预发包与测试环境，已发包测试；测试可用但预发尚未上线",
        )
        add(pending, "预发环境是否已上线，上传后的联调验证是否完成")
    if "A8" in blob and "发个包" in blob and "周五" in blob:
        add(progress, "推进 A8 升级，等待周五的新版本包")
        add(pending, "A8 新版本包是否已收到并完成升级")
    if "提测" in blob and "A6" in blob:
        add(progress, "发起 APP 预发 A6 全功能及 M8 pro/粤语问题提测")
        add(pending, "A6 全功能提测是否已受理并有结果")
    if "Vue" in blob and ("目前没有仓库" in blob or "还没有开始" in blob):
        add(progress, "确认新项目采用 Vue，但仓库尚未建立、项目还未启动")
        add(pending, "新项目仓库与启动时间")
    if "飞猫听见" in blob and "申请" in blob:
        add(progress, "确认 A6、A8、飞猫听见等 AI 功能需要申请登记")
        add(pending, "飞猫听见等 AI 功能申请是否已登记完成")
    if "巨量千川" in blob:
        add(progress, "确定尝试巨量千川，后续需要比较实际效果")
        add(pending, "巨量千川方案的效果对比结果")
    if "ICCID" in blob and "Web页" in blob:
        add(progress, "跟进 ICCID 从 APP 带入二次认证 Web 页的问题")
        add(pending, "ICCID 传递问题的处理结论")

    covered = (
        "M8p",
        "周报",
        "待处理 / 待回复",
        "技术方案",
        "入职第二个月",
        "试用期考核",
        "预发",
        "scene",
        "A6",
        "A8",
        "M8 pro",
        "Vue",
        "仓库",
        "飞猫听见",
        "申请",
        "巨量千川",
        "ICCID",
    )
    # Do NOT dump "群名：聊天原文" into done/progress — that is the Cursor gap.
    # Only curated rules above produce weekly/recap bullets.
    _ = (covered, records)

    done = done[:6]
    progress = progress[:8]
    pending = pending[:6]

    def section(title: str, rows: list[str], empty: str) -> list[str]:
        return [title, *(f"- {row}" for row in rows)] if rows else [title, f"- {empty}"]

    lines = section("【今天确认做过】", done, "暂无明确完成证据")
    lines.extend(section("【推进中】", progress, "暂无可归纳的工作消息"))
    lines.extend(
        section(
            "【待你确认】",
            pending,
            "消息能证明沟通与推进，但最终上线、验收状态仍需你确认",
        )
    )
    return "\n".join(lines)


def curated_work_buckets(context: str) -> tuple[list[str], list[str], list[str]]:
    """Structured bullets for weekly fill / fallback — never raw chat quotes."""
    text = _fallback_summary(context or "")
    done: list[str] = []
    progress: list[str] = []
    pending: list[str] = []
    bucket = done
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("【今天确认做过】") or s.startswith("【本周完成】"):
            bucket = done
            continue
        if s.startswith("【推进中】") or s.startswith("【进行中"):
            bucket = progress
            continue
        if s.startswith("【待你确认】") or s.startswith("【问题"):
            bucket = pending
            continue
        if s.startswith("- "):
            item = s[2:].strip()
            if item and "暂无" not in item and item not in bucket:
                bucket.append(item)
    return done[:6], progress[:8], pending[:6]


def today_recap(
    instruction: str,
    *,
    previous_context: str = "",
    refresh: bool = True,
    now: datetime | None = None,
) -> DayRecap:
    if refresh:
        bundle = collect_today_evidence(now)
        if bundle.error:
            return DayRecap(bundle.error, "", 0, 0)
        context = bundle.context
        message_count = bundle.message_count
        evidence_count = bundle.evidence_count
    else:
        context = previous_context
        message_count = 0
        evidence_count = len(re.findall(r"(?m)^\[E\d+", context))
    if not context:
        return DayRecap("上一轮消息证据没有留住，请再说一次「读今天消息」。", "", 0, 0)
    baseline = _fallback_summary(context)
    text = (
        _pending_focus(baseline, instruction)
        if not refresh and "确认" in instruction
        else baseline
    )
    return DayRecap(text, context, message_count, evidence_count)


def _pending_focus(baseline: str, instruction: str) -> str:
    marker = "【待你确认】"
    if marker not in baseline:
        return baseline
    pending = baseline.split(marker, 1)[1].strip()
    if not pending:
        return baseline
    query = re.sub(r"(?:继续|再)?确认|这项|一下", "", instruction or "")
    query = re.sub(r"[\s：:，,。？?]+", "", query)
    rows = [line for line in pending.splitlines() if line.strip()]
    if len(query) >= 2:
        matched = [line for line in rows if query in re.sub(r"\s+", "", line)]
        if matched:
            pending = "\n".join(matched)
    return (
        f"【继续确认】\n{pending}\n\n"
        "说「继续确认 + 事项关键词」，我会沿用今天的消息证据继续收窄。"
    )


def looks_like_recap_followup(raw: str) -> bool:
    text = re.sub(r"\s+", "", raw or "")
    return any(
        phrase in text
        for phrase in (
            "继续确认",
            "再确认",
            "再详细",
            "详细点",
            "还有呢",
            "还有吗",
            "接着看",
            "继续看",
            "再分析",
            "哪些完成",
        )
    )
