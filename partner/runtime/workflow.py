"""Configurable Workflow DSL and executor (JSON)."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .planner import _keyword
from .tool_registry import execute_tool, read_tools
from ..core.trace import emit_trace


@dataclass(frozen=True)
class WorkflowStep:
    title: str
    tool: str
    args: dict[str, str]
    when: str = ""
    unless: str = ""
    repeat: int = 1
    until: str = ""


@dataclass(frozen=True)
class Workflow:
    id: str
    name: str
    description: str
    triggers: tuple[str, ...]
    steps: tuple[WorkflowStep, ...]
    finalize: str


def _bundled_defaults_path() -> Path:
    from ..paths import WORKFLOWS_DIR

    return WORKFLOWS_DIR / "default.json"


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
    try:
        repeat = int(raw.get("repeat") or 1)
    except (TypeError, ValueError):
        repeat = 1
    return WorkflowStep(
        title=str(raw.get("title") or tool).strip()[:160],
        tool=tool,
        args=args,
        when=str(raw.get("when") or "").strip(),
        unless=str(raw.get("unless") or "").strip(),
        repeat=max(1, min(repeat, 8)),
        until=str(raw.get("until") or "").strip(),
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


def _haystack(goal: str, executed: list[dict[str, Any]]) -> str:
    parts = [goal or ""]
    for row in executed:
        parts.append(str(row.get("result") or ""))
        parts.append(str(row.get("error") or ""))
    return "\n".join(parts)


def _pattern_hit(pattern: str, text: str) -> bool:
    raw = (pattern or "").strip()
    if not raw:
        return False
    blob = text or ""
    for piece in re.split(r"[|｜]", raw):
        piece = piece.strip()
        if piece and piece in blob:
            return True
    return False


def _http_step(args: dict[str, str]) -> str:
    url = (args.get("url") or args.get("query") or "").strip()
    if not url:
        raise ValueError("http 步骤需要 args.url")
    lowered = url.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        raise ValueError("http 只允许 http/https")
    method = (args.get("method") or "GET").strip().upper() or "GET"
    if method not in {"GET", "POST", "HEAD"}:
        raise ValueError(f"http 方法不允许：{method}")
    body = (args.get("body") or args.get("content") or "").encode("utf-8")
    req = Request(url, data=body if method == "POST" else None, method=method)
    req.add_header("User-Agent", "feishu-partner-workflow")
    if method == "POST" and body:
        req.add_header("Content-Type", args.get("content_type") or "text/plain; charset=utf-8")
    try:
        with urlopen(req, timeout=8) as resp:
            raw = resp.read(65536)
            status = getattr(resp, "status", None) or resp.getcode()
    except HTTPError as exc:
        return f"HTTP {exc.code} {url}\n{(exc.read(2000) or b'').decode('utf-8', 'replace')}"
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"http 失败：{exc}") from exc
    text = raw.decode("utf-8", "replace")
    return f"HTTP {status} {url}\n{text}"


def _execute_step(step: WorkflowStep, args: dict[str, str]) -> str:
    if step.tool == "http":
        return _http_step(args)
    return execute_tool(step.tool, args, confirmed=False)


def _should_run_step(step: WorkflowStep, *, goal: str, executed: list[dict[str, Any]]) -> bool:
    hay = _haystack(goal, executed)
    if step.when and not _pattern_hit(step.when, hay):
        return False
    if step.unless and _pattern_hit(step.unless, hay):
        return False
    return True


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
        if not _should_run_step(step, goal=cleaned_goal, executed=executed):
            row = {
                "index": index,
                "title": step.title,
                "tool": step.tool,
                "status": "skipped",
                "result": f"条件未满足（when={step.when or '-'} unless={step.unless or '-'}）",
                "error": "",
            }
            executed.append(row)
            emit_trace(trace_id, "workflow.step", workflow_id=wf.id, **row)
            continue
        if step.tool != "http" and step.tool not in read_tools():
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
        chunks: list[str] = []
        status = "done"
        error = ""
        for _attempt in range(step.repeat):
            try:
                chunk = _execute_step(step, args)
                chunks.append(chunk)
                if step.until and _pattern_hit(step.until, chunk):
                    break
            except Exception as exc:  # noqa: BLE001 - surface to user
                status = "failed"
                error = str(exc)
                break
        result = "\n".join(chunks)
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
