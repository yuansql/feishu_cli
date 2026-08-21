"""Local Multi-Agent coordination: researcher → executor → writer (no second router)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
import re
from typing import Any

from .planner import _clean_goal, _keyword, _step_templates, plan_steps
from ..core.trace import emit_trace


@dataclass(frozen=True)
class RoleBrief:
    role: str
    label: str
    text: str
    source: str


@dataclass(frozen=True)
class MultiAgentResult:
    researcher: RoleBrief
    executor: RoleBrief
    writer: RoleBrief

    def as_plan_context(self) -> str:
        return "\n".join(
            [
                "【Multi-Agent 协作摘要 · 本地子角色】",
                f"【调研 · {self.researcher.source}】",
                self.researcher.text.strip(),
                f"【执行 · {self.executor.source}】",
                self.executor.text.strip(),
                f"【交付 · {self.writer.source}】",
                self.writer.text.strip(),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "researcher": asdict(self.researcher),
            "executor": asdict(self.executor),
            "writer": asdict(self.writer),
        }


def _extract_json(raw: str) -> dict[str, Any] | None:
    blob = (raw or "").strip()
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(blob[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _deterministic_researcher(goal: str, facts: str) -> str:
    lines = ["材料盘点（基于已观察事实）："]
    chunks = [c.strip() for c in re.split(r"\n{2,}", facts or "") if c.strip()]
    if not chunks:
        lines.append("- 还没有可读材料；优先 today / tasks / search。")
    else:
        for chunk in chunks[:8]:
            head = chunk.split("\n", 1)[0][:80]
            lines.append(f"- {head}")
    gaps: list[str] = []
    lower = (facts or "").lower()
    if any(w in goal for w in ("文档", "资料", "制度", "方案")) and "search" not in lower:
        gaps.append(f"补 search：{_keyword(goal)}")
    if any(w in goal for w in ("会议", "纪要")) and "minutes" not in lower:
        gaps.append("补 minutes：最近会议纪要")
    if any(w in goal for w in ("审批", "流程")) and "approval" not in lower:
        gaps.append("补 approval：待办审批")
    if any(w in goal for w in ("跟进", "催", "谁找我")) and "inbox" not in lower:
        gaps.append("补 inbox：待跟进与 @")
    if gaps:
        lines.extend(["", "建议补读：", *[f"- {item}" for item in gaps]])
    return "\n".join(lines)


def _deterministic_executor(goal: str, facts: str) -> str:
    steps = plan_steps(goal, facts)
    lines = ["可验证执行序列（工具级）："]
    for index, step in enumerate(steps, 1):
        tool = str(step.get("tool") or "")
        title = str(step.get("title") or tool)
        confirm = " · 需确认" if step.get("requires_confirm") else ""
        lines.append(f"{index}. {title} → {tool}{confirm}")
    if not steps:
        lines.append("1. 先 today/tasks 建立事实基线")
    return "\n".join(lines)


def _deterministic_writer(goal: str) -> str:
    lines = ["交付结构建议："]
    for index, item in enumerate(_step_templates(goal), 1):
        lines.append(f"{index}. {item}")
    lines.extend(["", f"最终对齐目标：{goal}"])
    return "\n".join(lines)


def _model_coordinate(goal: str, facts: str, *, timeout: int = 45) -> MultiAgentResult | None:
    from ..compose.llm import _invoke_hermes

    prompt = f"""你是本地 Multi-Agent 协调器，输出严格 JSON，三个子角色分工：
1. researcher：只分析已有事实，列出材料要点、缺口、建议补读方向；不编造。
2. executor：基于事实给出 3-6 步可执行动作，每步对应飞书工具名（today/tasks/search/read/...）。
3. writer：给出最终交付物结构（章节/列表），便于 summarize 汇总。

目标：{goal}

已观察事实：
{(facts or "暂无")[:12000]}

输出 JSON：
{{"researcher":"...","executor":"...","writer":"..."}}
"""
    raw = _invoke_hermes(prompt, timeout=timeout, mode="rewrite")
    payload = _extract_json(raw)
    if not isinstance(payload, dict):
        return None
    parts: dict[str, str] = {}
    for key in ("researcher", "executor", "writer"):
        text = str(payload.get(key) or "").strip()
        if not text:
            return None
        parts[key] = text[:4000]
    return MultiAgentResult(
        researcher=RoleBrief("researcher", "调研子智能体", parts["researcher"], "model"),
        executor=RoleBrief("executor", "执行子智能体", parts["executor"], "model"),
        writer=RoleBrief("writer", "交付子智能体", parts["writer"], "model"),
    )


def coordinate(
    goal: str,
    facts: str,
    *,
    task_id: str = "",
    allow_model: bool = True,
) -> MultiAgentResult:
    """Run local three-role coordination; always returns a usable brief."""
    cleaned = _clean_goal(goal)
    use_model = (
        allow_model
        and os.environ.get("FEISHU_PARTNER_NO_LLM") != "1"
        and os.environ.get("FEISHU_PARTNER_NO_MULTI_AGENT") != "1"
    )
    if use_model:
        modeled = _model_coordinate(cleaned, facts)
        if modeled is not None:
            emit_trace(
                task_id,
                "multi_agent.coordinated",
                source="model",
                roles=["researcher", "executor", "writer"],
            )
            return modeled
    result = MultiAgentResult(
        researcher=RoleBrief(
            "researcher",
            "调研子智能体",
            _deterministic_researcher(cleaned, facts),
            "deterministic",
        ),
        executor=RoleBrief(
            "executor",
            "执行子智能体",
            _deterministic_executor(cleaned, facts),
            "deterministic",
        ),
        writer=RoleBrief(
            "writer",
            "交付子智能体",
            _deterministic_writer(cleaned),
            "deterministic",
        ),
    )
    emit_trace(
        task_id,
        "multi_agent.coordinated",
        source="deterministic",
        roles=["researcher", "executor", "writer"],
    )
    return result


def enrich_facts_for_plan(
    goal: str,
    facts: str,
    *,
    task_id: str = "",
) -> tuple[str, MultiAgentResult]:
    """Append multi-agent brief to planner facts."""
    from ..office.memory import memory_context_for_goal

    base = (facts or "").strip()
    mem = memory_context_for_goal(goal)
    if mem:
        base = f"{base}\n\n{mem}".strip() if base else mem
    result = coordinate(goal, base, task_id=task_id)
    merged = base
    context = result.as_plan_context()
    if merged:
        merged = f"{merged}\n\n{context}"
    else:
        merged = context
    return merged[:16000], result
