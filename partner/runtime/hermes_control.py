"""P2P turns controlled by local Hermes. Shell only seeds + write gate."""

from __future__ import annotations

import re
from typing import Any

from ..compose.llm import (
    hermes_available,
    hermes_partner_turn,
    salvage_spoken_reply,
    _is_usable_reply,
)
from ..core.session import save_turn
from .artifact import (
    _URL_RE,
    _error_text,
    _fetch_target,
    _payload_content,
    _plain_source,
    _section_hint,
    _marker_hint,
    load_artifact,
    observe_document,
    save_artifact,
)

# 壳硬路径：不经 Hermes（卡/销账/写确认已在 dispatch 前置）。
SHELL_FAST = frozenset(
    {
        "send",
        "resolve",
        "task_done",
        "digest",
        "weekly_tasks",
        "today",
        "today_recap",
        "tomorrow",
        "brief",
        "task_continue",
        "task_status",
        "task_confirm",
        "task_cancel",
        "aily",
        "help",
        "identity",
        "write_weekly",
    }
)

_CONTROL_RULES = (
    "【主控规则】\n"
    "你是本机 Hermes，单聊主控。壳只负责收消息与「写进去」确认写入。\n"
    "有【进行中的文档任务】时必须接着改这份文档，禁止问「哪份文档/进度/接口」。\n"
    "要改云文档时：先用人话说明改法，再输出【草稿】（每行一条），"
    "末尾提示用户回复「写进去」才真正写入；你自己不能写云文档。\n"
    "试用期考核表：条目应落在对应月的「二、工作完成情况」列表，"
    "不要插在月标题正下方；发现格式不对就指出并给纠正草稿。\n"
    "可选一行：【章节】第二个月|工作完成情况\n"
)


def _doc_seed(chat_id: str, asked: str) -> tuple[str, dict[str, Any]]:
    task = dict(load_artifact(chat_id) or {})
    explicit = _URL_RE.search(asked or "")
    doc_url = (
        (explicit.group(0).rstrip(")。,，") if explicit else "")
        or str(task.get("doc_url") or "")
    )
    plain = str(task.get("source_snapshot") or "")
    if doc_url:
        payload = _fetch_target(doc_url, str(task.get("section") or ""), str(task.get("marker") or ""))
        if payload.get("ok") is not False:
            plain = _plain_source(_payload_content(payload)) or plain
            observe_document(chat_id, doc_url, plain, instruction=asked)
            task = dict(load_artifact(chat_id) or {})
        elif not plain:
            return f"读文档失败：{_error_text(payload)}", task
    if not doc_url:
        return "", task
    status = str(task.get("status") or "observed")
    return (
        "【进行中的文档任务】\n"
        f"文档：{doc_url}\n"
        f"状态：{status}\n"
        f"上一轮：{task.get('instruction') or asked}\n"
        f"章节：{task.get('section') or '（未定）'} / {task.get('marker') or '（未定）'}\n\n"
        f"【文档正文节选】\n{plain[:9000]}"
    ), task


def _stash_draft_from_reply(chat_id: str, spoken: str, asked: str) -> None:
    blob = spoken or ""
    if "【草稿】" not in blob:
        return
    draft = blob.split("【草稿】", 1)[1]
    for stop in ("回复「写进去」", "回复「写进去」", "说「结束」", "说「多一点」"):
        if stop in draft:
            draft = draft.split(stop, 1)[0]
    draft = draft.strip()
    if len(draft) < 8:
        return
    task = dict(load_artifact(chat_id) or {})
    doc_url = str(task.get("doc_url") or "")
    url_m = _URL_RE.search(asked or "")
    if url_m:
        doc_url = url_m.group(0).rstrip(")。,，")
    if not doc_url:
        return
    section = str(task.get("section") or "")
    marker = str(task.get("marker") or "")
    chap = re.search(
        r"【章节】\s*([^\n|]+)(?:\s*\|\s*([^\n]+))?",
        blob,
    )
    if chap:
        section = section or chap.group(1).strip()
        if chap.group(2):
            marker = chap.group(2).strip()
    if not section:
        section = _section_hint(asked) or _section_hint(blob) or section
    if not marker:
        marker = _marker_hint(asked, section) or _marker_hint(blob, section)
    if section and "月" in section and not marker and (
        "格式" in asked or "完成" in asked or "添加" in asked or "本周" in asked
    ):
        marker = "工作完成情况"
    task.update(
        {
            "kind": "doc_edit",
            "status": "ready",
            "doc_url": doc_url,
            "instruction": (asked or str(task.get("instruction") or "")).strip(),
            "section": section,
            "marker": marker,
            "draft": draft,
            "source_snapshot": str(task.get("source_snapshot") or "")[:24000],
            "inserted_block_id": "",
            "last_error": "",
        }
    )
    save_artifact(chat_id, task)


def hermes_control_turn(chat_id: str, asked: str, *, action: str = "") -> str:
    """Let Hermes decide the reply; shell only supplies seed + optional draft stash."""
    if not hermes_available():
        return ""
    doc_seed, _task = _doc_seed(chat_id, asked)
    seed = _CONTROL_RULES
    if doc_seed:
        seed = f"{_CONTROL_RULES}\n\n{doc_seed}"
    elif action:
        seed = f"{_CONTROL_RULES}\n\n（无打开的文档任务；可用 feishu_* 自行取数。）"
    spoken = hermes_partner_turn(asked, seed_facts=seed, timeout=120)
    if spoken:
        spoken = salvage_spoken_reply(spoken) or spoken
    if not spoken or not _is_usable_reply(spoken, limit=2500):
        return ""
    _stash_draft_from_reply(chat_id, spoken, asked)
    if chat_id:
        save_turn(chat_id, kind="action", query=asked, action="hermes")
    return spoken
