from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


WORKBUDDY_LARK = (
    Path.home()
    / ".workbuddy/binaries/node/cli-connector-packages/bin/lark-cli"
)


class LarkError(RuntimeError):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(payload.get("error", {}).get("message") or "lark-cli failed")
        self.payload = payload


def find_lark_cli() -> Path:
    override = os.environ.get("LARK_CLI")
    if override:
        p = Path(override).expanduser()
        if p.exists():
            return p
    which = shutil.which("lark-cli")
    if which:
        return Path(which)
    if WORKBUDDY_LARK.exists():
        return WORKBUDDY_LARK
    local = Path.home() / ".local/bin/lark-cli"
    if local.exists():
        return local
    raise FileNotFoundError(
        "找不到 lark-cli。请确认 WorkBuddy 已装 @larksuite/cli，或设置 LARK_CLI。"
    )


def _loads_json_blob(text: str) -> dict[str, Any] | None:
    blob = (text or "").strip()
    if not blob:
        return None
    start = blob.find("{")
    if start < 0:
        return None
    try:
        loaded = json.loads(blob[start:])
    except json.JSONDecodeError:
        return None
    if isinstance(loaded, dict):
        return loaded
    return {"ok": True, "data": loaded}


def parse_cli_output(stdout: str, stderr: str, returncode: int) -> dict[str, Any]:
    """Turn lark-cli stdout/stderr into one JSON payload. Errors often land on stderr."""
    payload = _loads_json_blob(stdout) or _loads_json_blob(stderr)
    if payload is None:
        payload = {
            "ok": returncode == 0,
            "raw": (stdout or "")[:2000],
            "stderr": (stderr or "")[:1500],
        }
        if returncode != 0:
            payload["error"] = {
                "message": (stderr or stdout or "lark-cli non-zero").strip()[:1500]
            }
        return payload
    if returncode != 0:
        payload["ok"] = False
        if "error" not in payload:
            payload["error"] = {
                "message": (stderr or stdout or "lark-cli non-zero").strip()[:1500]
            }
    return payload


def run_lark(
    args: list[str],
    *,
    as_identity: str | None = "user",
    timeout: int = 60,
    input_text: str | None = None,
) -> dict[str, Any]:
    binary = find_lark_cli()
    cmd = [str(binary), *args]
    meta = args[0] if args else ""
    if as_identity and meta not in {"doctor", "auth", "config", "update"}:
        cmd.extend(["--as", as_identity])
    skip_format = {"whoami", "doctor", "auth", "config", "update", "help", "event"}
    if meta not in skip_format and "--format" not in cmd and "--json" not in cmd:
        cmd.extend(["--format", "json"])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            input=input_text,
        )
    except subprocess.TimeoutExpired:
        # lark-cli 偶发超时（如 base +record-list 大表扫描）绝不能搞挂调用方
        # （serve 主循环就是被未捕获的 TimeoutExpired 打死的）。
        return {
            "ok": False,
            "error": {"message": f"lark-cli timeout after {timeout}s: {meta}"},
        }
    except OSError as exc:
        return {"ok": False, "error": {"message": f"lark-cli spawn failed: {exc}"}}
    return parse_cli_output(proc.stdout or "", proc.stderr or "", proc.returncode)
