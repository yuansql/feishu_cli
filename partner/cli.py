from __future__ import annotations

import argparse
import subprocess
import sys

from .actions import (
    chats_text,
    dispatch,
    doctor_text,
    read_text,
    search_text,
    send_text,
    status_text,
    tasks_text,
    today_text,
    weekly_text,
)
from .brief import brief_text, push_brief
from .followup import digest_text, push_digest
from .ids import P2P_CHAT_ID
from .schedule import install_schedule, install_serve
from .formatters import help_text
from .intents import parse_intent
from .lark import find_lark_cli
from .serve import serve as serve_loop

PARTNER_CMDS = {
    "status",
    "doctor",
    "today",
    "tasks",
    "search",
    "read",
    "chats",
    "send",
    "serve",
    "help",
    "ask",
    "weekly",
    "brief",
    "mcp",
    "mcp-http",
    "followup",
    "digest",
    "aily",
    "align",
    "plan",
    "agent-worker",
    "webhook",
    "workflow",
    "rag",
    "sandbox",
    "eval",
    "versions",
    "setup",
    "report",
    "memory",
}


def _passthrough(argv: list[str]) -> int:
    binary = find_lark_cli()
    return subprocess.call([str(binary), *argv])


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        print(help_text())
        print("CLI：feishu setup|status|doctor|today|tasks|search|read|chats|send|serve|brief|followup|report|aily|plan")
        print("其余参数原样交给 lark-cli，例如：feishu calendar +agenda")
        return 0
    head = argv[0]
    if head == "mcp":
        from .mcp_server import serve_stdio

        return serve_stdio()
    if head not in PARTNER_CMDS:
        return _passthrough(argv)

    parser = argparse.ArgumentParser(prog="feishu", add_help=False)
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("status")
    sub.add_parser("doctor")
    sub.add_parser("today")
    sub.add_parser("tasks")
    sub.add_parser("weekly")
    sub.add_parser("chats")
    sub.add_parser("help")
    sub.add_parser("aily")
    sub.add_parser("align")

    p_setup = sub.add_parser("setup")
    p_setup.add_argument("--name", default="")
    p_setup.add_argument("--weekly-query", default="")
    p_setup.add_argument("--force", action="store_true")

    p_report = sub.add_parser("report")
    p_report.add_argument("title", nargs="*")

    p_search = sub.add_parser("search")
    p_search.add_argument("query", nargs="+")

    p_read = sub.add_parser("read")
    p_read.add_argument("doc")

    p_send = sub.add_parser("send")
    p_send.add_argument("chat_id")
    p_send.add_argument("text", nargs="+")

    p_ask = sub.add_parser("ask")
    p_ask.add_argument("text", nargs="+")

    p_plan = sub.add_parser("plan")
    p_plan.add_argument("goal", nargs="*")

    p_worker = sub.add_parser("agent-worker")
    p_worker.add_argument("--drain", action="store_true")

    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--timeout", default=None)
    p_serve.add_argument("--max-events", type=int, default=0)
    p_serve.add_argument("--install", action="store_true")

    p_brief = sub.add_parser("brief")
    p_brief.add_argument("--push", action="store_true")
    p_brief.add_argument("--force", action="store_true")
    p_brief.add_argument("--install", action="store_true")

    p_fu = sub.add_parser("followup")
    p_fu.add_argument("--setup-tables", action="store_true")
    p_fu.add_argument("--digest-once", action="store_true")
    p_fu.add_argument("--weekly-once", action="store_true")
    p_fu.add_argument("--scan-bitable", action="store_true")
    p_fu.add_argument("--scan-chats", action="store_true")
    p_fu.add_argument("--install", action="store_true")

    p_digest = sub.add_parser("digest")
    p_digest.add_argument("--push", action="store_true")
    p_digest.add_argument("--force", action="store_true")

    sub.add_parser("mcp")
    p_mcp_http = sub.add_parser("mcp-http")
    p_mcp_http.add_argument("--host", default="127.0.0.1")
    p_mcp_http.add_argument("--port", type=int, default=8765)

    p_webhook = sub.add_parser("webhook")
    p_webhook.add_argument("--host", default="127.0.0.1")
    p_webhook.add_argument("--port", type=int, default=8766)

    sub.add_parser("workflow")

    p_rag = sub.add_parser("rag")
    p_rag.add_argument("rag_cmd", nargs="?", choices=["index", "query", "stats"])
    p_rag.add_argument("rag_args", nargs="*", default=[])

    sub.add_parser("sandbox")
    sub.add_parser("eval")

    p_versions = sub.add_parser("versions")
    p_versions.add_argument("versions_args", nargs="*", default=[])

    p_memory = sub.add_parser("memory")
    p_memory.add_argument("memory_args", nargs="*", default=[])

    args = parser.parse_args(argv)
    if args.cmd == "status":
        print(status_text())
        return 0
    if args.cmd == "doctor":
        print(doctor_text())
        return 0
    if args.cmd == "today":
        print(today_text())
        return 0
    if args.cmd == "weekly":
        print(weekly_text())
        return 0
    if args.cmd == "tasks":
        print(tasks_text())
        return 0
    if args.cmd == "chats":
        print(chats_text())
        return 0
    if args.cmd == "help":
        print(help_text())
        return 0
    if args.cmd == "setup":
        from .setup import setup_text

        print(
            setup_text(
                name=args.name,
                weekly_query=args.weekly_query,
                force=args.force,
            )
        )
        return 0
    if args.cmd == "report":
        from .report import report_cli

        print(report_cli(list(args.title or [])))
        return 0
    if args.cmd in {"aily", "align"}:
        print(dispatch(parse_intent("aily"), force_facts=True))
        return 0
    if args.cmd == "search":
        print(search_text(" ".join(args.query)))
        return 0
    if args.cmd == "read":
        print(read_text(args.doc))
        return 0
    if args.cmd == "send":
        print(send_text(args.chat_id, " ".join(args.text)))
        return 0
    if args.cmd == "ask":
        asked = " ".join(args.text)
        print(
            dispatch(
                parse_intent(asked),
                user_text=asked,
                channel="p2p",
                chat_id=P2P_CHAT_ID,
            )
        )
        return 0
    if args.cmd == "plan":
        goal = " ".join(args.goal)
        print(
            dispatch(
                parse_intent(f"规划 {goal}" if goal else "任务规划"),
                user_text=goal or "任务规划",
                channel="p2p",
                chat_id=P2P_CHAT_ID,
                force_facts=True,
            )
        )
        return 0
    if args.cmd == "agent-worker":
        from .runner import run_worker

        run_worker(drain=args.drain)
        return 0
    if args.cmd == "serve":
        if args.install:
            print(install_serve())
            return 0
        return serve_loop(timeout=args.timeout, max_events=args.max_events)
    if args.cmd == "brief":
        if args.install:
            print(install_schedule())
            if not args.push:
                return 0
        if args.push:
            print(push_brief(force=args.force))
            extra = push_digest(force=args.force)
            if extra:
                print(extra)
            return 0
        print(brief_text())
        return 0
    if args.cmd == "digest":
        if args.push:
            print(push_digest(force=args.force))
            return 0
        print(digest_text())
        return 0
    if args.cmd == "followup":
        from .bitable import followup_cli_text
        from .schedule import install_weekly

        if args.install:
            print(install_weekly())
        ran = (
            args.setup_tables
            or args.digest_once
            or args.weekly_once
            or args.scan_bitable
            or args.scan_chats
        )
        if ran or not args.install:
            print(
                followup_cli_text(
                    setup=args.setup_tables,
                    digest=args.digest_once,
                    weekly=args.weekly_once,
                    scan=args.scan_bitable,
                    scan_chats=args.scan_chats,
                )
            )
        return 0
    if args.cmd == "mcp":
        from .mcp_server import serve_stdio

        return serve_stdio()
    if args.cmd == "mcp-http":
        from .mcp_http import serve_http

        return serve_http(host=args.host, port=args.port)
    if args.cmd == "webhook":
        from .webhook import serve_webhook

        return serve_webhook(host=args.host, port=args.port)
    if args.cmd == "workflow":
        from .workflow import list_workflows_text

        print(list_workflows_text())
        return 0
    if args.cmd == "rag":
        from .rag import rag_cli

        cmd = getattr(args, "rag_cmd", None)
        arg = " ".join(getattr(args, "rag_args", []) or []).strip()
        if cmd == "stats":
            print(rag_cli(stats=True))
        elif cmd == "index":
            print(rag_cli(index=arg))
        elif cmd == "query":
            print(rag_cli(query=arg))
        else:
            print(rag_cli())
        return 0
    if args.cmd == "sandbox":
        from .sandbox import sandbox_status_text

        print(sandbox_status_text())
        return 0
    if args.cmd == "eval":
        from .eval import eval_text

        print(eval_text())
        return 0
    if args.cmd == "versions":
        from .versions import versions_text

        print(versions_text(getattr(args, "versions_args", []) or []))
        return 0
    if args.cmd == "memory":
        from .memory import memory_cli

        print(memory_cli(getattr(args, "memory_args", []) or []))
        return 0
    print(help_text())
    return 0
