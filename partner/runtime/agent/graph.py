"""LangGraph think→act loop for Feishu long tasks."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from ..tool_registry import execute_tool, get_tool
from .brain import Decision, decide, looks_like_tool_failure


class AgentState(TypedDict, total=False):
    goal: str
    chat_id: str
    observations: list[str]
    pending_write: dict[str, Any] | None
    status: str
    summary: str
    steps_taken: int
    max_steps: int
    allow_writes: bool
    last_thought: str
    last_decision: dict[str, Any]


def _decision_to_dict(d: Decision) -> dict[str, Any]:
    return {
        "thought": d.thought,
        "kind": d.kind,
        "tool": d.tool,
        "args": d.args,
        "summary": d.summary,
    }


def think_node(state: AgentState) -> dict[str, Any]:
    goal = str(state.get("goal") or "")
    observations = list(state.get("observations") or [])
    allow_writes = bool(state.get("allow_writes"))
    decision = decide(goal, observations, allow_writes=allow_writes)
    updates: dict[str, Any] = {
        "last_thought": decision.thought,
        "last_decision": _decision_to_dict(decision),
    }
    if decision.kind == "finish":
        updates["status"] = "done"
        updates["summary"] = decision.summary or decision.thought
    elif decision.kind == "confirm":
        updates["status"] = "blocked"
        updates["summary"] = decision.thought or "写回飞书需要你确认：回复「确认写入」。"
        updates["pending_write"] = {"reason": "confirmation"}
    return updates


def act_node(state: AgentState) -> dict[str, Any]:
    decision = state.get("last_decision") or {}
    kind = str(decision.get("kind") or "")
    if kind != "tool":
        return {}
    tool = str(decision.get("tool") or "").strip()
    args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
    clean_args = {str(k): str(v) for k, v in args.items()}
    steps = int(state.get("steps_taken") or 0) + 1
    allow_writes = bool(state.get("allow_writes"))
    spec = get_tool(tool)
    observations = list(state.get("observations") or [])
    if spec is None:
        observations.append(f"[tool:{tool}] unknown tool")
        return {
            "observations": observations,
            "steps_taken": steps,
            "last_thought": f"未知工具 {tool}",
        }
    if spec.effect == "write" and not allow_writes:
        return {
            "status": "blocked",
            "pending_write": {"tool": tool, "args": clean_args},
            "summary": f"准备执行写操作 `{tool}`，回复「确认写入」后继续。",
            "steps_taken": steps,
        }
    try:
        result = execute_tool(tool, clean_args, confirmed=allow_writes and spec.effect == "write")
    except Exception as exc:  # noqa: BLE001 — surface to observations for rethinking
        result = f"Error: {exc}"
    observations.append(f"[tool:{tool}] {result}")
    updates: dict[str, Any] = {
        "observations": observations,
        "steps_taken": steps,
        "last_thought": str((state.get("last_decision") or {}).get("thought") or ""),
    }
    if looks_like_tool_failure(result) and steps >= int(state.get("max_steps") or 8):
        updates["status"] = "failed"
        updates["summary"] = result[:1500]
    return updates


def route_after_think(state: AgentState) -> Literal["act", "end"]:
    status = str(state.get("status") or "")
    if status in {"done", "blocked", "failed", "cancelled"}:
        return "end"
    decision = state.get("last_decision") or {}
    if str(decision.get("kind") or "") == "tool":
        return "act"
    return "end"


def route_after_act(state: AgentState) -> Literal["think", "end"]:
    status = str(state.get("status") or "")
    if status in {"done", "blocked", "failed", "cancelled"}:
        return "end"
    steps = int(state.get("steps_taken") or 0)
    max_steps = int(state.get("max_steps") or 8)
    if steps >= max_steps:
        return "end"
    return "think"


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("think", think_node)
    graph.add_node("act", act_node)
    graph.add_edge(START, "think")
    graph.add_conditional_edges("think", route_after_think, {"act": "act", "end": END})
    graph.add_conditional_edges("act", route_after_act, {"think": "think", "end": END})
    return graph.compile()


_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def run_loop(
    *,
    goal: str,
    chat_id: str = "",
    observations: list[str] | None = None,
    allow_writes: bool = False,
    max_steps: int = 8,
    pending_write: dict[str, Any] | None = None,
) -> AgentState:
    graph = get_graph()
    initial: AgentState = {
        "goal": goal,
        "chat_id": chat_id,
        "observations": list(observations or []),
        "pending_write": pending_write,
        "status": "running",
        "summary": "",
        "steps_taken": 0,
        "max_steps": max_steps,
        "allow_writes": allow_writes,
        "last_thought": "",
        "last_decision": {},
    }
    final = graph.invoke(initial)
    status = str(final.get("status") or "")
    if status == "running":
        # hit max steps without finish
        obs = list(final.get("observations") or [])
        final["status"] = "done" if obs else "failed"
        final["summary"] = final.get("summary") or (
            "\n".join(obs[-5:]) if obs else "任务未产出可交付结果。"
        )
    return final  # type: ignore[return-value]
