"""Agent decision brain: LLM JSON when available, else observation-aware heuristic."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

DecisionKind = Literal["tool", "finish", "confirm"]


@dataclass
class Decision:
    thought: str
    kind: DecisionKind
    tool: str = ""
    args: dict[str, str] = field(default_factory=dict)
    summary: str = ""


_READ_TOOLS = (
    "today",
    "tomorrow",
    "tasks",
    "search",
    "read",
    "person",
    "chats",
    "inbox",
    "minutes",
    "approval",
    "brief",
    "weekly",
)
_WRITE_TOOLS = ("followup_add", "task_create", "docs_create")


def tool_catalog(*, allow_writes: bool) -> str:
    lines = ["可读工具："]
    for name in _READ_TOOLS:
        lines.append(f"- {name}")
    if allow_writes:
        lines.append("可写工具（已获用户确认）：")
        for name in _WRITE_TOOLS:
            lines.append(f"- {name}")
    else:
        lines.append("写工具未确认：若需要写入，输出 kind=confirm。")
    return "\n".join(lines)


def _parse_decision(raw: str) -> Decision | None:
    text = (raw or "").strip()
    if not text:
        return None
    # tolerate ```json fences
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        blob = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(blob, dict):
        return None
    kind = str(blob.get("kind") or "").strip()
    if kind not in {"tool", "finish", "confirm"}:
        return None
    args_raw = blob.get("args") if isinstance(blob.get("args"), dict) else {}
    args = {str(k): str(v) for k, v in args_raw.items()}
    return Decision(
        thought=str(blob.get("thought") or "")[:500],
        kind=kind,  # type: ignore[arg-type]
        tool=str(blob.get("tool") or "").strip(),
        args=args,
        summary=str(blob.get("summary") or "").strip(),
    )


def _llm_decide(goal: str, observations: list[str], *, allow_writes: bool) -> Decision | None:
    from .settings import brain_mode, llm_disabled, load_agent_config, require_llm

    if llm_disabled():
        return None
    mode = brain_mode()
    if mode == "heuristic":
        return None
    if mode in {"auto", "hermes"}:
        from ...compose.llm import _invoke_hermes, hermes_available

        if hermes_available():
            obs = "\n".join(observations[-8:]) or "（尚无观察）"
            prompt = (
                "你是飞书工作伙伴的任务决策器。根据目标与已观察事实，决定下一步。"
                "只输出一个 JSON 对象，不要解释。\n"
                '格式：{"thought":"短理由","kind":"tool|finish|confirm",'
                '"tool":"工具名","args":{},"summary":"结束时给用户的摘要"}\n'
                "规则：只根据材料；不编造；写操作未确认时用 kind=confirm；"
                "材料够了用 kind=finish 并写 summary。\n\n"
                f"{tool_catalog(allow_writes=allow_writes)}\n\n"
                f"【目标】\n{goal}\n\n【已观察】\n{obs}\n"
            )
            raw = _invoke_hermes(prompt, timeout=60, mode="rewrite")
            parsed = _parse_decision(raw or "")
            if parsed:
                return parsed
        if mode == "hermes" and require_llm():
            return Decision(
                "Hermes 不可用且 require_llm=true",
                "finish",
                summary="Agent 需要 Hermes，但本机未就绪。请检查 agent.json / hermes。",
            )
    if mode in {"auto", "openai"}:
        parsed = _openai_decide(goal, observations, allow_writes=allow_writes)
        if parsed:
            return parsed
        if mode == "openai" and require_llm():
            return Decision(
                "OpenAI 不可用且 require_llm=true",
                "finish",
                summary="Agent 需要 OpenAI 兼容 key，请在 agent.json 或 OPENAI_API_KEY 配置。",
            )
    return None


def _openai_decide(
    goal: str, observations: list[str], *, allow_writes: bool
) -> Decision | None:
    import json
    import urllib.error
    import urllib.request

    from .settings import load_agent_config, openai_keys

    cfg = load_agent_config()
    keys = openai_keys()
    if not keys:
        return None
    base = str(cfg.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")
    model = str(cfg.get("openai_model") or "gpt-4o-mini").strip()
    obs = "\n".join(observations[-8:]) or "（尚无观察）"
    user = (
        f"{tool_catalog(allow_writes=allow_writes)}\n\n"
        f"【目标】\n{goal}\n\n【已观察】\n{obs}\n\n"
        "只输出一个 JSON："
        '{"thought":"...","kind":"tool|finish|confirm","tool":"...","args":{},"summary":"..."}'
    )
    body = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "你是飞书工作伙伴的任务决策器。只输出 JSON，不解释。",
            },
            {"role": "user", "content": user},
        ],
    }
    payload_bytes = json.dumps(body).encode("utf-8")
    for api_key in keys:
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=payload_bytes,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            continue
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            continue
        parsed = _parse_decision(str(content or ""))
        if parsed:
            return parsed
    return None


def _used_tools(observations: list[str]) -> set[str]:
    used: set[str] = set()
    for line in observations:
        match = re.match(r"^\[tool:([^\]]+)\]", line.strip())
        if match:
            used.add(match.group(1))
    return used


def heuristic_decide(
    goal: str,
    observations: list[str],
    *,
    allow_writes: bool,
) -> Decision:
    """Observation-aware fallback: still chooses next step from results, not a fixed plan table."""
    g = goal or ""
    used = _used_tools(observations)
    blob = "\n".join(observations)

    def need(*names: str) -> str | None:
        for name in names:
            if name not in used:
                return name
        return None

    if any(k in g for k in ("周报", "本周", "下周计划")):
        t = need("weekly", "today", "tasks")
        if t:
            return Decision(f"周报类目标先读 {t}", "tool", t, {})
    if any(k in g for k in ("明天", "明日")):
        t = need("tomorrow", "tasks")
        if t:
            return Decision(f"明天相关先读 {t}", "tool", t, {})
    if any(k in g for k in ("纪要", "会议")):
        t = need("minutes", "today", "tasks")
        if t:
            return Decision(f"会议相关先读 {t}", "tool", t, {})
    if any(k in g for k in ("审批",)):
        t = need("approval", "tasks")
        if t:
            return Decision(f"审批相关先读 {t}", "tool", t, {})
    if "搜" in g or "文档" in g:
        t = need("search")
        if t:
            q = re.sub(r".{0,6}搜\s*", "", g).strip() or g[:40]
            return Decision("需要文档搜索", "tool", "search", {"query": q})

    t = need("today", "tasks")
    if t:
        return Decision(f"先收集上下文：{t}", "tool", t, {})

    wants_write = any(k in g for k in ("创建待办", "写跟进", "建文档", "写入", "新建文档"))
    if wants_write and not allow_writes:
        return Decision("需要写回飞书，先请你确认", "confirm")
    if wants_write and allow_writes:
        if "待办" in g and "task_create" not in used:
            title = g[:80]
            return Decision("创建待办", "tool", "task_create", {"title": title})
        if "跟进" in g and "followup_add" not in used:
            return Decision("写入跟进账", "tool", "followup_add", {"text": g[:200]})
        if ("文档" in g or "建档" in g) and "docs_create" not in used:
            return Decision("新建文档", "tool", "docs_create", {"title": g[:60]})

    summary = blob.strip() or f"已根据目标「{g}」完成观察，暂无更多可执行步骤。"
    if len(summary) > 2500:
        summary = summary[:2500] + "…"
    return Decision("观察已够，汇总交付", "finish", summary=summary)


def decide(
    goal: str,
    observations: list[str],
    *,
    allow_writes: bool = False,
) -> Decision:
    from .settings import require_llm

    llm = _llm_decide(goal, observations, allow_writes=allow_writes)
    if llm is not None:
        if llm.kind == "tool":
            allowed = set(_READ_TOOLS) | (set(_WRITE_TOOLS) if allow_writes else set())
            if llm.tool not in allowed:
                if require_llm():
                    return Decision(
                        "模型选了未允许的工具",
                        "finish",
                        summary=f"决策非法工具：{llm.tool}",
                    )
                return heuristic_decide(goal, observations, allow_writes=allow_writes)
        return llm
    if require_llm():
        return Decision(
            "需要 LLM 但未可用",
            "finish",
            summary="agent.json 里 require_llm=true，但 Hermes/OpenAI 都不可用。",
        )
    return heuristic_decide(goal, observations, allow_writes=allow_writes)


def looks_like_tool_failure(text: str) -> bool:
    blob = text or ""
    markers = ("飞书权威失败", "缺权限", "requires confirmation", "unknown tool", "Error:")
    return any(m in blob for m in markers)
