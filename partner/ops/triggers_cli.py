"""CLI for declarative scheduled triggers."""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Sequence

from ..core.ids import P2P_CHAT_ID
from ..core.triggers import (
    add_trigger,
    delete_trigger,
    format_triggers_text,
    get_trigger,
    list_triggers,
    mark_trigger_run,
    toggle_trigger,
)


def _with_id(parser: argparse.ArgumentParser, help_text: str) -> None:
    parser.add_argument("id", help=help_text)


def _cmd_add(args: argparse.Namespace) -> int:
    try:
        spec = add_trigger(
            goal=args.goal,
            schedule=args.schedule,
            title=args.title or "",
            chat_id=args.chat_id or "",
            enabled=not args.disabled,
        )
    except ValueError as exc:
        print(f"创建失败：{exc}", file=sys.stderr)
        return 1
    status = "启用" if spec.get("enabled") else "停用"
    print(
        f"已创建触发器 {spec['id']} [{status}]：{spec['schedule']}"
    )
    print(f"  目标：{spec['goal']}")
    if spec.get("next_run_at"):
        print(f"  下次运行：{spec['next_run_at']}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    specs = list_triggers(enabled_only=args.enabled_only)
    print(format_triggers_text(specs))
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    if delete_trigger(args.id):
        print(f"已删除触发器 {args.id}")
        return 0
    print(f"未找到触发器 {args.id}", file=sys.stderr)
    return 1


def _cmd_toggle(args: argparse.Namespace) -> int:
    if args.on and args.off:
        print("不能同时指定 --on 和 --off", file=sys.stderr)
        return 1
    if args.on:
        enabled = True
    elif args.off:
        enabled = False
    else:
        enabled = args.enable
    spec = toggle_trigger(args.id, enabled)
    if not spec:
        print(f"未找到触发器 {args.id}", file=sys.stderr)
        return 1
    status = "启用" if spec.get("enabled") else "停用"
    print(f"触发器 {args.id} 已{status}")
    if spec.get("next_run_at"):
        print(f"  下次运行：{spec['next_run_at']}")
    return 0


def _cmd_fire(args: argparse.Namespace) -> int:
    spec = get_trigger(args.id)
    if not spec:
        print(f"未找到触发器 {args.id}", file=sys.stderr)
        return 1
    from ..runtime.agent.service import start_agent_task

    chat_id = (spec.get("chat_id") or "").strip() or P2P_CHAT_ID
    title = str(spec.get("title") or spec.get("goal") or "触发器").strip()
    print(start_agent_task(spec["goal"], chat_id, background=True))
    updated = mark_trigger_run(args.id)
    if updated:
        print(
            f"触发器 {args.id}「{title}」已执行，累计运行 {updated.get('run_count', 0)} 次。"
        )
    return 0


def triggers_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="feishu triggers",
        description="声明式定时触发器：按 cron/daily/weekly/once 启动 Agent 任务。",
    )
    sub = parser.add_subparsers(dest="subcmd")

    p_list = sub.add_parser("list", help="列出触发器")
    p_list.add_argument(
        "--enabled-only", action="store_true", help="只显示启用的触发器"
    )

    p_add = sub.add_parser("add", help="添加触发器")
    p_add.add_argument("--title", default="", help="触发器标题")
    p_add.add_argument("--goal", required=True, help="要执行的 Agent 目标")
    p_add.add_argument(
        "--schedule", required=True,
        help="触发计划，例如 daily@09:00 / weekly@Mon09:00 / cron@*/15 9 * * 1-5 / once@2026-08-27T09:00"
    )
    p_add.add_argument("--chat-id", default="", help="目标聊天 ID，默认私聊")
    p_add.add_argument("--disabled", action="store_true", help="创建后先停用")

    p_del = sub.add_parser("del", help="删除触发器")
    _with_id(p_del, "触发器 ID")

    p_toggle = sub.add_parser("toggle", help="启用/停用触发器")
    _with_id(p_toggle, "触发器 ID")
    p_toggle.add_argument("--on", action="store_true", help="启用")
    p_toggle.add_argument("--off", action="store_true", help="停用")
    p_toggle.add_argument(
        "--enable", action="store_true", default=True, help=argparse.SUPPRESS
    )

    p_fire = sub.add_parser("fire", help="立即手动触发一次")
    _with_id(p_fire, "触发器 ID")

    args = parser.parse_args(argv or [])
    dispatch: dict[str, Callable[[argparse.Namespace], int]] = {
        "list": _cmd_list,
        "add": _cmd_add,
        "del": _cmd_delete,
        "toggle": _cmd_toggle,
        "fire": _cmd_fire,
    }
    handler = dispatch.get(args.subcmd)
    if handler is None:
        # Default to list.
        return _cmd_list(argparse.Namespace(enabled_only=False))
    return handler(args)
