"""Local publish snapshots for Workflow / knowledge / terminology / 技能提示词. Diff + rollback."""

from __future__ import annotations

import difflib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..office.knowledge import config_path as knowledge_path
from ..office.memory import agents_md_path, experience_path
from ..office.rag import terminology_path
from ..runtime.workflow import _bundled_defaults_path, user_workflows_path

CN_TZ = timezone(timedelta(hours=8))
TRACKED = (
    "workflows.json",
    "knowledge.json",
    "terminology.json",
    "bundled-workflows.json",
    "Agents.md",
    "experience.jsonl",
)


def versions_dir() -> Path:
    override = os.environ.get("FEISHU_PARTNER_VERSIONS")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner" / "versions"


def _now_id() -> str:
    stamp = datetime.now(CN_TZ).strftime("%Y%m%d-%H%M%S")
    root = versions_dir()
    if not (root / stamp).exists():
        return stamp
    n = 2
    while (root / f"{stamp}-{n}").exists():
        n += 1
    return f"{stamp}-{n}"


def _copy_if_exists(src: Path, dest: Path) -> bool:
    if not src.is_file():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return True


def _live_sources() -> dict[str, Path]:
    return {
        "workflows.json": user_workflows_path(),
        "knowledge.json": knowledge_path(),
        "terminology.json": terminology_path(),
        "bundled-workflows.json": _bundled_defaults_path(),
        "Agents.md": agents_md_path(),
        "experience.jsonl": experience_path(),
    }


def publish(*, note: str = "") -> dict[str, Any]:
    vid = _now_id()
    root = versions_dir() / vid
    root.mkdir(parents=True, exist_ok=True)
    files: dict[str, bool] = {}
    for name, src in _live_sources().items():
        files[name] = _copy_if_exists(src, root / name)
    manifest = {
        "id": vid,
        "created_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "note": (note or "").strip()[:200],
        "files": files,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (versions_dir() / "current").write_text(vid, encoding="utf-8")
    return manifest


def list_versions() -> list[dict[str, Any]]:
    root = versions_dir()
    if not root.is_dir():
        return []
    current = ""
    cur_path = root / "current"
    if cur_path.is_file():
        current = cur_path.read_text(encoding="utf-8").strip()
    out: list[dict[str, Any]] = []
    for path in sorted(root.iterdir(), reverse=True):
        man = path / "manifest.json"
        if not man.is_file():
            continue
        try:
            blob = json.loads(man.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(blob, dict):
            blob["is_current"] = blob.get("id") == current
            out.append(blob)
    return out


def _snapshot_file(vid: str, name: str) -> Path:
    return versions_dir() / vid / name


def diff_versions(left: str, right: str) -> str:
    if not left or not right:
        return "用法：feishu versions diff <idA> <idB>"
    lines = [f"diff {left} → {right}", ""]
    any_file = False
    for name in TRACKED:
        a = _snapshot_file(left, name)
        b = _snapshot_file(right, name)
        text_a = a.read_text(encoding="utf-8") if a.is_file() else ""
        text_b = b.read_text(encoding="utf-8") if b.is_file() else ""
        if text_a == text_b:
            continue
        any_file = True
        hunk = difflib.unified_diff(
            text_a.splitlines(),
            text_b.splitlines(),
            fromfile=f"{left}/{name}",
            tofile=f"{right}/{name}",
            lineterm="",
        )
        lines.extend(hunk)
        lines.append("")
    if not any_file:
        lines.append("无差异（或快照文件缺失）。")
    return "\n".join(lines).rstrip()


def rollback(vid: str) -> str:
    snap = versions_dir() / (vid or "").strip()
    man = snap / "manifest.json"
    if not man.is_file():
        return f"找不到版本：{vid}"
    restored: list[str] = []
    mapping = {
        "workflows.json": user_workflows_path(),
        "knowledge.json": knowledge_path(),
        "terminology.json": terminology_path(),
        "Agents.md": agents_md_path(),
        "experience.jsonl": experience_path(),
    }
    for name, dest in mapping.items():
        src = snap / name
        if not src.is_file():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        restored.append(str(dest))
    (versions_dir() / "current").write_text(vid.strip(), encoding="utf-8")
    if not restored:
        return f"版本 {vid} 没有可回滚的用户配置（可能当时只有内置 Workflow）。"
    return "已回滚：\n" + "\n".join(f"- {p}" for p in restored)


def versions_text(argv: list[str] | None = None) -> str:
    args = [a for a in (argv or []) if a]
    if not args or args[0] in {"list", "ls"}:
        rows = list_versions()
        if not rows:
            return "还没有版本快照。先运行：feishu versions publish"
        lines = ["本地配置版本："]
        for row in rows[:20]:
            mark = " *" if row.get("is_current") else ""
            note = str(row.get("note") or "")
            extra = f"  {note}" if note else ""
            lines.append(f"- {row.get('id')}{mark}{extra}")
        return "\n".join(lines)
    cmd = args[0]
    if cmd == "publish":
        note = " ".join(args[1:]).strip()
        man = publish(note=note)
        copied = [name for name, ok in (man.get("files") or {}).items() if ok]
        return f"已发布 {man['id']}\n文件：{', '.join(copied) or '无'}"
    if cmd == "diff":
        if len(args) < 3:
            return "用法：feishu versions diff <idA> <idB>"
        return diff_versions(args[1], args[2])
    if cmd == "rollback":
        if len(args) < 2:
            return "用法：feishu versions rollback <id>"
        return rollback(args[1])
    return "用法：feishu versions list|publish|diff|rollback"
