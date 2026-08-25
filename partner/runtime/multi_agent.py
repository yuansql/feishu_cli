"""Local Multi-Agent coordination: researcher → executor → writer (no second router)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
import json
import os
import re
from typing import Any

from .planner import _clean_goal, _keyword, _step_templates, plan_steps
from ..core.trace import emit_trace

ALL_ROLES = ("researcher", "executor", "writer")


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
    active_roles: tuple[str, ...] = ALL_ROLES

    def as_plan_context(self) -> str:
        lines = ["【Multi-Agent 协作摘要 · 本地子角色】"]
        mapping = {
            "researcher": ("调研", self.researcher),
            "executor": ("执行", self.executor),
            "writer": ("交付", self.writer),
        }
        for role in self.active_roles:
            label, brief = mapping[role]
            if brief.source == "skipped":
                continue
            lines.append(f"【{label} · {brief.source}】")
            lines.append(brief.text.strip())
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "researcher": asdict(self.researcher),
            "executor": asdict(self.executor),
            "writer": asdict(self.writer),
            "active_roles": list(self.active_roles),
        }


def select_roles(goal: str) -> tuple[str, ...]:
    """Pick role subset by goal type. Executor always runs."""
    g = (goal or "").strip()
    roles: list[str] = ["executor"]
    research_hit = any(
        w in g
        for w in (
            "调研",
            "风险",
            "资料",
            "文档",
            "制度",
            "方案",
            "纪要",
            "审批",
            "搜",
            "分析",
            "核对",
            "盘点",
        )
    )
    write_hit = any(
        w in g
        for w in ("写", "周报", "报告", "汇总", "总结", "交付", "提纲", "文档")
    )
    # short checklist goals stay executor-only
    short = len(re.sub(r"\s+", "", g)) <= 8 and not research_hit and not write_hit
    if short:
        return ("executor",)
    if research_hit:
        roles.insert(0, "researcher")
    if write_hit:
        roles.append("writer")
    # complex / vague goals: full trio
    if "researcher" not in roles and "writer" not in roles:
        return ALL_ROLES
    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for role in roles:
        if role in seen:
            continue
        seen.add(role)
        out.append(role)
    return tuple(out)


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


def _skipped(role: str, label: str) -> RoleBrief:
    return RoleBrief(role, label, "（本目标未启用该角色）", "skipped")


def _build_role(role: str, goal: str, facts: str) -> RoleBrief:
    if role == "researcher":
        return RoleBrief(
            "researcher",
            "调研子智能体",
            _deterministic_researcher(goal, facts),
            "deterministic",
        )
    if role == "executor":
        return RoleBrief(
            "executor",
            "执行子智能体",
            _deterministic_executor(goal, facts),
            "deterministic",
        )
    return RoleBrief(
        "writer",
        "交付子智能体",
        _deterministic_writer(goal),
        "deterministic",
    )


def _parallel_deterministic(goal: str, facts: str, roles: tuple[str, ...]) -> MultiAgentResult:
    briefs: dict[str, RoleBrief] = {
        "researcher": _skipped("researcher", "调研子智能体"),
        "executor": _skipped("executor", "执行子智能体"),
        "writer": _skipped("writer", "交付子智能体"),
    }
    if len(roles) == 1:
        role = roles[0]
        briefs[role] = _build_role(role, goal, facts)
    else:
        with ThreadPoolExecutor(max_workers=min(3, len(roles))) as pool:
            futures = {
                pool.submit(_build_role, role, goal, facts): role for role in roles
            }
            for fut in as_completed(futures):
                role = futures[fut]
                briefs[role] = fut.result()
    return MultiAgentResult(
        researcher=briefs["researcher"],
        executor=briefs["executor"],
        writer=briefs["writer"],
        active_roles=roles,
    )


def _model_coordinate(
    goal: str,
    facts: str,
    roles: tuple[str, ...],
    *,
    timeout: int = 45,
) -> MultiAgentResult | None:
    from ..compose.llm import _invoke_hermes

    role_list = ", ".join(roles)
    prompt = f"""你是本地 Multi-Agent 协调器，输出严格 JSON。只填写这些角色：{role_list}。
角色说明：
- researcher：只分析已有事实，列出材料要点、缺口、建议补读方向；不编造。
- executor：基于事实给出 3-6 步可执行动作，每步对应飞书工具名（today/tasks/search/read/...）。
- writer：给出最终交付物结构（章节/列表），便于 summarize 汇总。

目标：{goal}

已观察事实：
{(facts or "暂无")[:12000]}

输出 JSON，键只能是启用角色名：
{{"researcher":"...","executor":"...","writer":"..."}}
"""
    raw = _invoke_hermes(prompt, timeout=timeout, mode="rewrite")
    payload = _extract_json(raw)
    if not isinstance(payload, dict):
        return None
    briefs: dict[str, RoleBrief] = {
        "researcher": _skipped("researcher", "调研子智能体"),
        "executor": _skipped("executor", "执行子智能体"),
        "writer": _skipped("writer", "交付子智能体"),
    }
    labels = {
        "researcher": "调研子智能体",
        "executor": "执行子智能体",
        "writer": "交付子智能体",
    }
    for role in roles:
        text = str(payload.get(role) or "").strip()
        if not text:
            return None
        briefs[role] = RoleBrief(role, labels[role], text[:4000], "model")
    return MultiAgentResult(
        researcher=briefs["researcher"],
        executor=briefs["executor"],
        writer=briefs["writer"],
        active_roles=roles,
    )


def coordinate(
    goal: str,
    facts: str,
    *,
    task_id: str = "",
    allow_model: bool = True,
) -> MultiAgentResult:
    """Run local role coordination; always returns a usable brief."""
    cleaned = _clean_goal(goal)
    roles = select_roles(cleaned)
    use_model = (
        allow_model
        and os.environ.get("FEISHU_PARTNER_NO_LLM") != "1"
        and os.environ.get("FEISHU_PARTNER_NO_MULTI_AGENT") != "1"
    )
    if use_model:
        modeled = _model_coordinate(cleaned, facts, roles)
        if modeled is not None:
            emit_trace(
                task_id,
                "multi_agent.coordinated",
                source="model",
                roles=list(roles),
            )
            return modeled
    result = _parallel_deterministic(cleaned, facts, roles)
    emit_trace(
        task_id,
        "multi_agent.coordinated",
        source="deterministic",
        roles=list(roles),
        parallel=len(roles) > 1,
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
