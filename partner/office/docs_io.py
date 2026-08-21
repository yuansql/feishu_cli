from __future__ import annotations

from datetime import datetime, timedelta, timezone
from ..compose.formatters import help_text, document_markdown, draft_doc_markdown, format_approvals, format_clarify, format_doc, format_docs_search, format_lark_error, format_minutes, format_topic_brief, material_pairs
from ..core.lark import run_lark
from .calendar_views import _created_doc_link

CN_TZ = timezone(timedelta(hours=8))

def write_doc_text(query: str='') -> str:
    asked = (query or '').strip()
    if not asked:
        return '说一下要写的标题，或把提纲文档链接发我。'
    url = ''
    title = asked
    from ..routing.intents import _URL_RE
    url_m = _URL_RE.search(asked)
    if url_m:
        url = url_m.group(0).rstrip(')。,，')
        leftover = _URL_RE.sub(' ', asked).strip()
        title = leftover or '未命名文档'
    if not url:
        docs = run_lark(['docs', '+search', '--query', asked, '--page-size', '5'], as_identity='user')
        if docs.get('ok') is False:
            return format_lark_error(docs)
        pairs = material_pairs(docs, limit=5)
        if not pairs or not pairs[0][1]:
            return f'没搜到《{asked}》提纲，换个标题或把链接发我。'
        title, url = pairs[0]
    fetched = run_lark(['docs', '+fetch', '--doc', url, '--doc-format', 'markdown', '--detail', 'simple'], as_identity='user')
    outline = document_markdown(fetched)
    extras: list[str] = []
    related = run_lark(['docs', '+search', '--query', title, '--page-size', '5'], as_identity='user')
    if related.get('ok'):
        for rel_title, rel_url in material_pairs(related, limit=5):
            if not rel_url or rel_url == url:
                continue
            extra_hit = run_lark(['docs', '+fetch', '--doc', rel_url, '--doc-format', 'markdown', '--detail', 'simple'], as_identity='user')
            body = document_markdown(extra_hit)
            if body:
                extras.append(f'# {rel_title}\n{body}')
            if len(extras) >= 2:
                break
    markdown = draft_doc_markdown(title, outline, extras)
    new_title = f'{title} · {datetime.now(CN_TZ).date().isoformat()}'
    created = run_lark(['docs', '+create', '--title', new_title, '--doc-format', 'markdown', '--content', markdown], as_identity='user')
    if created.get('ok'):
        link = _created_doc_link(created)
        extra = f'\n{link}' if link else ''
        return f'已生成云文档《{new_title}》。{extra}\n聊天里不贴正文，打开文档看。'
    return format_lark_error(created)

def docs_search_text(query: str) -> str:
    if not query:
        return '请给出关键词，例如：搜 周报'
    payload = run_lark(['docs', '+search', '--query', query, '--page-size', '8'], as_identity='user')
    return format_docs_search(payload, query=query)

def search_text(query: str) -> str:
    if not query:
        return '请给出关键词，例如：搜 周报'
    from .knowledge import search_prioritized
    return search_prioritized(query)

def read_text(doc: str) -> str:
    if not doc:
        return '请给出文档链接或 token，例如：读 https://...'
    payload = run_lark(['docs', '+fetch', '--doc', doc, '--doc-format', 'markdown', '--detail', 'simple'], as_identity='user')
    return format_doc(payload)

def minutes_text() -> str:
    start = (datetime.now(CN_TZ) - timedelta(days=14)).date().isoformat()
    payload = run_lark(['minutes', '+search', '--participant-ids', 'me', '--start', start, '--page-size', '8'], as_identity='user')
    return format_minutes(payload)

def approval_text() -> str:
    payload = run_lark(['approval', 'tasks', 'query', '--topic', '1', '--page-size', '15'], as_identity='user')
    return format_approvals(payload)

def analyze_result(query: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (reply, title/url pairs). Several hits → ask which. Never dump search lists."""
    if not query:
        return (help_text(), [])
    docs = run_lark(['docs', '+search', '--query', query, '--page-size', '5'], as_identity='user')
    if docs.get('ok') is False:
        return (format_clarify(query, []), [])
    pairs = material_pairs(docs, limit=4)
    if len(pairs) != 1 or not pairs[0][1]:
        return (format_clarify(query, [title for title, _url in pairs]), pairs)
    title, url = pairs[0]
    fetched = run_lark(['docs', '+fetch', '--doc', url, '--doc-format', 'markdown', '--detail', 'simple'], as_identity='user')
    return (format_topic_brief(query, title, url, document_markdown(fetched)), pairs)

def analyze_text(query: str) -> str:
    text, _pairs = analyze_result(query)
    return text
