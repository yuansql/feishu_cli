"""Unified tool registry for TaskRunner, MCP, and Planner allowlists."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

Effect = Literal["read", "write", "internal"]
Confirmation = Literal["never", "required"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    effect: Effect
    confirmation: Confirmation
    expose_mcp: bool
    mcp_name: str | None = None
    mcp_action: str | None = None
    runner_only: bool = False


def _registry() -> dict[str, ToolSpec]:
    read_specs = (
        ("today", "今天的日程、待办和要跟的活", "today"),
        ("tomorrow", "明天的日程、待办和要跟的活", "tomorrow"),
        ("tasks", "未完成待办", "tasks"),
        ("search", "搜飞书文档", "search"),
        ("read", "读飞书文档（URL 或 token）", "read"),
        ("person", "某人最近怎么回的", "person"),
        ("chats", "会话/群列表", "chats"),
        ("inbox", "谁找我（机器人所在群）", "inbox"),
        ("minutes", "最近会议纪要", "minutes"),
        ("approval", "待办审批", "approval"),
        ("brief", "昨天小结 + 今天规划", "brief"),
    )
    out: dict[str, ToolSpec] = {}
    for name, desc, action in read_specs:
        mcp = f"feishu_{name}"
        out[name] = ToolSpec(
            name=name,
            description=desc,
            effect="read",
            confirmation="never",
            expose_mcp=True,
            mcp_name=mcp,
            mcp_action=action,
        )
    out["weekly"] = ToolSpec(
        name="weekly",
        description="本周周报/计划；下周传 focus=next",
        effect="read",
        confirmation="never",
        expose_mcp=True,
        mcp_name="feishu_weekly",
        mcp_action="weekly",
    )
    out["digest"] = ToolSpec(
        name="digest",
        description="今日待跟进（本地跟进账摘要）",
        effect="read",
        confirmation="never",
        expose_mcp=True,
        mcp_name="feishu_digest",
        mcp_action="digest",
    )
    out["day_recap"] = ToolSpec(
        name="day_recap",
        description="我今天干了什么：按当天消息证据回顾",
        effect="read",
        confirmation="never",
        expose_mcp=True,
        mcp_name="feishu_day_recap",
        mcp_action="today_recap",
    )
    out["weekly_tasks"] = ToolSpec(
        name="weekly_tasks",
        description="本周任务清单（多维表/本地）",
        effect="read",
        confirmation="never",
        expose_mcp=True,
        mcp_name="feishu_weekly_tasks",
        mcp_action="weekly_tasks",
    )
    out["memory"] = ToolSpec(
        name="memory",
        description="本地工作记忆/经验片段（只读）",
        effect="read",
        confirmation="never",
        expose_mcp=True,
        mcp_name="feishu_memory",
        mcp_action="memory",
    )
    out["knowledge"] = ToolSpec(
        name="knowledge",
        description="按本地知识/文档片段作答（只读；制度/规范问答）",
        effect="read",
        confirmation="never",
        expose_mcp=True,
        mcp_name="feishu_knowledge",
        mcp_action="knowledge",
    )
    out["identity"] = ToolSpec(
        name="identity",
        description="伙伴身份与飞书 CLI 授权状态（不含密钥）",
        effect="read",
        confirmation="never",
        expose_mcp=True,
        mcp_name="feishu_identity",
        mcp_action="identity",
    )
    out["help"] = ToolSpec(
        name="help",
        description="能力说明",
        effect="read",
        confirmation="never",
        expose_mcp=True,
        mcp_name="feishu_help",
        mcp_action="help",
    )
    out["followup_add"] = ToolSpec(
        name="followup_add",
        description="写入本地跟进账",
        effect="write",
        confirmation="required",
        expose_mcp=False,
    )
    out["task_create"] = ToolSpec(
        name="task_create",
        description="创建飞书待办",
        effect="write",
        confirmation="required",
        expose_mcp=False,
    )
    out["docs_create"] = ToolSpec(
        name="docs_create",
        description="新建云文档",
        effect="write",
        confirmation="required",
        expose_mcp=False,
    )
    out["sandbox_ls"] = ToolSpec(
        name="sandbox_ls",
        description="列出本地沙箱文件",
        effect="read",
        confirmation="never",
        expose_mcp=False,
    )
    out["sandbox_read"] = ToolSpec(
        name="sandbox_read",
        description="读取本地沙箱文件",
        effect="read",
        confirmation="never",
        expose_mcp=False,
    )
    out["sandbox_write"] = ToolSpec(
        name="sandbox_write",
        description="写入本地沙箱文件（不出沙箱）",
        effect="write",
        confirmation="never",
        expose_mcp=False,
    )
    out["sandbox_run"] = ToolSpec(
        name="sandbox_run",
        description="在沙箱内执行允许的命令（python3/ls/cat 等）",
        effect="write",
        confirmation="never",
        expose_mcp=False,
    )
    out["report_write"] = ToolSpec(
        name="report_write",
        description="生成本地 HTML 报告（~/.feishu-partner/reports）",
        effect="write",
        confirmation="never",
        expose_mcp=False,
    )
    out["summarize"] = ToolSpec(
        name="summarize",
        description="汇总任务材料（内部）",
        effect="internal",
        confirmation="never",
        expose_mcp=False,
        runner_only=True,
    )
    out["parity_probe"] = ToolSpec(
        name="parity_probe",
        description="本地能力探针；验收对标分数与 nonce",
        effect="read",
        confirmation="never",
        expose_mcp=True,
        mcp_name="feishu_parity_probe",
        mcp_action="parity_probe",
        runner_only=True,
    )
    return out


_REGISTRY = _registry()


def get_tool(name: str) -> ToolSpec | None:
    return _REGISTRY.get((name or "").strip())


def read_tools() -> frozenset[str]:
    return frozenset(k for k, v in _REGISTRY.items() if v.effect == "read" and not v.runner_only)


def write_tools() -> frozenset[str]:
    return frozenset(k for k, v in _REGISTRY.items() if v.effect == "write")


def confirm_tools() -> frozenset[str]:
    return frozenset(k for k, v in _REGISTRY.items() if v.confirmation == "required")


def internal_tools() -> frozenset[str]:
    return frozenset(k for k, v in _REGISTRY.items() if v.effect == "internal")


def all_runner_tools() -> frozenset[str]:
    return frozenset(_REGISTRY.keys())


def mcp_tools() -> list[tuple[str, str]]:
    return [
        (spec.mcp_name or spec.name, spec.description)
        for spec in _REGISTRY.values()
        if spec.expose_mcp and spec.mcp_name
    ]


def mcp_action(name: str) -> str | None:
    spec = _REGISTRY.get(name) or next(
        (s for s in _REGISTRY.values() if s.mcp_name == name),
        None,
    )
    if spec is None:
        return None
    return spec.mcp_action or spec.name


def mcp_tool_schema(mcp_name: str, description: str) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    if mcp_name == "feishu_weekly":
        props["focus"] = {"type": "string", "description": "next 表示下周"}
    elif mcp_name == "feishu_chats":
        props["query"] = {"type": "string", "description": "群名关键词"}
    elif mcp_name == "feishu_person":
        props["query"] = {"type": "string", "description": "人名"}
        required.append("query")
    elif mcp_name == "feishu_search":
        props["query"] = {"type": "string", "description": "搜索关键词"}
        required.append("query")
    elif mcp_name == "feishu_read":
        props["doc"] = {"type": "string", "description": "飞书文档 URL 或 token"}
        required.append("doc")
    elif mcp_name == "feishu_memory":
        props["query"] = {"type": "string", "description": "与当前目标相关的关键词，可空"}
    elif mcp_name == "feishu_knowledge":
        props["query"] = {"type": "string", "description": "制度/规范/知识问题"}
        required.append("query")
    elif mcp_name == "feishu_parity_probe":
        props["nonce"] = {"type": "string", "description": "验收短字符串，可留空"}
    return {
        "name": mcp_name,
        "description": description,
        "inputSchema": {"type": "object", "properties": props, "required": required},
    }


def execute_tool(
    tool: str,
    args: dict[str, str],
    *,
    confirmed: bool = False,
) -> str:
    name = (tool or "").strip()
    spec = get_tool(name)
    if spec is None:
        raise RuntimeError(f"unknown tool: {name}")
    if spec.effect == "internal" and name == "summarize":
        raise RuntimeError("summarize must go through _summarize_task")
    if spec.effect == "write":
        if spec.confirmation == "required" and not confirmed:
            raise RuntimeError(f"write tool {name} requires confirmation")
        return _execute_write(name, args)
    if spec.effect == "read" or (spec.effect == "internal" and name == "parity_probe"):
        if name == "parity_probe":
            return _parity_probe(args)
        return _execute_read(name, args)
    raise RuntimeError(f"tool not wired: {name}")


def _parity_probe(args: dict[str, str]) -> str:
    import json

    from ..ops.aily import alignment_score, alignment_target_score

    nonce = str(args.get("nonce") or "")[:100]
    return json.dumps(
        {
            "ok": True,
            "runtime": "feishu-partner",
            "local_score": alignment_score(),
            "local_target_score": alignment_target_score(),
            "nonce": nonce,
        },
        ensure_ascii=False,
    )


def _execute_read(name: str, args: dict[str, str]) -> str:
    from ..actions import (
        approval_text,
        brief_text,
        chats_text,
        inbox_text,
        minutes_text,
        person_text,
        read_text,
        search_text,
        tasks_text,
        today_text,
        tomorrow_text,
    )

    runners: dict[str, Callable[[dict[str, str]], str]] = {
        "today": lambda _a: today_text(),
        "tomorrow": lambda _a: tomorrow_text(),
        "tasks": lambda _a: tasks_text(),
        "search": lambda a: search_text(a.get("query", "")),
        "read": lambda a: read_text(a.get("doc", "")),
        "person": lambda a: person_text(a.get("query", "")),
        "chats": lambda a: chats_text(a.get("query", "")),
        "inbox": lambda _a: inbox_text(),
        "minutes": lambda _a: minutes_text(),
        "approval": lambda _a: approval_text(),
        "brief": lambda _a: brief_text(),
        "sandbox_ls": lambda a: _sandbox_ls(a),
        "sandbox_read": lambda a: _sandbox_read(a),
    }
    fn = runners.get(name)
    if fn is None:
        raise RuntimeError(f"tool not wired: {name}")
    return fn(args)


def _execute_write(name: str, args: dict[str, str]) -> str:
    if name == "followup_add":
        from ..office.followup import add_goal_item

        goal = args.get("goal") or args.get("summary") or args.get("query") or ""
        item = add_goal_item(goal, chat_id=args.get("chat_id") or "")
        return f"已写入跟进账：{item.get('text')}（{item.get('id')}）"
    if name == "task_create":
        from ..actions import create_task_item

        summary = args.get("summary") or args.get("goal") or ""
        return create_task_item(summary, due=args.get("due") or "")
    if name == "docs_create":
        from ..actions import write_doc_text

        query = args.get("query") or args.get("goal") or args.get("summary") or ""
        return write_doc_text(query)
    if name == "sandbox_write":
        from .sandbox import sandbox_write

        return sandbox_write(args.get("path") or args.get("file") or "", args.get("content") or "")
    if name == "sandbox_run":
        from .sandbox import sandbox_run

        return sandbox_run(args.get("command") or args.get("query") or "")
    if name == "report_write":
        from ..office.report import report_from_goal

        goal = args.get("goal") or args.get("query") or args.get("title") or "工作报告"
        materials = args.get("materials") or args.get("body") or args.get("content") or ""
        return report_from_goal(goal, materials)
    raise RuntimeError(f"unknown write tool: {name}")


def _sandbox_ls(args: dict[str, str]) -> str:
    from .sandbox import sandbox_ls

    return sandbox_ls(args.get("path") or ".")


def _sandbox_read(args: dict[str, str]) -> str:
    from .sandbox import sandbox_read

    rel = args.get("path") or args.get("file") or ""
    if not rel:
        return "请给出沙箱内相对路径。"
    return sandbox_read(rel)


def call_mcp_tool(mcp_name: str, arguments: dict[str, Any] | None) -> str:
    from ..routing.intents import Intent

    from ..actions import _facts_for as facts_for

    spec = next((s for s in _REGISTRY.values() if s.mcp_name == mcp_name), None)
    if spec is None:
        return f"未知工具：{mcp_name}"
    if mcp_name == "feishu_parity_probe":
        args = arguments or {}
        return _parity_probe({"nonce": str(args.get("nonce") or "")})
    action = spec.mcp_action
    if not action or action == "parity_probe":
        return f"未知工具：{mcp_name}"
    args = arguments or {}
    query = ""
    if action == "weekly" and str(args.get("focus") or "") == "next":
        query = "next"
    elif action == "chats":
        query = str(args.get("query") or "").strip()
    elif action == "person":
        query = str(args.get("query") or "").strip()
        if not query:
            return "person 需要人名"
    elif action == "search":
        query = str(args.get("query") or "").strip()
        if not query:
            return "search 需要 query"
    elif action == "read":
        query = str(args.get("doc") or "").strip()
        if not query:
            return "read 需要 doc"
    elif action == "memory":
        query = str(args.get("query") or "").strip()
    elif action == "knowledge":
        query = str(args.get("query") or "").strip()
        if not query:
            return "knowledge 需要 query"
    elif action == "today_recap":
        query = str(args.get("query") or "我今天干了什么").strip()
    elif action == "identity":
        query = ""
    return facts_for(Intent(action=action, query=query))
