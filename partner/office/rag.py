"""Lightweight local RAG: chunking, terminology expansion, keyword recall (stdlib only)."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CN_TZ = timezone(timedelta(hours=8))
_TOKEN_RE = re.compile(r"[A-Za-z0-9_+-]{2,}|[\u4e00-\u9fff]")


def rag_dir() -> Path:
    override = os.environ.get("FEISHU_PARTNER_RAG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner" / "rag"


def index_path() -> Path:
    return rag_dir() / "index.jsonl"


def terminology_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_TERMINOLOGY")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner" / "terminology.json"


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "") if t.strip()]


@dataclass(frozen=True)
class ChunkHit:
    chunk_id: str
    doc_id: str
    title: str
    url: str
    text: str
    score: float


def load_terminology() -> list[dict[str, Any]]:
    path = terminology_path()
    if not path.is_file():
        return []
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = blob.get("terms") if isinstance(blob, dict) else blob
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def expand_query(query: str) -> tuple[str, list[str]]:
    """Expand query with terminology aliases/definitions."""
    q = (query or "").strip()
    if not q:
        return q, []
    expanded_tokens = tokenize(q)
    notes: list[str] = []
    folded = re.sub(r"\s+", "", q).lower()
    for row in load_terminology():
        term = str(row.get("term") or "").strip()
        if not term:
            continue
        aliases = row.get("aliases")
        alias_list = (
            [str(a).strip() for a in aliases if str(a).strip()]
            if isinstance(aliases, list)
            else []
        )
        candidates = [term, *alias_list]
        hit = any(
            re.sub(r"\s+", "", c).lower() in folded or c.lower() in q.lower()
            for c in candidates
        )
        if not hit:
            continue
        definition = str(row.get("definition") or "").strip()
        expanded_tokens.extend(tokenize(term))
        expanded_tokens.extend(tokenize(" ".join(alias_list)))
        if definition:
            notes.append(f"{term}：{definition[:200]}")
    # dedupe preserve order
    seen: set[str] = set()
    tokens: list[str] = []
    for tok in expanded_tokens:
        if tok in seen:
            continue
        seen.add(tok)
        tokens.append(tok)
    return q, notes


def chunk_markdown(text: str, *, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    body = (text or "").strip()
    if not body:
        return []
    parts = re.split(r"\n{2,}", body)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        if len(buf) + len(piece) + 2 <= chunk_size:
            buf = f"{buf}\n\n{piece}".strip() if buf else piece
            continue
        if buf:
            chunks.append(buf)
        if len(piece) <= chunk_size:
            buf = piece
            continue
        start = 0
        while start < len(piece):
            end = min(len(piece), start + chunk_size)
            chunks.append(piece[start:end].strip())
            if end >= len(piece):
                break
            start = max(end - overlap, start + 1)
        buf = ""
    if buf:
        chunks.append(buf)
    return [c for c in chunks if c.strip()]


def _chunk_id(doc_id: str, index: int, text: str) -> str:
    digest = hashlib.sha256(f"{doc_id}:{index}:{text[:200]}".encode()).hexdigest()
    return digest[:16]


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def append_chunks(
    *,
    doc_id: str,
    title: str,
    url: str,
    markdown: str,
    source: str = "manual",
) -> int:
    rag_dir().mkdir(parents=True, exist_ok=True)
    chunks = chunk_markdown(markdown)
    if not chunks:
        return 0
    existing = {row.get("chunk_id") for row in _read_index() if row.get("doc_id") == doc_id}
    written = 0
    with index_path().open("a", encoding="utf-8") as handle:
        for index, text in enumerate(chunks):
            cid = _chunk_id(doc_id, index, text)
            if cid in existing:
                continue
            row = {
                "chunk_id": cid,
                "doc_id": doc_id,
                "title": title[:200],
                "url": url[:500],
                "chunk_index": index,
                "text": text[:4000],
                "source": source,
                "indexed_at": _now_iso(),
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1
    return written


def _read_index() -> list[dict[str, Any]]:
    path = index_path()
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    except (OSError, json.JSONDecodeError):
        return []
    return rows


def index_stats_text() -> str:
    rows = _read_index()
    docs = {str(r.get("doc_id") or "") for r in rows}
    terms = len(load_terminology())
    return (
        f"本地 RAG 索引：{len(rows)} 个切片，{len(docs)} 篇文档；术语条目 {terms}。"
        f"\n索引路径：{index_path()}\n术语路径：{terminology_path()}"
    )


def _score(query_tokens: list[str], chunk_text: str) -> float:
    if not query_tokens:
        return 0.0
    chunk_tokens = tokenize(chunk_text)
    if not chunk_tokens:
        return 0.0
    chunk_set = set(chunk_tokens)
    hits = sum(1 for tok in query_tokens if tok in chunk_set)
    if hits == 0:
        return 0.0
    # mild length normalization
    return hits / (len(query_tokens) ** 0.5)


def retrieve(query: str, *, top_k: int = 5, min_score: float = 0.55) -> list[ChunkHit]:
    _, term_notes = expand_query(query)
    query_tokens = tokenize(query)
    for note in term_notes:
        query_tokens.extend(tokenize(note))
    seen: set[str] = set()
    deduped: list[str] = []
    for tok in query_tokens:
        if tok in seen:
            continue
        seen.add(tok)
        deduped.append(tok)
    hits: list[ChunkHit] = []
    for row in _read_index():
        text = str(row.get("text") or "")
        score = _score(deduped, text)
        if score < min_score:
            continue
        hits.append(
            ChunkHit(
                chunk_id=str(row.get("chunk_id") or ""),
                doc_id=str(row.get("doc_id") or ""),
                title=str(row.get("title") or ""),
                url=str(row.get("url") or ""),
                text=text,
                score=round(score, 3),
            )
        )
    hits.sort(key=lambda item: item.score, reverse=True)
    return hits[: max(1, top_k)]


def format_hits(query: str, hits: list[ChunkHit], *, term_notes: list[str] | None = None) -> str:
    lines = [f"知识问答：{query}", ""]
    if term_notes:
        lines.extend(["【术语】", *[f"- {n}" for n in term_notes], ""])
    if not hits:
        lines.append("本地索引未命中；将回退在线检索。")
        return "\n".join(lines)
    lines.append("【召回片段】")
    for index, hit in enumerate(hits, 1):
        header = hit.title or hit.doc_id or "文档"
        if hit.url:
            header = f"{header} ({hit.url})"
        preview = hit.text.replace("\n", " ")
        if len(preview) > 320:
            preview = preview[:320] + "…"
        lines.append(f"{index}. [{hit.score}] {header}")
        lines.append(f"   {preview}")
    lines.extend(
        [
            "",
            "【说明】",
            "- 以上来自本地 RAG 切片；未覆盖时请补索引：`feishu rag index 关键词`。",
            "- 回答须以片段为准，缺证据时明确未知。",
        ]
    )
    return "\n".join(lines)


def rag_answer(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return "请给出问题或关键词。"
    expanded, term_notes = expand_query(q)
    hits = retrieve(expanded or q, top_k=5)
    if not hits:
        return ""
    return format_hits(q, hits, term_notes=term_notes)


def index_from_search(query: str, *, max_docs: int = 3) -> str:
    """Fetch Feishu docs via search+read and append chunks to local index."""
    q = (query or "").strip()
    if not q:
        return "请给出 indexing 关键词。"
    from ..actions import docs_search_text, read_text
    from ..compose.formatters import material_pairs
    from ..core.lark import run_lark

    search_body = docs_search_text(q)
    if not search_body.strip():
        return "搜索无结果，无法建索引。"

    payload = run_lark(
        ["docs", "+search", "--query", q, "--page-size", str(max_docs)],
        as_identity="user",
    )
    pairs = material_pairs(payload, limit=max_docs)
    if not pairs:
        return f"没有可索引文档。搜索输出：\n{search_body[:500]}"
    total = 0
    for title, url in pairs:
        if not url:
            continue
        doc_id = hashlib.sha256(url.encode()).hexdigest()[:16]
        body = read_text(url)
        if not body.strip() or "飞书权威失败" in body:
            continue
        total += append_chunks(
            doc_id=doc_id,
            title=title or q,
            url=url,
            markdown=body,
            source="feishu_search",
        )
    return f"已为「{q}」索引 {total} 个新切片（文档 {len(pairs)} 篇）。\n{index_stats_text()}"


def rag_cli(*, index: str = "", query: str = "", stats: bool = False) -> str:
    if stats:
        return index_stats_text()
    if index:
        return index_from_search(index)
    if query:
        body = rag_answer(query)
        return body or "本地索引未命中。"
    return (
        "用法：\n"
        "  feishu rag stats\n"
        "  feishu rag index 关键词\n"
        "  feishu rag query 问题"
    )
