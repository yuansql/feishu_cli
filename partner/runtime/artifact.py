"""Persistent document-edit task: observe → draft → confirm → patch → verify."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import html
import json
import os
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from ..core.lark import run_lark
from ..compose.llm import draft_doc_edit

CN_TZ = timezone(timedelta(hours=8))
_DEFAULT = Path.home() / ".feishu-partner" / "artifacts.json"
_URL_RE = re.compile(r"https://[^\s]*feishu\.cn/[^\s]+")
_MONTH_RE = re.compile(r"(?:入职)?第[一二三四五六七八九十\d]+个月")
_SUBSECTIONS = (
    "工作完成情况",
    "工作安排",
    "试用期考核",
    "员工自评",
    "部门评价",
    "相关部门评价",
    "已完成",
    "进行中",
    "下周计划",
)
_CONFIRM = frozenset(
    {
        "写进去",
        "写入",
        "写啊",
        "确认写入",
        "确认执行",
        "就按这个写",
        "改进去",
        "填进去",
    }
)
_CLOSE = frozenset({"结束", "任务结束", "取消", "不用了", "先不写了"})
_REVISE = ("多一点", "太少", "少了", "补充", "扩写", "再写", "调整", "改成", "这块")


@dataclass(frozen=True)
class TargetBlock:
    block_id: str
    label: str


def artifact_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_ARTIFACTS")
    return Path(override).expanduser() if override else _DEFAULT


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _load_store() -> dict[str, Any]:
    path = artifact_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_artifact(chat_id: str) -> dict[str, Any] | None:
    row = _load_store().get((chat_id or "").strip())
    return row if isinstance(row, dict) else None


def save_artifact(chat_id: str, task: dict[str, Any]) -> None:
    cid = (chat_id or "").strip()
    if not cid:
        return
    store = _load_store()
    row = dict(task)
    row.setdefault("created_at", _now_iso())
    row["updated_at"] = _now_iso()
    store[cid] = row
    path = artifact_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def observe_document(
    chat_id: str,
    doc_url: str,
    source: str,
    *,
    instruction: str = "",
) -> None:
    section = _section_hint(instruction)
    marker = _marker_hint(instruction, section)
    save_artifact(
        chat_id,
        {
            "kind": "doc_edit",
            "status": "observed",
            "doc_url": (doc_url or "").strip(),
            "instruction": (instruction or "").strip(),
            "section": section,
            "marker": marker,
            "anchor_block_id": "",
            "anchor_label": "",
            "draft": "",
            "source_snapshot": (source or "")[:24000],
            "inserted_block_id": "",
            "last_error": "",
        },
    )


def close_artifact(chat_id: str) -> str:
    task = load_artifact(chat_id)
    if task:
        task["status"] = "closed"
        task["last_error"] = ""
        save_artifact(chat_id, task)
    return "任务结束。"


def artifact_turn(task: dict[str, Any] | None, raw: str) -> str:
    """Return confirm/revise/close for a short turn bound to an active artifact."""
    if not task or str(task.get("status") or "") == "closed":
        return ""
    status = str(task.get("status") or "")
    text = re.sub(r"\s+", "", raw or "")
    if text in _CLOSE:
        return "close"
    # 已有草稿 → 写啊 = 确认写入；只观察过 → 写啊 = 先起草。
    if text in _CONFIRM and status in {"ready", "verify_failed"}:
        return "confirm"
    if text in _CONFIRM and status in {"observed", "needs_target"}:
        return "revise"
    if any(key in (raw or "") for key in _REVISE):
        if status in {"ready", "done", "verify_failed", "observed", "needs_target"}:
            return "revise"
        if any(key in (raw or "") for key in ("写", "填", "改", "补")):
            return "revise"
    if _MONTH_RE.search(raw or "") and any(
        key in (raw or "") for key in ("写", "填", "改", "加", "添")
    ):
        return "revise"
    if any(key in (raw or "") for key in ("不是新写", "改这个文档", "继续改")):
        return "revise"
    return ""


def _element_label(element: ElementTree.Element) -> str:
    bits = [text.strip() for text in element.itertext() if text.strip()]
    for node in element.iter():
        for key in ("user-name", "title"):
            value = str(node.attrib.get(key) or "").strip()
            if value:
                bits.append(value)
    return " ".join(dict.fromkeys(bits)).strip()


def _heading_level(element: ElementTree.Element) -> int | None:
    match = re.fullmatch(r"h([1-9])", element.tag)
    return int(match.group(1)) if match else None


def _flatten(elements: list[ElementTree.Element]) -> list[ElementTree.Element]:
    out: list[ElementTree.Element] = []
    for element in elements:
        if element.attrib.get("id"):
            out.append(element)
        out.extend(_flatten(list(element)))
    return out


def locate_target_block(
    xml: str,
    *,
    marker: str = "",
    section: str = "",
) -> TargetBlock:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ValueError("文档结构无法解析") from exc
    top = list(root)
    scope = top
    section_text = (section or "").strip()
    if section_text:
        start = -1
        level = 10
        for index, element in enumerate(top):
            current = _heading_level(element)
            if current is not None and section_text in _element_label(element):
                start = index
                level = current
                break
        if start >= 0:
            end = len(top)
            for index in range(start + 1, len(top)):
                current = _heading_level(top[index])
                if current is not None and current <= level:
                    end = index
                    break
            scope = top[start:end]
    blocks = _flatten(scope)
    needle = (marker or "").strip()
    if needle:
        hits = [
            element
            for element in blocks
            if needle in _element_label(element) and element.attrib.get("id")
        ]
        if len(hits) == 1:
            return TargetBlock(str(hits[0].attrib["id"]), _element_label(hits[0]))
        if len(hits) > 1:
            exact = [
                element
                for element in hits
                if needle in {
                    str(node.attrib.get("user-name") or "")
                    for node in element.iter()
                }
            ]
            if len(exact) == 1:
                return TargetBlock(str(exact[0].attrib["id"]), _element_label(exact[0]))
            raise ValueError(f"「{needle}」在目标范围内出现多次")
        raise ValueError(f"没在文档里定位到「{needle}」")
    if section_text:
        for element in blocks:
            if section_text in _element_label(element) and element.attrib.get("id"):
                return TargetBlock(str(element.attrib["id"]), _element_label(element))
        raise ValueError(f"没在文档里定位到「{section_text}」")
    raise ValueError("还不知道要写到文档哪一节")


def _section_hint(instruction: str) -> str:
    month = _MONTH_RE.search(instruction or "")
    if month:
        return month.group(0)
    return next((item for item in _SUBSECTIONS if item in (instruction or "")), "")


def _marker_hint(instruction: str, section: str) -> str:
    text = instruction or ""
    match = re.search(
        r"(?:写到|填到|加到|放到|添加到|补充到|更新到)(.{1,32}?)(?:下面|下边|后面|部分|那里|中)",
        text,
    )
    if not match:
        return next(
            (
                item
                for item in _SUBSECTIONS
                if item != section and item in text
            ),
            "",
        )
    marker = match.group(1).strip(" ：:，,")
    if section:
        marker = marker.replace(section, "").strip(" ：:，,")
    return marker


def _payload_content(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return ""
    document = data.get("document") if isinstance(data.get("document"), dict) else data
    if not isinstance(document, dict):
        return ""
    return str(document.get("content") or document.get("markdown") or "")


def _payload_revision(payload: dict[str, Any]) -> int:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    document = data.get("document") if isinstance(data, dict) else {}
    value = document.get("revision_id") if isinstance(document, dict) else None
    if not isinstance(value, (int, str)):
        return -1
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _error_text(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or "飞书文档操作失败")
    return str(payload.get("stderr") or payload.get("raw") or "飞书文档操作失败")


def _fetch_target(doc_url: str, section: str, marker: str) -> dict[str, Any]:
    keywords = "|".join(item for item in (section, marker) if item)
    args = ["docs", "+fetch", "--doc", doc_url]
    if keywords:
        args.extend(
            [
                "--scope",
                "keyword",
                "--keyword",
                keywords,
                "--context-before",
                "4",
                "--context-after",
                "20",
            ]
        )
    args.extend(["--detail", "with-ids", "--doc-format", "xml"])
    return run_lark(args, as_identity="user", timeout=90)


def _plain_source(xml: str) -> str:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return re.sub(r"<[^>]+>", " ", xml)
    lines = [_element_label(element) for element in _flatten(list(root))]
    return "\n".join(dict.fromkeys(line for line in lines if line))


_EXPAND_CUES = frozenset(
    {
        "多一点",
        "太少",
        "少了",
        "扩写",
        "再写",
        "补充",
        "补充一下",
        "再多点",
        "详细点",
    }
)


def _is_expand_cue(text: str) -> bool:
    folded = re.sub(r"\s+", "", text or "")
    return folded in {re.sub(r"\s+", "", cue) for cue in _EXPAND_CUES} or any(
        cue in (text or "") for cue in _EXPAND_CUES
    ) and len(folded) <= 12


def work_facts_from_week_chats() -> str:
    """Build draft materials from this week's Feishu IM — not calendar weekly_text junk."""
    from ..office.calendar_views import _week_bounds
    from ..office.recap import collect_week_evidence, curated_work_buckets
    from ..compose.formatters import format_lark_error, format_tasks

    start, end = _week_bounds()
    bundle = collect_week_evidence(start, end)
    if bundle.error:
        return f"【本周飞书对话】取数失败：{bundle.error}"
    done, progress, pending = curated_work_buckets(bundle.context or "")
    tasks = run_lark(
        ["task", "+get-my-tasks", "--complete=false", "--page-limit", "20"],
        as_identity="user",
    )
    task_blob = format_tasks(tasks) if tasks.get("ok") is not False else format_lark_error(tasks)
    lines = [
        f"周期：{start.date().isoformat()} ~ {end.date().isoformat()}",
        f"本周检索消息 {bundle.message_count} 条，工作相关证据 {bundle.evidence_count} 条。",
        "",
        "【本周完成·从对话归纳】",
    ]
    if done:
        lines.extend(f"- {item}" for item in done)
    else:
        lines.append("- （对话里暂未归纳出明确完成项）")
    lines.append("")
    lines.append("【推进中】")
    if progress:
        lines.extend(f"- {item}" for item in progress)
    else:
        lines.append("- （暂无）")
    if pending:
        lines.append("")
        lines.append("【待确认】")
        lines.extend(f"- {item}" for item in pending[:5])
    lines.append("")
    lines.append("【未完成待办】")
    lines.append(task_blob[:2000] if task_blob else "- （无）")
    lines.append("")
    lines.append("【对话证据摘录】")
    lines.append((bundle.context or "（空）")[:5500])
    return "\n".join(lines)


def _draft_limit(instruction: str) -> int:
    match = re.search(r"(\d+)\s*(?:句|条|点)", instruction or "")
    if match:
        return max(1, min(12, int(match.group(1))))
    if _is_expand_cue(instruction) or "扩写" in (instruction or "") or "多列" in (instruction or ""):
        return 10
    return 8


def _fallback_draft(
    instruction: str,
    source: str,
    work_facts: str,
    previous: str = "",
) -> str:
    limit = _draft_limit(instruction)
    junk = (
        "飞书权威失败",
        "缺权限",
        "任务规划",
        "工作内容",
        "工作安排",
        "这周我这边的情况",
        "【草稿】",
        "准备修改",
        "周期：",
        "本周检索消息",
        "检索消息总数",
        "对话证据摘录",
        "未完成待办",
    )
    candidates: list[str] = []
    # Prefer curated week bullets over previous junk draft / meta headers.
    factual_pool = work_facts or ""
    prefer = ""
    if "【本周完成" in factual_pool:
        # Only take curated buckets, not the long 对话证据摘录 tail.
        prefer = factual_pool.split("【对话证据摘录】", 1)[0]
    pool = prefer or f"{work_facts}\n{previous}\n{source}".strip()
    for raw in pool.splitlines():
        line = re.sub(r"^[\s>*#\-]+", "", raw).strip()
        line = re.sub(r"^\d+[\.、)\]]\s*", "", line).strip()
        if line.startswith("【") and line.endswith("】"):
            continue
        if len(line) < 6 or line in candidates:
            continue
        if any(token in line for token in junk):
            continue
        if line.startswith("周期：") or "检索消息" in line:
            continue
        # Drop broken date fragments like "/17–8/23" or bare "/17".
        if re.fullmatch(r"[/–\-~\d\s]+", line):
            continue
        if re.match(r"^/\d+", line) and len(line) < 20:
            continue
        candidates.append(line[:180])
        if len(candidates) >= limit:
            break
    if not candidates and previous.strip():
        return previous.strip()
    return "\n".join(f"{index}. {line}" for index, line in enumerate(candidates, 1))


def _looks_like_bad_draft(text: str) -> bool:
    blob = (text or "").strip()
    if not blob:
        return True
    lines = [ln.strip() for ln in blob.splitlines() if ln.strip()]
    if len(lines) < 2:
        return True
    bad_hits = 0
    for line in lines:
        body = re.sub(r"^[\s\d.、\-]+", "", line)
        if body in {"工作内容", "工作安排", "【工作内容】"} or body.startswith("【"):
            bad_hits += 1
        if re.match(r"^/\d+", body) and len(body) < 24:
            bad_hits += 1
        if "这周我这边的情况" in body:
            bad_hits += 1
        if body.startswith("周期：") or "检索消息" in body:
            bad_hits += 1
    return bad_hits >= max(2, len(lines) // 2)


def prepare_document_edit(
    chat_id: str,
    instruction: str,
    *,
    work_facts: str = "",
    doc_url: str = "",
) -> str:
    active = load_artifact(chat_id) or {}
    # 「写啊」 alone: reuse last instruction / section from the observed doc.
    instr = (instruction or "").strip()
    expand = _is_expand_cue(instr)
    if re.sub(r"\s+", "", instr) in _CONFIRM:
        instr = str(active.get("instruction") or "").strip() or instr
    if expand:
        base = str(active.get("instruction") or "").strip() or (
            "把本周实际工作写进目标章节"
        )
        draft_instruction = (
            f"{base}\n\n【本轮】用户要求扩写：根据【工作事实】里本周飞书对话，"
            "列举可核验的工作条目（目标 6-10 条）；保留上一版仍正确的条目并补充新的；"
            "禁止残缺日期（如 /17）、禁止把【工作内容】这类标题当条目、禁止空话。"
        )
        store_instruction = base
    else:
        draft_instruction = instr if instr not in _CONFIRM else (
            str(active.get("instruction") or "") or instruction
        )
        store_instruction = draft_instruction
    explicit = _URL_RE.search(f"{draft_instruction} {doc_url} {instruction}")
    target_url = (
        (explicit.group(0).rstrip(")。,，") if explicit else "")
        or (doc_url or "").strip()
        or str(active.get("doc_url") or "")
    )
    if not target_url:
        return "还没有目标文档。先发文档链接并说要改哪一节。"
    explicit_section = _section_hint(draft_instruction) or _section_hint(
        str(active.get("instruction") or "")
    )
    section = explicit_section or str(active.get("section") or "")
    explicit_marker = _marker_hint(draft_instruction, section)
    marker = (
        explicit_marker
        if explicit_marker or explicit_section
        else str(active.get("marker") or "")
    )
    facts = (work_facts or "").strip() or work_facts_from_week_chats()
    payload = _fetch_target(target_url, section, marker)
    if payload.get("ok") is False:
        return _error_text(payload)
    source_xml = _payload_content(payload)
    if not source_xml:
        return "文档读到了，但没有可定位的正文；没有执行写入。"
    try:
        if (
            not section
            and not marker
            and active.get("anchor_block_id")
            and target_url == active.get("doc_url")
        ):
            target = TargetBlock(
                str(active["anchor_block_id"]),
                str(active.get("anchor_label") or "上次位置"),
            )
        else:
            target = locate_target_block(source_xml, marker=marker, section=section)
    except ValueError as exc:
        task = dict(active)
        task.update(
            {
                "kind": "doc_edit",
                "status": "needs_target",
                "doc_url": target_url,
                "instruction": store_instruction,
                "section": section,
                "marker": marker,
                "source_snapshot": _plain_source(source_xml)[:24000],
                "last_error": str(exc),
            }
        )
        save_artifact(chat_id, task)
        return f"已读到目标文档，但{exc}。请说清楚章节或名字，我会接着这份文档处理。"
    plain = _plain_source(source_xml)
    same_target = (
        target_url == active.get("doc_url")
        and target.block_id == active.get("anchor_block_id")
    )
    previous = str(active.get("draft") or "") if same_target else ""
    draft = draft_doc_edit(
        draft_instruction,
        plain,
        facts,
        previous_draft=previous,
    )
    if draft and _looks_like_bad_draft(draft):
        draft = ""
    if not draft:
        draft = _fallback_draft(draft_instruction, plain, facts, previous)
    if not draft:
        return (
            "目标位置已找到，但本周飞书对话里还不够写成条目。"
            "可以说「多一点」，或先「读一下消息」确认本周证据。"
        )
    task = {
        "kind": "doc_edit",
        "status": "ready",
        "doc_url": target_url,
        "instruction": store_instruction,
        "section": section,
        "marker": marker,
        "anchor_block_id": target.block_id,
        "anchor_label": target.label,
        "draft": draft.strip(),
        "source_snapshot": plain[:24000],
        "inserted_block_id": (
            str(active.get("inserted_block_id") or "")
            if same_target
            else ""
        ),
        "revision_id": _payload_revision(payload),
        "last_error": "",
    }
    save_artifact(chat_id, task)
    return (
        f"已读到目标文档，准备修改「{target.label[:80]}」下方。\n\n"
        f"【草稿】\n{task['draft']}\n\n"
        "回复「写进去」执行；说「多一点」继续扩写；说「结束」取消。"
    )


def _xml_paragraph(text: str) -> str:
    escaped = html.escape(text or "", quote=False).replace("\n", "<br/>")
    return f"<p>{escaped}</p>"


def _first_preview(text: str) -> str:
    for raw in (text or "").splitlines():
        line = re.sub(r"^[\s>*#\-\d.、]+", "", raw).strip()
        if line:
            return line[:28]
    return ""


def _new_block_id(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    document = data.get("document") if isinstance(data, dict) else {}
    blocks = document.get("new_blocks") if isinstance(document, dict) else []
    if isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, dict) and block.get("block_id"):
                return str(block["block_id"])
    return ""


def _update_succeeded(payload: dict[str, Any]) -> bool:
    if payload.get("ok") is False:
        return False
    raw_data = payload.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    result = str(data.get("result") or "success")
    return result == "success"


def apply_document_edit(chat_id: str) -> str:
    task = load_artifact(chat_id)
    if not task:
        return "当前没有待写入的文档草稿。"
    if str(task.get("status") or "") not in {"ready", "verify_failed"}:
        if str(task.get("status") or "") == "done":
            return "这次修改已经完成。说「多一点」可继续补，或说「结束」。"
        return "当前草稿还没准备好，先说要改哪一节。"
    doc_url = str(task.get("doc_url") or "")
    draft = str(task.get("draft") or "").strip()
    payload = _fetch_target(
        doc_url,
        str(task.get("section") or ""),
        str(task.get("marker") or ""),
    )
    if payload.get("ok") is False:
        task["last_error"] = _error_text(payload)
        save_artifact(chat_id, task)
        return f"重新读取目标位置失败：{task['last_error']}。没有执行写入。"
    source_xml = _payload_content(payload)
    inserted = str(task.get("inserted_block_id") or "")
    if inserted:
        command = "block_replace"
        block_id = inserted
    else:
        try:
            target = locate_target_block(
                source_xml,
                marker=str(task.get("marker") or ""),
                section=str(task.get("section") or ""),
            )
        except ValueError as exc:
            task["last_error"] = str(exc)
            save_artifact(chat_id, task)
            return f"目标位置已变化：{exc}。没有执行写入。"
        command = "block_insert_after"
        block_id = target.block_id
        task["anchor_block_id"] = target.block_id
        task["anchor_label"] = target.label
    update = run_lark(
        [
            "docs",
            "+update",
            "--doc",
            doc_url,
            "--command",
            command,
            "--block-id",
            block_id,
            "--doc-format",
            "xml",
            "--content",
            _xml_paragraph(draft),
            "--revision-id",
            str(_payload_revision(payload)),
        ],
        as_identity="user",
        timeout=90,
    )
    if not _update_succeeded(update):
        task["status"] = "ready"
        task["last_error"] = _error_text(update)
        save_artifact(chat_id, task)
        return f"飞书写入失败：{task['last_error']}。草稿已保留，可重试。"
    new_id = _new_block_id(update) or inserted
    task["inserted_block_id"] = new_id
    preview = _first_preview(draft)
    verify = run_lark(
        [
            "docs",
            "+fetch",
            "--doc",
            doc_url,
            "--scope",
            "keyword",
            "--keyword",
            preview,
            "--context-before",
            "1",
            "--context-after",
            "2",
            "--detail",
            "with-ids",
            "--doc-format",
            "xml",
        ],
        as_identity="user",
        timeout=90,
    )
    verified_content = html.unescape(_payload_content(verify))
    if verify.get("ok") is False or (preview and preview not in verified_content):
        task["status"] = "verify_failed"
        task["last_error"] = "写入返回成功，但回读未匹配草稿"
        save_artifact(chat_id, task)
        return (
            "飞书返回写入成功，但回读未验真；我先不宣称完成。"
            "草稿和 block 已保留，可说「写进去」重试验证。"
        )
    task["status"] = "done"
    task["last_error"] = ""
    task["revision_id"] = _payload_revision(verify)
    save_artifact(chat_id, task)
    return (
        f"已写入并回读确认：{task.get('anchor_label') or '目标位置'}。\n"
        f"{doc_url}\n\n任务结束。"
    )
