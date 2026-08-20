"""Prioritized knowledge sources for doc Q&A (Aily-aligned, no full RAG)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_SOURCES: list[dict[str, Any]] = [
    {"name": "云文档", "type": "docs_search", "priority": 1, "enabled": True},
    {"name": "知识库空间", "type": "wiki_spaces", "priority": 2, "enabled": True},
]


def config_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_KNOWLEDGE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner" / "knowledge.json"


def load_sources() -> list[dict[str, Any]]:
    path = config_path()
    if not path.is_file():
        return list(DEFAULT_SOURCES)
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return list(DEFAULT_SOURCES)
    raw = blob.get("sources") if isinstance(blob, dict) else blob
    if not isinstance(raw, list):
        return list(DEFAULT_SOURCES)
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("enabled") is False:
            continue
        out.append(item)
    return out or list(DEFAULT_SOURCES)


def _failed(text: str) -> bool:
    blob = (text or "").strip()
    if not blob:
        return True
    markers = ("飞书权威失败", "missing_scope", "缺权限", "没搜到", "找不到")
    lower = blob.lower()
    return any(token in blob or token in lower for token in markers)


def search_prioritized(query: str) -> str:
    """Walk configured sources; first non-empty hit wins."""
    q = (query or "").strip()
    if not q:
        return "请给出搜索关键词。"
    from .actions import docs_search_text
    from .formatters import format_wiki_spaces
    from .lark import run_lark

    errors: list[str] = []
    for src in sorted(load_sources(), key=lambda row: int(row.get("priority") or 99)):
        name = str(src.get("name") or src.get("type") or "source")
        kind = str(src.get("type") or "docs_search")
        if kind == "docs_search":
            body = docs_search_text(q)
            if not _failed(body):
                return f"【知识源·{name}】\n{body}"
            errors.append(body)
            continue
        if kind == "wiki_spaces":
            wiki = run_lark(["wiki", "+space-list"], as_identity="user")
            body = format_wiki_spaces(wiki, query=q)
            if body.strip() and not _failed(body):
                return f"【知识源·{name}】\n{body}"
            errors.append(body or f"【知识源·{name}】无结果")
            continue
        errors.append(f"未知知识源类型：{kind}")
    if errors:
        return errors[0]
    return "Configured knowledge sources returned nothing."


def knowledge_answer(query: str, *, chat_id: str = "") -> str:
    """Knowledge Q&A: local RAG recall first, then prioritized online sources."""
    q = (query or "").strip()
    if not q:
        return "请给出要问的知识问题或关键词。"
    if os.environ.get("FEISHU_PARTNER_NO_RAG") != "1":
        from .rag import rag_answer

        local = rag_answer(q)
        if local and "本地索引未命中" not in local:
            if chat_id:
                from .session import save_turn

                save_turn(chat_id, kind="knowledge", query=q, action="knowledge", pairs=[])
            return local
    body = search_prioritized(q)
    if _failed(body):
        return body
    from .actions import analyze_text

    if "http" in body or "feishu.cn" in body:
        analyzed = analyze_text(q)
        if analyzed and not _failed(analyzed):
            if chat_id:
                from .session import save_turn

                save_turn(
                    chat_id,
                    kind="knowledge",
                    query=q,
                    action="knowledge",
                    pairs=[],
                )
            return analyzed
    if chat_id:
        from .session import save_turn

        save_turn(chat_id, kind="knowledge", query=q, action="knowledge", pairs=[])
    return body
