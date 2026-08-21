"""Local Agents.md + experience archive for cross-session reuse (no cloud)."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CN_TZ = timezone(timedelta(hours=8))

_DEFAULT_AGENTS = """# Agents.md · 飞书工作伙伴本地经验

> 本文件在 `~/.feishu-partner/Agents.md`，**不要提交 git**。
> 任务规划时会摘录相关段落；可用 `feishu memory note …` 追加短经验。

## 偏好

- 写操作必须先确认（「确认写入」）
- 未知句默认不搜文档；要搜说「搜」
- 缺权限明示，不假装成功

## 常用术语

- （在此写团队缩写、项目代号）

## 坑与教训

- （在此写踩过的权限/群/文档坑）
"""


def memory_root() -> Path:
    override = os.environ.get("FEISHU_PARTNER_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner"


def agents_md_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_AGENTS_MD")
    if override:
        return Path(override).expanduser()
    return memory_root() / "Agents.md"


def experience_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_EXPERIENCE")
    if override:
        return Path(override).expanduser()
    return memory_root() / "experience.jsonl"


def ensure_agents_md() -> Path:
    path = agents_md_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text(_DEFAULT_AGENTS, encoding="utf-8")
    return path


def read_agents_md() -> str:
    path = ensure_agents_md()
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def append_experience(
    text: str,
    *,
    tags: list[str] | None = None,
    source: str = "note",
) -> dict[str, Any]:
    body = (text or "").strip()
    if not body:
        raise ValueError("经验内容不能为空")
    path = experience_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "source": (source or "note").strip() or "note",
        "text": body[:2000],
        "tags": [str(t).strip() for t in (tags or []) if str(t).strip()][:8],
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def recent_experience(*, limit: int = 20) -> list[dict[str, Any]]:
    path = experience_path()
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                blob = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(blob, dict) and blob.get("text"):
                rows.append(blob)
    except OSError:
        return []
    return rows[-max(1, limit) :]


def note_task_outcome(goal: str, status: str, summary: str = "") -> None:
    cleaned = (goal or "").strip()
    if not cleaned:
        return
    bits = [f"任务「{cleaned[:120]}」→ {status or 'done'}"]
    extra = (summary or "").strip().replace("\n", " ")
    if extra:
        bits.append(extra[:240])
    try:
        append_experience("；".join(bits), source="task", tags=["task", status or "done"])
    except ValueError:
        return


def _tokens(text: str) -> set[str]:
    parts = re.findall(r"[\w\u4e00-\u9fff]{2,}", (text or "").lower())
    return {p for p in parts if p not in {"任务", "模式", "规划", "今天", "明天", "飞书"}}


def memory_context_for_goal(goal: str, *, max_chars: int = 1800) -> str:
    """Return Agents.md + matching experience snippets for planner facts."""
    ensure_agents_md()
    goal_tokens = _tokens(goal)
    sections: list[str] = []
    agents = read_agents_md().strip()
    if agents:
        # Prefer sections whose heading or body share tokens with the goal.
        chunks = re.split(r"(?=^## )", agents, flags=re.M)
        picked: list[str] = []
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            if not goal_tokens or _tokens(chunk) & goal_tokens or chunk.startswith("# "):
                picked.append(chunk)
        body = "\n\n".join(picked[:4]) if picked else agents[:800]
        sections.append("【本地 Agents.md】\n" + body[:1200])

    matches: list[str] = []
    for row in reversed(recent_experience(limit=40)):
        text = str(row.get("text") or "")
        if goal_tokens and not (_tokens(text) & goal_tokens):
            continue
        matches.append(f"- {text[:200]}")
        if len(matches) >= 5:
            break
    if not matches:
        for row in reversed(recent_experience(limit=3)):
            matches.append(f"- {str(row.get('text') or '')[:200]}")
    if matches:
        sections.append("【近期经验】\n" + "\n".join(matches))
    joined = "\n\n".join(sections).strip()
    if len(joined) > max_chars:
        return joined[:max_chars] + "\n…(截断)"
    return joined


def memory_cli(argv: list[str] | None = None) -> str:
    args = list(argv or [])
    if not args or args[0] in {"show", "status"}:
        path = ensure_agents_md()
        exp = recent_experience(limit=5)
        lines = [
            "本地记忆",
            f"- Agents.md：{path}",
            f"- 经验条数：{len(recent_experience(limit=500))}（最近 {len(exp)} 条预览）",
            "",
        ]
        if exp:
            lines.append("最近经验：")
            for row in reversed(exp):
                lines.append(f"- [{row.get('ts', '')}] {row.get('text', '')[:120]}")
        else:
            lines.append("还没有经验条目。可用：`feishu memory note 一句话教训`")
        return "\n".join(lines)
    if args[0] == "init":
        path = ensure_agents_md()
        return f"已确保 Agents.md 存在：{path}"
    if args[0] == "note":
        text = " ".join(args[1:]).strip()
        if not text:
            return "用法：feishu memory note 一句话经验"
        row = append_experience(text, source="cli")
        return f"已写入经验：{row['ts']}\n{row['text']}"
    if args[0] == "context":
        goal = " ".join(args[1:]).strip() or "日常"
        return memory_context_for_goal(goal) or "（暂无可用记忆）"
    return (
        "用法：\n"
        "  feishu memory          # 状态\n"
        "  feishu memory init     # 生成 Agents.md\n"
        "  feishu memory note …  # 追加经验\n"
        "  feishu memory context 关键词"
    )
