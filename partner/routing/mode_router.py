"""Mixed mode routing: task / workflow / knowledge / model."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from .intents import Intent, looks_like_bare_search, looks_like_plan
from ..runtime.workflow import match_workflow

AgentMode = Literal["task", "workflow", "knowledge", "model"]

_KNOWLEDGE_PREFIXES = (
    "知识问答",
    "问答",
    "根据文档",
    "根据知识库",
    "制度查询",
    "查制度",
    "资料问答",
)


@dataclass(frozen=True)
class RouteDecision:
    mode: AgentMode
    workflow_id: str = ""
    query: str = ""


def _knowledge_query(raw: str) -> str:
    text = (raw or "").strip()
    for prefix in _KNOWLEDGE_PREFIXES:
        if text.startswith(prefix):
            rest = text[len(prefix) :].strip(" ：:，,")
            if rest:
                return rest
    if looks_like_bare_search(text):
        return text[2:].strip() if text.startswith("搜") else text
    return text


def looks_like_knowledge_qa(raw: str) -> bool:
    text = (raw or "").strip()
    if not text:
        return False
    if any(text.startswith(prefix) for prefix in _KNOWLEDGE_PREFIXES):
        return True
    if re.search(r"(根据|查阅).{0,6}(文档|知识库|制度)", text):
        return True
    return False


def route_request(text: str, intent: Intent) -> RouteDecision:
    """Resolve agent mode without replacing intent parsing."""
    asked = (text or "").strip()
    if looks_like_plan(asked) or intent.action == "plan":
        goal = (intent.query or asked).strip()
        return RouteDecision(mode="task", query=goal)
    wf_id = match_workflow(asked)
    if wf_id:
        return RouteDecision(mode="workflow", workflow_id=wf_id, query=asked)
    if looks_like_knowledge_qa(asked) or (
        intent.action == "search" and any(p in asked for p in ("制度", "知识库", "规范"))
    ):
        return RouteDecision(mode="knowledge", query=_knowledge_query(asked or intent.query))
    if intent.action in {
        "task_continue",
        "task_status",
        "task_confirm",
        "task_cancel",
    }:
        return RouteDecision(mode="task", query=asked)
    return RouteDecision(mode="model", query=asked)
