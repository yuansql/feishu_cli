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
from .ids import P2P_CHAT_ID
from .schedule import install_schedule, install_serve
from .formatters import HELP_TEXT
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
}


def _passthrough(argv: list[str]) -> int:
    binary = find_lark_cli()
    return subprocess.call([str(binary), *argv])


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        print(HELP_TEXT)
        print("CLI：feishu status|doctor|today|tasks|search|read|chats|send|serve|brief")
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

    p_search = sub.add_parser("search")
    p_search.add_argument("query", nargs="+")

    p_read = sub.add_parser("read")
    p_read.add_argument("doc")

    p_send = sub.add_parser("send")
    p_send.add_argument("chat_id")
    p_send.add_argument("text", nargs="+")

    p_ask = sub.add_parser("ask")
    p_ask.add_argument("text", nargs="+")

    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--timeout", default=None)
    p_serve.add_argument("--max-events", type=int, default=0)
    p_serve.add_argument("--install", action="store_true")

    p_brief = sub.add_parser("brief")
    p_brief.add_argument("--push", action="store_true")
    p_brief.add_argument("--force", action="store_true")
    p_brief.add_argument("--install", action="store_true")

    sub.add_parser("mcp")

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
        print(HELP_TEXT)
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
            return 0
        print(brief_text())
        return 0
    if args.cmd == "mcp":
        from .mcp_server import serve_stdio

        return serve_stdio()
    print(HELP_TEXT)
    return 0
