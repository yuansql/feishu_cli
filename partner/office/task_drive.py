"""任务推进链路：纪要 → 负责人 → 建任务 → 私信提醒。

对标豆包工作核心理念「交付进展而非产物」：拿到会议纪要后，
自动提取行动项和负责人，建飞书任务，并私信提醒负责人。
安全默认：只列行动项（dry-run）；--execute 显式开启才建任务 + 私信。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

from ..compose.formatters import format_lark_error
from ..core.lark import run_lark
from .org import resolve

CN_TZ = timezone(timedelta(hours=8))
MAX_ACTIONS = 12


def extract_actions_llm(minutes: str) -> list[dict[str, str]]:
    """Hermes 从纪要提取行动项 JSON：[{title, owner, due}]。失败返回 []。"""
    if os.environ.get("FEISHU_PARTNER_NO_LLM") == "1":
        return []
    if not (minutes or "").strip():
        return []
    from ..compose.llm import _invoke_hermes

    prompt = (
        "从会议纪要里提取行动项，只输出一行 JSON 数组，不要解释。\n"
        '格式：[{"title":"要做的事","owner":"负责人姓名","due":"截止时间原文"}]\n'
        "规则：没有负责人的行动项 owner 填空串；没有截止 due 填空串；"
        "最多 8 条；只提取纪要里明确说的，不要编。\n\n"
        f"【纪要】\n{minutes.strip()[:6000]}"
    )
    raw = _invoke_hermes(prompt, timeout=60)
    text = (raw or "").strip()
    if not text or "Error:" in text[:80]:
        return []
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        loaded = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    items: list[dict[str, str]] = []
    for item in loaded[:MAX_ACTIONS]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        items.append(
            {
                "title": title[:120],
                "owner": str(item.get("owner") or "").strip(),
                "due": str(item.get("due") or "").strip(),
            }
        )
    return items


_OWNER_DUE_RE = re.compile(
    r"^(?:[-*•]\s*)?(?:\d+[.、)]\s*)?(?:@?(?P<owner>[一-鿿A-Za-z]{2,8})"
    r"(?:[:：]|\s*负责|\s*牵头))?\s*(?P<title>[^（(]+?)"
    r"(?:[（(]\s*(?:截止|due|DDL|ddl)?\s*[:：]?\s*(?P<due>[^）)]+)\s*[）)])?$"
)
_OWNER_INLINE_RE = re.compile(r"@([一-鿿A-Za-z]{2,8})")
_DUE_INLINE_RE = re.compile(r"(?:截止|due|DDL|ddl)[：:\s]*([0-9月日/.-]+|周[一二三四五六日]|周[一二三四五六日]前|明天|下周\S{0,6})")


def extract_actions_regex(minutes: str) -> list[dict[str, str]]:
    """无 LLM 时的确定性提取：抓「- 张三：做某事（截止：周五）」式行。"""
    items: list[dict[str, str]] = []
    for raw in (minutes or "").splitlines():
        line = raw.strip()
        if not line or len(line) < 4:
            continue
        if not (line.startswith(("-", "*", "•")) or re.match(r"^\d+[.、)]", line)):
            continue
        body = re.sub(r"^[-*•]\s*|^\d+[.、)]\s*", "", line).strip()
        owner = ""
        due = ""
        m_owner = _OWNER_INLINE_RE.search(body)
        if m_owner:
            owner = m_owner.group(1)
            body = body.replace(m_owner.group(0), "").strip()
        m_due = _DUE_INLINE_RE.search(body)
        if m_due:
            due = m_due.group(1).strip()
            body = (body[: m_due.start()] + body[m_due.end() :]).strip()
        body = re.sub(r"[（(]\s*[）)]", "", body).strip()
        body = body.rstrip("，,；;")
        m_struct = _OWNER_DUE_RE.match(body)
        title = body
        if m_struct:
            if m_struct.group("owner") and not owner:
                owner = m_struct.group("owner")
            if m_struct.group("due") and not due:
                due = m_struct.group("due").strip()
            title = (m_struct.group("title") or body).strip()
        title = re.sub(r"[（(]\s*(?:负责人|截止|due|DDL|ddl)?\s*[）)]$", "", title).strip()
        title = title.rstrip("。；;，,")
        if len(title) < 3:
            continue
        items.append({"title": title[:120], "owner": owner, "due": due})
        if len(items) >= MAX_ACTIONS:
            break
    return items


def extract_actions(minutes: str) -> list[dict[str, str]]:
    """LLM 优先，正则兜底。"""
    return extract_actions_llm(minutes) or extract_actions_regex(minutes)


def parse_due(text: str, *, today: datetime | None = None) -> str:
    """把「周五前」「明天」「9月10日」「2026-09-10」转成 YYYY-MM-DD。转不了返回原文。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    today = today or datetime.now(CN_TZ)
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", raw):
        return raw
    if raw in ("今天", "今日"):
        return today.strftime("%Y-%m-%d")
    if raw in ("明天", "明日"):
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    m = re.fullmatch(r"(本|下)?周([一二三四五六日天])(?:前|之前)?", raw)
    if m:
        target = weekday_map[m.group(2)]
        if m.group(1) == "下":
            delta = 7 - today.weekday() + target
        else:
            delta = (target - today.weekday()) % 7
            if delta == 0:
                delta = 7
        return (today + timedelta(days=delta)).strftime("%Y-%m-%d")
    m = re.fullmatch(r"(\d{1,2})月(\d{1,2})[日号]?(?:前|之前)?", raw)
    if m:
        year = today.year
        month, day = int(m.group(1)), int(m.group(2))
        if (month, day) < (today.month, today.day):
            year += 1
        return f"{year:04d}-{month:02d}-{day:02d}"
    return raw


def create_task(item: dict[str, str], owner_open_id: str = "") -> tuple[bool, str]:
    """task +create 建任务。返回 (成功与否, 说明)。"""
    argv = ["task", "+create", "--summary", item["title"]]
    due = parse_due(item.get("due", ""))
    if due and re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
        argv.extend(["--due", due])
    if owner_open_id:
        argv.extend(["--assignee", owner_open_id])
    payload = run_lark(argv, as_identity="user")
    if payload.get("ok") is False or payload.get("error"):
        return False, format_lark_error(payload)
    return True, ""


def notify_owner(open_id: str, item: dict[str, str], *, source: str = "") -> str:
    """私信提醒负责人：你被派了个任务。"""
    from .messaging import send_text

    due = item.get("due") or ""
    lines = [f"新任务：{item['title']}"]
    if due:
        lines.append(f"截止：{due}")
    if source:
        lines.append(f"来源：{source}")
    return send_text(open_id, "\n".join(lines), as_identity="bot")


def format_plan(items: list[dict[str, str]]) -> str:
    """dry-run 预览文本。"""
    if not items:
        return "纪要里没提取到行动项。"
    lines = [f"提取到 {len(items)} 条行动项（预览，--execute 才建任务+私信）："]
    for index, item in enumerate(items, 1):
        line = f"{index}. {item['title']}"
        if item.get("owner"):
            line += f"｜负责人：{item['owner']}"
        if item.get("due"):
            line += f"｜截止：{item['due']}"
        lines.append(line)
    return "\n".join(lines)


def drive_from_minutes(
    minutes: str,
    *,
    execute: bool = False,
    source: str = "",
    notify: bool = True,
) -> str:
    """完整链路：纪要 → 行动项 →（execute 时）建任务 + 私信提醒。"""
    if not (minutes or "").strip():
        return "没有纪要内容。用法：feishu drive-tasks <纪要文本|@文件> [--execute]"
    items = extract_actions(minutes)
    if not execute:
        return format_plan(items)
    if not items:
        return "纪要里没提取到行动项。"
    results = [f"推进 {len(items)} 条行动项："]
    for index, item in enumerate(items, 1):
        owner_name = item.get("owner") or ""
        owner_oid = resolve(owner_name) if owner_name else ""
        ok, err = create_task(item, owner_oid)
        if not ok:
            results.append(f"{index}. {item['title']} → 建任务失败：{err}")
            continue
        note = f"{index}. {item['title']}"
        if owner_oid:
            note += f" → 已建任务（{owner_name}）"
            if notify:
                sent = notify_owner(owner_oid, item, source=source)
                note += "，已私信" if sent == "已发送。" else f"，私信没发出：{sent}"
        else:
            note += " → 已建任务（无负责人或未匹配到人）"
        results.append(note)
    return "\n".join(results)


def drive_tasks_cli(argv: list[str]) -> int:
    """CLI 入口：feishu drive-tasks <纪要文本|@文件> [--execute] [--no-notify]"""
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(
        prog="feishu drive-tasks", description="任务推进：纪要→负责人→建任务→私信提醒"
    )
    parser.add_argument("minutes", nargs="+", help="纪要文本，或 @文件路径")
    parser.add_argument("--execute", action="store_true", help="真正建任务+私信（默认只预览）")
    parser.add_argument("--no-notify", action="store_true", help="建任务但不私信提醒")
    parser.add_argument("--source", default="", help="来源说明（如会议名）")
    args = parser.parse_args(argv)
    text = " ".join(args.minutes)
    if text.startswith("@"):
        path = Path(text[1:]).expanduser()
        if not path.exists():
            print(f"纪要文件不存在：{path}")
            return 1
        text = path.read_text(encoding="utf-8")
    print(
        drive_from_minutes(
            text,
            execute=args.execute,
            source=args.source,
            notify=not args.no_notify,
        )
    )
    return 0
