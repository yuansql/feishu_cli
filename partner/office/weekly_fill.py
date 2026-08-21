"""Fill an existing department weekly wiki/docx for the configured owner.

Closes the gap vs Cursor: given a template URL +「填写周报」, write into the
person's 已完成/进行中/下周计划 slots — do not create a new dump doc.
"""

from __future__ import annotations

import re
from typing import Any

from ..compose.formatters import format_lark_error, format_tasks
from ..core.ids import USER_NAMES, display_name
from ..core.lark import run_lark
from .recap import collect_week_evidence, curated_work_buckets

_DOC_URL_RE = re.compile(
    r"https://[^\s]*feishu\.cn/(?:wiki|docx)/[A-Za-z0-9]+",
    re.I,
)


def extract_weekly_doc_url(raw: str) -> str:
    m = _DOC_URL_RE.search(raw or "")
    return m.group(0).rstrip(")。,，") if m else ""


def _owner_name() -> str:
    name = (display_name() or "").strip()
    if name:
        return name
    if USER_NAMES:
        return str(USER_NAMES[0]).strip()
    return ""


def _fetch_section_xml(doc: str, person: str) -> tuple[str, str]:
    """Return (xml, error). Prefer section around the person's heading."""
    payload = run_lark(
        [
            "docs",
            "+fetch",
            "--api-version",
            "v2",
            "--doc",
            doc,
            "--scope",
            "keyword",
            "--keyword",
            person,
            "--context-before",
            "0",
            "--context-after",
            "20",
            "--detail",
            "with-ids",
            "--doc-format",
            "xml",
        ],
        as_identity="user",
        timeout=90,
    )
    if payload.get("ok") is False:
        return "", format_lark_error(payload)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    doc_blob = data.get("document") if isinstance(data.get("document"), dict) else {}
    content = str(doc_blob.get("content") or "")
    return content, ""


def _person_heading_slice(xml: str, person: str) -> str:
    """Keep the personal h3… section, not the team roster cite."""
    if not xml:
        return ""
    # Prefer standalone heading: <h3 id="..."><cite ... user-name="吴梦晨"></cite></h3>
    pat = re.compile(
        rf'<h3 id="(doxcn[^"]+)"[^>]*>\s*<cite[^>]*user-name="{re.escape(person)}"[^>]*></cite>\s*</h3>',
        re.I,
    )
    matches = list(pat.finditer(xml))
    if not matches:
        return xml
    # Last match is usually the personal section (roster header comes first).
    m = matches[-1]
    start = m.start()
    # End at next peer h3 or 待解决的问题 paragraph / callout shared section.
    rest = xml[start:]
    end_m = re.search(
        r'<h3 id="doxcn[^"]+"|<p id="doxcn[^"]+"[^>]*>\s*<br\s*/?>\s*<b>.*待解决',
        rest[len(m.group(0)) :],
        re.I,
    )
    if end_m:
        return rest[: len(m.group(0)) + end_m.start()]
    return rest[:8000]


def parse_weekly_slots(xml: str) -> dict[str, list[str]]:
    """Map section → empty-ish list item block ids."""
    slots: dict[str, list[str]] = {"done": [], "progress": [], "next": [], "think": []}
    if not xml:
        return slots
    # Split by h4 labels
    parts = re.split(r"(<h4\b[^>]*>.*?</h4>)", xml, flags=re.I | re.S)
    current = ""
    for part in parts:
        label = re.sub(r"<[^>]+>", "", part)
        if "已完成" in label:
            current = "done"
            continue
        if "进行中" in label:
            current = "progress"
            continue
        if "下周计划" in label:
            current = "next"
            continue
        if not current:
            continue
        for m in re.finditer(r'<li id="(doxcn[^"]+)"[^>]*>(.*?)</li>', part, re.I | re.S):
            bid, inner = m.group(1), re.sub(r"\s+", "", m.group(2) or "")
            # Fill empty or whitespace-only slots
            if inner == "" or inner == "<br/>" or not re.sub(r"<[^>]+>", "", inner).strip():
                slots[current].append(bid)
    return slots


def _next_plans(progress: list[str], pending: list[str]) -> list[str]:
    plans: list[str] = []
    for row in pending:
        if row and row not in plans:
            # Soften "是否…" into action
            t = row
            t = re.sub(r"^(.+?)是否已经", r"确认\1是否", t)
            if not t.startswith(("推进", "跟进", "输出", "确认", "补齐", "闭环")):
                t = f"跟进：{t}"
            plans.append(t)
        if len(plans) >= 3:
            break
    if len(plans) < 3:
        for row in progress:
            if row and row not in plans:
                plans.append(f"继续推进：{row}")
            if len(plans) >= 3:
                break
    while len(plans) < 3:
        plans.append("")
    return plans[:3]


def _pad(rows: list[str], n: int = 3) -> list[str]:
    out = list(rows[:n])
    while len(out) < n:
        out.append("")
    return out


def _block_replace(doc: str, block_id: str, text: str) -> dict[str, Any]:
    body = f"<li>{text}</li>" if text else "<li></li>"
    return run_lark(
        [
            "docs",
            "+update",
            "--api-version",
            "v2",
            "--doc",
            doc,
            "--command",
            "block_replace",
            "--block-id",
            block_id,
            "--content",
            "-",
        ],
        as_identity="user",
        timeout=60,
        input_text=body,
    )


def fill_department_weekly(doc_url: str) -> str:
    """Write curated weekly bullets into the owner's slots on the template."""
    person = _owner_name()
    if not person:
        return "还没配置本人姓名（feishu setup --name …），没法对上模板里的人名节。"
    url = extract_weekly_doc_url(doc_url) or (doc_url or "").strip()
    if not url:
        return "请附上部门周报的 wiki/docx 链接，再说「填写周报」。"

    from .calendar_views import _week_bounds

    start, end = _week_bounds()
    bundle = collect_week_evidence(start, end)
    if bundle.error:
        return f"取本周聊天证据失败：\n{bundle.error}"

    open_tasks = run_lark(
        ["task", "+get-my-tasks", "--complete=false", "--page-limit", "30"],
        as_identity="user",
    )
    tasks_blob = format_tasks(open_tasks)
    if open_tasks.get("ok") is False:
        tasks_blob = ""

    done, progress, pending = curated_work_buckets(bundle.context or "")
    if tasks_blob and "没有未完成" not in tasks_blob:
        for line in tasks_blob.splitlines():
            s = line.strip(" -•\t")
            if s and s not in progress and "【" not in s:
                progress.append(s)
                if len(progress) >= 6:
                    break
    next_rows = _next_plans(progress, pending)
    think = pending[:2]

    xml, err = _fetch_section_xml(url, person)
    if err:
        return f"读周报模板失败：\n{err}"
    slice_xml = _person_heading_slice(xml, person)
    slots = parse_weekly_slots(slice_xml)
    if not any(slots.values()):
        return (
            f"在模板里找到了「{person}」，但已完成/进行中/下周计划的空行对不上。"
            "请确认人名节下仍是空的 1. 2. 3. 列表。"
        )

    mapping = [
        ("done", _pad(done, max(3, len(slots["done"])))),
        ("progress", _pad(progress, max(3, len(slots["progress"])))),
        ("next", _pad(next_rows, max(3, len(slots["next"])))),
        ("think", _pad(think, max(0, len(slots["think"])))),
    ]
    wrote = 0
    errors: list[str] = []
    for key, rows in mapping:
        ids = slots.get(key) or []
        for bid, text in zip(ids, rows):
            if not text:
                continue
            payload = _block_replace(url, bid, text)
            if payload.get("ok"):
                wrote += 1
            else:
                errors.append(format_lark_error(payload)[:200])

    if wrote == 0:
        return "尝试写入模板失败：\n" + ("\n".join(errors[:3]) or "未知错误")

    preview = [
        f"已写入《部门周报》里【{person}】本周栏位（{start.date()} ~ {end.date()}，"
        f"依据 {bundle.evidence_count} 条工作证据 / 检索 {bundle.message_count} 条）。",
        url,
        "",
        "【已完成】",
        *[f"- {x}" for x in done[:3] or ["（空）"]],
        "【进行中】",
        *[f"- {x}" for x in progress[:3] or ["（空）"]],
        "【下周计划】",
        *[f"- {x}" for x in next_rows[:3] if x],
    ]
    if errors:
        preview.append("\n部分条目写入失败：")
        preview.extend(f"- {e}" for e in errors[:2])
    return "\n".join(preview)
