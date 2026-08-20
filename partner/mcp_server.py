"""Allowlisted Feishu facts over MCP stdio. No send, no shell, no write_weekly."""

from __future__ import annotations

import json
import sys
from typing import Any

from .tool_registry import call_mcp_tool, mcp_tool_schema, mcp_tools

PROTOCOL = "2025-03-26"
SUPPORTED_PROTOCOLS = frozenset({"2024-11-05", "2025-03-26", "2025-06-18"})


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    msg_id = message.get("id")
    if method == "initialize":
        params = message.get("params")
        requested = str(params.get("protocolVersion") or "") if isinstance(params, dict) else ""
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": requested if requested in SUPPORTED_PROTOCOLS else PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "feishu-partner", "version": "2"},
            },
        }
    if method == "notifications/initialized" or method == "initialized":
        return None
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": [mcp_tool_schema(name, desc) for name, desc in mcp_tools()]
            },
        }
    if method == "tools/call":
        params = message.get("params") or {}
        name = str(params.get("name") or "")
        text = call_mcp_tool(name, params.get("arguments") or {})
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"content": [{"type": "text", "text": text}]},
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if msg_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def _read_message(buf) -> dict[str, Any] | None:
    # Hermes MCP client speaks newline-delimited JSON, not LSP Content-Length.
    while True:
        line = buf.readline()
        if not line:
            return None
        stripped = line.strip()
        if stripped and stripped.startswith(b"{"):
            return json.loads(stripped.decode("utf-8"))


def _write_message(payload: dict[str, Any]) -> None:
    data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def serve_stdio() -> int:
    print("feishu-mcp: listening", file=sys.stderr, flush=True)
    buf = sys.stdin.buffer
    while True:
        try:
            message = _read_message(buf)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"feishu-mcp: read-error {exc}", file=sys.stderr, flush=True)
            return 1
        if message is None:
            return 0
        print(f"feishu-mcp: {message.get('method')}", file=sys.stderr, flush=True)
        reply = handle(message)
        if reply is not None:
            _write_message(reply)
