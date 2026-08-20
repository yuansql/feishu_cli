"""Isolated local workspace: files + allowlisted commands. Not a cloud VM."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

MAX_OUTPUT = 8000
MAX_FILE = 256 * 1024
TIMEOUT_S = 20
ALLOWED_CMDS = frozenset({"python3", "python", "ls", "cat", "head", "wc", "uname"})


def sandbox_root() -> Path:
    override = os.environ.get("FEISHU_PARTNER_SANDBOX")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner" / "sandbox"


def resolve_path(rel: str) -> Path:
    root = sandbox_root().resolve()
    root.mkdir(parents=True, exist_ok=True)
    raw = (rel or ".").strip() or "."
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"路径越出沙箱：{rel}") from exc
    return candidate


def _safe_args(cmd: str, args: list[str]) -> list[str]:
    if cmd in {"python", "python3"}:
        if not args or args[0].startswith("-"):
            raise ValueError("python 只允许执行沙箱内脚本文件，例如：python3 hello.py")
        script = resolve_path(args[0])
        if not script.is_file():
            raise ValueError(f"脚本不存在：{args[0]}")
        extra: list[str] = []
        for arg in args[1:]:
            extra.append(_confine_arg(arg))
        return [str(script), *extra]
    return [_confine_arg(arg) for arg in args]


def _confine_arg(arg: str) -> str:
    if arg.startswith("-"):
        return arg
    if arg.startswith("/") or ".." in Path(arg).parts:
        raise ValueError(f"参数越出沙箱：{arg}")
    return arg


def sandbox_ls(rel: str = ".") -> str:
    root = sandbox_root().resolve()
    path = resolve_path(rel)
    if not path.exists():
        return f"沙箱里没有：{rel}"
    if path.is_file():
        return f"文件 {path.relative_to(root)}  {path.stat().st_size}B"
    names = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
    if not names:
        shown = path.relative_to(root)
        return f"空目录 {shown or '.'}"
    return "\n".join(names)


def sandbox_read(rel: str) -> str:
    path = resolve_path(rel)
    if not path.is_file():
        return f"不是文件：{rel}"
    data = path.read_bytes()[:MAX_FILE]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return f"二进制文件 {rel}，{len(data)} bytes"


def sandbox_write(rel: str, content: str) -> str:
    path = resolve_path(rel)
    if path.exists() and path.is_dir():
        return f"不能覆盖目录：{rel}"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = content or ""
    if len(text.encode("utf-8")) > MAX_FILE:
        return "内容超过 256KiB 上限。"
    path.write_text(text, encoding="utf-8")
    rel_shown = path.relative_to(sandbox_root().resolve())
    return f"已写入沙箱 {rel_shown}（{path.stat().st_size}B）"


def sandbox_run(command: str) -> str:
    parts = shlex.split((command or "").strip())
    if not parts:
        return "请给出命令，例如：python3 hello.py"
    cmd = Path(parts[0]).name
    if cmd not in ALLOWED_CMDS:
        allowed = ", ".join(sorted(ALLOWED_CMDS))
        return f"命令不在沙箱允许列表：{cmd}。允许：{allowed}"
    try:
        safe_args = _safe_args(cmd, parts[1:])
    except ValueError as exc:
        return str(exc)
    binary = shutil.which(cmd) or cmd
    argv = [binary, *safe_args]
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(sandbox_root()),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
    }
    try:
        proc = subprocess.run(
            argv,
            cwd=str(sandbox_root()),
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"超时（>{TIMEOUT_S}s），已终止。"
    except OSError as exc:
        return f"无法执行：{exc}"
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    out = out.strip()
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT] + "\n…(截断)"
    return f"exit {proc.returncode}\n{out}".rstrip()


def sandbox_status_text() -> str:
    root = sandbox_root()
    root.mkdir(parents=True, exist_ok=True)
    return (
        f"本地沙箱：{root}\n"
        f"允许命令：{', '.join(sorted(ALLOWED_CMDS))}\n"
        f"边界：不能访问沙箱外路径；无任意 shell；非云 VM。"
    )
