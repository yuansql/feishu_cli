"""Local eval: fixture routing cases + trace health + 人工评分. No Aily console."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..routing.intents import parse_intent
from ..routing.mode_router import route_request
from ..routing.resolved import looks_like_resolve
from ..core.trace import traces_dir

CN_TZ = timezone(timedelta(hours=8))


def cases_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_EVAL_CASES")
    if override:
        return Path(override).expanduser()
    from ..paths import PACKAGE_DIR

    return PACKAGE_DIR / "eval_cases.json"


def user_cases_path() -> Path:
    """traces 一键转用例的落点：用户级用例文件，与仓库内置 fixtures 合并评测。"""
    override = os.environ.get("FEISHU_PARTNER_EVAL_USER_CASES")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner" / "eval-cases.json"


def scores_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_EVAL_SCORES")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner" / "eval-scores.json"


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = blob.get("cases") if isinstance(blob, dict) else blob
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def load_cases() -> list[dict[str, Any]]:
    """内置 fixtures + 用户用例（traces 转来的）合并。"""
    return _load_rows(cases_path()) + _load_rows(user_cases_path())


# ---------------------------------------------------------------- traces → 用例


def harvest_cases_from_traces(*, limit_files: int = 50) -> dict[str, Any]:
    """扫 traces/*.jsonl，把 workflow.started 的 goal 去重后转成路由用例。

    新用例带 observed_action（当前路由结果）+ 空 expect_action，人工确认后
    把 expect_action 填上即成为回归用例。不覆盖已有 text 相同的用例。
    """
    root = traces_dir()
    summary = {"scanned": 0, "goals": 0, "added": 0, "skipped_dup": 0, "path": ""}
    if not root.is_dir():
        return summary
    files = sorted(root.glob("*.jsonl"))[-max(1, limit_files) :]
    summary["scanned"] = len(files)
    goals: list[tuple[str, str]] = []  # (task_id, goal)
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
            if str(row.get("event") or "") != "workflow.started":
                continue
            goal = str(row.get("goal") or "").strip()
            if len(goal) >= 4:
                goals.append((path.stem, goal))
    summary["goals"] = len(goals)
    if not goals:
        return summary
    existing = load_cases()
    known_texts = {str(c.get("text") or "").strip() for c in existing}
    user_rows = _load_rows(user_cases_path())
    added = 0
    for task_id, goal in goals:
        if goal in known_texts:
            summary["skipped_dup"] += 1
            continue
        known_texts.add(goal)
        observed = parse_intent(goal).action
        user_rows.append(
            {
                "id": f"trace-{task_id}-{added + 1}",
                "text": goal,
                "expect_action": "",
                "observed_action": observed,
                "source": "trace",
            }
        )
        added += 1
    summary["added"] = added
    if added:
        path = user_cases_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"cases": user_rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary["path"] = str(path)
    return summary


# ---------------------------------------------------------------- 人工评分


def load_scores() -> dict[str, dict[str, Any]]:
    path = scores_path()
    if not path.is_file():
        return {}
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in blob.items() if isinstance(v, dict)} if isinstance(blob, dict) else {}


def score_case(case_id: str, score: str, note: str = "") -> str:
    cid = (case_id or "").strip()
    mark = (score or "").strip().lower()
    if not cid:
        return "用法：feishu eval score <用例id> good|bad [备注]"
    if mark not in {"good", "bad"}:
        return "评分只接受 good / bad。"
    known = {str(c.get("id") or "") for c in load_cases()}
    if cid not in known:
        return f"找不到用例：{cid}（先 feishu eval 看用例 id）"
    scores = load_scores()
    scores[cid] = {
        "score": mark,
        "note": (note or "").strip()[:200],
        "ts": datetime.now(CN_TZ).isoformat(timespec="seconds"),
    }
    path = scores_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"已评分 {cid} = {mark}" + (f"（{note.strip()}）" if note.strip() else "")


# ---------------------------------------------------------------- 评测主流程


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


def _has_expectation(case: dict[str, Any]) -> bool:
    return bool(
        case.get("expect_action") or case.get("expect_action_not") or case.get("expect_mode")
    )


def run_fixture_eval() -> dict[str, Any]:
    cases = load_cases()
    results: list[dict[str, Any]] = []
    passed = 0
    reviewed = 0
    for case in cases:
        if not _has_expectation(case):
            # traces 转来但还没人工填 expect 的用例：不计入通过率
            results.append(
                {
                    "id": str(case.get("id") or ""),
                    "ok": None,
                    "detail": f"待人工确认（当前路由：{parse_intent(str(case.get('text') or '')).action}）",
                    "text": str(case.get("text") or "")[:80],
                }
            )
            continue
        reviewed += 1
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
    total = reviewed
    rate = round(100.0 * passed / total, 1) if total else 0.0
    pending = len(cases) - reviewed
    return {
        "passed": passed,
        "total": total,
        "pending": pending,
        "rate": rate,
        "results": results,
    }


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


def eval_text(argv: list[str] | None = None) -> str:
    args = [a for a in (argv or []) if a]
    if args and args[0] == "harvest":
        summary = harvest_cases_from_traces()
        lines = [
            f"扫描 trace 文件 {summary['scanned']} 个，提取 goal {summary['goals']} 条。",
            f"新增用例 {summary['added']} 条（跳过重复 {summary['skipped_dup']}）。",
        ]
        if summary["added"]:
            lines.append(f"已写入：{summary['path']}")
            lines.append("这些用例 expect_action 为空，人工确认后填上即纳入回归。")
        return "\n".join(lines)
    if args and args[0] == "score":
        if len(args) < 3:
            return "用法：feishu eval score <用例id> good|bad [备注]"
        return score_case(args[1], args[2], " ".join(args[3:]))
    fixtures = run_fixture_eval()
    traces = run_trace_eval()
    lines = [
        "本地评测（不对接 Aily）",
        "",
        f"【路由 fixtures】{fixtures['passed']}/{fixtures['total']} 通过（{fixtures['rate']}%）",
    ]
    if fixtures["pending"]:
        lines.append(f"另有 {fixtures['pending']} 条 traces 转来的用例待人工确认 expect。")
    fails = [row for row in fixtures["results"] if row["ok"] is False]
    if fails:
        lines.append("失败：")
        for row in fails[:20]:
            lines.append(f"- {row['id']}: {row['detail']}  «{row['text']}»")
    else:
        lines.append("全部高风险路由样例通过。")
    pending_rows = [row for row in fixtures["results"] if row["ok"] is None]
    if pending_rows:
        lines.append("待确认：")
        for row in pending_rows[:10]:
            lines.append(f"- {row['id']}: {row['detail']}  «{row['text']}»")
    scores = load_scores()
    if scores:
        good = sum(1 for s in scores.values() if s.get("score") == "good")
        bad = sum(1 for s in scores.values() if s.get("score") == "bad")
        lines.extend(["", f"【人工评分】good {good} · bad {bad}"])
        bad_rows = [(cid, s) for cid, s in scores.items() if s.get("score") == "bad"]
        for cid, s in bad_rows[:10]:
            note = str(s.get("note") or "")
            extra = f"（{note}）" if note else ""
            lines.append(f"- bad {cid} {extra}")
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
            "说明：这是本地 CLI 评测，不是 Aily 调优台。",
            "扩样例：feishu eval harvest（traces 转用例）；评分：feishu eval score <id> good|bad。",
        ]
    )
    return "\n".join(lines)
