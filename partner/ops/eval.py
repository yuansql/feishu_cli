"""Local eval: fixture routing cases + trace health. No Aily console."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..routing.intents import parse_intent
from ..routing.mode_router import route_request
from ..routing.resolved import looks_like_resolve
from ..core.trace import traces_dir


def cases_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_EVAL_CASES")
    if override:
        return Path(override).expanduser()
    from ..paths import PACKAGE_DIR

    return PACKAGE_DIR / "eval_cases.json"


def load_cases() -> list[dict[str, Any]]:
    path = cases_path()
    if not path.is_file():
        return []
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = blob.get("cases") if isinstance(blob, dict) else blob
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _check_case(case: dict[str, Any]) -> tuple[bool, str]:
    text = str(case.get("text") or "")
    intent = parse_intent(text)
    if looks_like_resolve(text) and case.get("expect_action") == "task_confirm":
        return False, "resolve swallowed confirm"
    expected = case.get("expect_action")
    if expected and intent.action != expected:
        return False, f"action={intent.action} want {expected}"
    banned = case.get("expect_action_not")
    if banned and intent.action == banned:
        return False, f"action should not be {banned}"
    mode = case.get("expect_mode")
    if mode:
        decision = route_request(text, intent)
        if decision.mode != mode:
            return False, f"mode={decision.mode} want {mode}"
    return True, "ok"


def run_fixture_eval() -> dict[str, Any]:
    cases = load_cases()
    results: list[dict[str, Any]] = []
    passed = 0
    for case in cases:
        ok, detail = _check_case(case)
        if ok:
            passed += 1
        results.append(
            {
                "id": str(case.get("id") or ""),
                "ok": ok,
                "detail": detail,
                "text": str(case.get("text") or "")[:80],
            }
        )
    total = len(cases)
    rate = round(100.0 * passed / total, 1) if total else 0.0
    return {"passed": passed, "total": total, "rate": rate, "results": results}


def run_trace_eval(*, limit_files: int = 50) -> dict[str, Any]:
    root = traces_dir()
    if not root.is_dir():
        return {
            "files": 0,
            "events": 0,
            "completed": 0,
            "failed": 0,
            "note": "no traces directory",
        }
    files = sorted(root.glob("*.jsonl"))[-max(1, limit_files) :]
    events = 0
    completed = 0
    failed = 0
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            events += 1
            name = str(row.get("event") or "")
            if name == "task.completed":
                completed += 1
            if name in {"task.failed", "step.failed"}:
                failed += 1
    return {
        "files": len(files),
        "events": events,
        "completed": completed,
        "failed": failed,
        "note": "",
    }


def eval_text() -> str:
    fixtures = run_fixture_eval()
    traces = run_trace_eval()
    lines = [
        "本地评测（不对接 Aily）",
        "",
        f"【路由 fixtures】{fixtures['passed']}/{fixtures['total']} 通过（{fixtures['rate']}%）",
    ]
    fails = [row for row in fixtures["results"] if not row["ok"]]
    if fails:
        lines.append("失败：")
        for row in fails[:20]:
            lines.append(f"- {row['id']}: {row['detail']}  «{row['text']}»")
    else:
        lines.append("全部高风险路由样例通过。")
    lines.extend(
        [
            "",
            "【运行轨迹】",
            f"- 文件 {traces['files']} · 事件 {traces['events']}",
            f"- 完成 {traces['completed']} · 失败事件 {traces['failed']}",
        ]
    )
    if traces.get("note"):
        lines.append(f"- {traces['note']}")
    lines.extend(
        [
            "",
            "说明：这是本地 CLI 评测，不是 Aily 调优台。扩样例改 partner/eval_cases.json。",
        ]
    )
    return "\n".join(lines)
