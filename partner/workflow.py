"""Configurable Workflow DSL and executor (JSON)."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from pathlib import Path
from typing import Any

from .planner import _keyword
from .tool_registry import execute_tool, read_tools
from .trace import emit_trace


@dataclass(frozen=True)
class WorkflowStep:
    title: str
    tool: str
    args: dict[str, str]


@dataclass(frozen=True)
class Workflow:
    id: str
    name: str
    description: str
    triggers: tuple[str, ...]
    steps: tuple[WorkflowStep, ...]
    finalize: str


def _bundled_defaults_path() -> Path:
    return Path(__file__).resolve().parent / "workflows" / "default.json"


def user_workflows_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_WORKFLOWS")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner" / "workflows.json"


def _parse_step(raw: dict[str, Any]) -> WorkflowStep | None:
    if not isinstance(raw, dict):
        return None
    tool = str(raw.get("tool") or "").strip()
    if not tool:
        return None
    args_raw = raw.get("args")
    args = (
        {str(k): str(v) for k, v in args_raw.items()}
        if isinstance(args_raw, dict)
        else {}
    )
    return WorkflowStep(
        title=str(raw.get("title") or tool).strip()[:160],
        tool=tool,
        args=args,
    )


def _parse_workflow(raw: dict[str, Any]) -> Workflow | None:
    if not isinstance(raw, dict):
        return None
    wid = str(raw.get("id") or "").strip()
    if not wid:
        return None
    triggers_raw = raw.get("triggers")
    triggers: list[str] = []
    if isinstance(triggers_raw, list):
        triggers = [str(item).strip() for item in triggers_raw if str(item).strip()]
    steps_raw = raw.get("steps")
    steps: list[WorkflowStep] = []
    if isinstance(steps_raw, list):
        for item in steps_raw:
            step = _parse_step(item)
            if step is not None:
                steps.append(step)
    if not steps:
        return None
    return Workflow(
        id=wid,
        name=str(raw.get("name") or wid).strip(),
        description=str(raw.get("description") or "").strip(),
        triggers=tuple(triggers),
        steps=tuple(steps),
        finalize=str(raw.get("finalize") or "summarize").strip() or "summarize",
    )


def _load_file(path: Path) -> list[Workflow]:
    if not path.is_file():
        return []
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = blob.get("workflows") if isinstance(blob, dict) else blob
    if not isinstance(rows, list):
        return []
    out: list[Workflow] = []
    for item in rows:
        parsed = _parse_workflow(item)
        if parsed is not None:
            out.append(parsed)
    return out


def load_workflows() -> dict[str, Workflow]:
    merged: dict[str, Workflow] = {}
    for wf in _load_file(_bundled_defaults_path()):
        merged[wf.id] = wf
    for wf in _load_file(user_workflows_path()):
        merged[wf.id] = wf
    return merged


def list_workflows_text() -> str:
    rows = load_workflows()
    if not rows:
        return "当前没有可用 Workflow。"
    lines = ["本地 Workflow（JSON 配置）：", ""]
    for wf in rows.values():
        triggers = " / ".join(wf.triggers[:4])
        lines.append(f"- {wf.id} · {wf.name}")
        if wf.description:
            lines.append(f"  {wf.description}")
        if triggers:
            lines.append(f"  触发：{triggers}")
        lines.append("")
    lines.append("用法：飞书内说触发词，或 `workflow:<id>`。")
    return "\n".join(lines).rstrip()


def match_workflow(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    if lowered.startswith("workflow:"):
        wid = raw.split(":", 1)[1].strip()
        return wid if wid in load_workflows() else None
    folded = re.sub(r"\s+", "", raw)
    for wf in load_workflows().values():
        for trigger in wf.triggers:
            trig = re.sub(r"\s+", "", trigger)
            if not trig:
                continue
            if folded == trig or raw == trigger or raw.startswith(trigger):
                return wf.id
    return None


def _render_args(args: dict[str, str], *, goal: str) -> dict[str, str]:
    key = _keyword(goal)
    out: dict[str, str] = {}
    for name, value in args.items():
        out[name] = (
            value.replace("{keyword}", key)
            .replace("{goal}", goal[:200])
            .strip()
        )
    return out


def _finalize_summarize(*, goal: str, steps: list[dict[str, Any]]) -> str:
    lines = [f"Workflow 完成：{goal}", "", "【步骤结果】"]
    for row in steps:
        title = str(row.get("title") or "")
        status = str(row.get("status") or "")
        body = str(row.get("result") or row.get("error") or "").strip()
        if not body:
            continue
        preview = body.replace("\n", " ")
        if len(preview) > 200:
            preview = preview[:200] + "…"
        lines.append(f"- [{status}] {title}：{preview}")
    lines.extend(["", "【建议】", "- 缺权限的步骤请 `feishu doctor` 补 scope。", "- 可说「任务模式 …」继续深度执行。"])
    return "\n".join(lines)


def run_workflow(
    workflow_id: str,
    *,
    goal: str = "",
    chat_id: str = "",
    run_id: str = "",
) -> str:
    workflows = load_workflows()
    wf = workflows.get((workflow_id or "").strip())
    if wf is None:
        return f"未知 Workflow：{workflow_id}。{list_workflows_text()}"
    cleaned_goal = (goal or wf.name).strip()
    trace_id = (run_id or chat_id or wf.id).strip()
    emit_trace(trace_id, "workflow.started", workflow_id=wf.id, goal=cleaned_goal)
    executed: list[dict[str, Any]] = []
    for index, step in enumerate(wf.steps, 1):
        if step.tool not in read_tools():
            row = {
                "index": index,
                "title": step.title,
                "tool": step.tool,
                "status": "failed",
                "error": f"工具不在 Workflow allowlist：{step.tool}",
            }
            executed.append(row)
            emit_trace(trace_id, "workflow.step", **row)
            continue
        args = _render_args(step.args, goal=cleaned_goal)
        if step.tool == "search" and not args.get("query"):
            args["query"] = _keyword(cleaned_goal)
        try:
            result = execute_tool(step.tool, args, confirmed=False)
            status = "done"
            error = ""
        except Exception as exc:  # noqa: BLE001 - surface to user
            result = ""
            status = "failed"
            error = str(exc)
        row = {
            "index": index,
            "title": step.title,
            "tool": step.tool,
            "status": status,
            "result": result,
            "error": error,
        }
        executed.append(row)
        emit_trace(trace_id, "workflow.step", workflow_id=wf.id, **row)
    if wf.finalize == "summarize":
        body = _finalize_summarize(goal=cleaned_goal, steps=executed)
    else:
        body = _finalize_summarize(goal=cleaned_goal, steps=executed)
    emit_trace(trace_id, "workflow.completed", workflow_id=wf.id, steps=len(executed))
    return body
