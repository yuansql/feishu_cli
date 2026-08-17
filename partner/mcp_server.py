"""Allowlisted Feishu facts over MCP stdio. No send, no shell, no write_weekly."""

from __future__ import annotations

import json
import sys
from typing import Any

from .intents import Intent

PROTOCOL = "2024-11-05"

_TOOLS = (
    ("feishu_today", "今天的日程"),
    ("feishu_tomorrow", "明天的日程"),
    ("feishu_tasks", "未完成待办"),
    ("feishu_weekly", "本周周报/计划；下周传 focus=next"),
    ("feishu_brief", "昨天小结 + 今天规划"),
    ("feishu_inbox", "谁找我（机器人所在群）"),
    ("feishu_minutes", "最近会议纪要"),
    ("feishu_approval", "待办审批"),
    ("feishu_chats", "会话/群列表；找某个群时传 query"),
    ("feishu_help", "能力说明"),
    ("feishu_search", "搜飞书文档"),
    ("feishu_read", "读飞书文档（URL 或 token）"),
)

_ACTION = {
    "feishu_today": "today",
    "feishu_tomorrow": "tomorrow",
    "feishu_tasks": "tasks",
    "feishu_weekly": "weekly",
    "feishu_brief": "brief",
    "feishu_inbox": "inbox",
    "feishu_minutes": "minutes",
    "feishu_approval": "approval",
    "feishu_chats": "chats",
    "feishu_help": "help",
    "feishu_search": "search",
    "feishu_read": "read",
}


def _tool_schema(name: str, description: str) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    if name == "feishu_weekly":
        props["focus"] = {"type": "string", "description": "next 表示下周"}
    elif name == "feishu_chats":
        props["query"] = {"type": "string", "description": "群名关键词，如 孙萌测试"}
    elif name == "feishu_search":
        props["query"] = {"type": "string", "description": "搜索关键词"}
        required.append("query")
    elif name == "feishu_read":
        props["doc"] = {"type": "string", "description": "飞书文档 URL 或 token"}
        required.append("doc")
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": props, "required": required},
    }


def _facts_for(intent: Intent) -> str:
    from .actions import _facts_for as facts_for

    return facts_for(intent)


def _call_tool(name: str, arguments: dict[str, Any] | None) -> str:
    action = _ACTION.get(name)
    if action is None:
        return f"未知工具：{name}"
    args = arguments or {}
    query = ""
    if action == "weekly" and str(args.get("focus") or "") == "next":
        query = "next"
    elif action == "chats":
        query = str(args.get("query") or "").strip()
    elif action == "search":
        query = str(args.get("query") or "").strip()
        if not query:
            return "search 需要 query"
    elif action == "read":
        query = str(args.get("doc") or "").strip()
        if not query:
            return "read 需要 doc"
    return _facts_for(Intent(action=action, query=query))


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    msg_id = message.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "feishu-partner", "version": "1"},
            },
        }
    if method == "notifications/initialized" or method == "initialized":
        return None
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": [_tool_schema(name, desc) for name, desc in _TOOLS]
            },
        }
    if method == "tools/call":
        params = message.get("params") or {}
        name = str(params.get("name") or "")
        text = _call_tool(name, params.get("arguments") or {})
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
