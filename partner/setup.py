"""Interactive-ish first-run setup for a new deployer (no secrets in git)."""

from __future__ import annotations

from typing import Any

from .ids import (
    BOT_OPEN_ID,
    P2P_CHAT_ID,
    USER_NAMES,
    USER_OPEN_ID,
    WEEKLY_QUERY,
    config_path,
    identity_ready,
    save_config,
)
from .lark import run_lark


def _open_id(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return ""
    for key in ("open_id", "user_id", "bot_open_id", "openId"):
        val = data.get(key)
        if isinstance(val, str) and val.startswith("ou_"):
            return val
    user = data.get("user")
    if isinstance(user, dict):
        val = user.get("open_id") or user.get("user_id")
        if isinstance(val, str) and val.startswith("ou_"):
            return val
    return ""


def _pick_p2p_chat(payload: dict[str, Any], _bot_open_id: str = "") -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    items = []
    if isinstance(data, dict):
        for key in ("items", "chats", "list"):
            raw = data.get(key)
            if isinstance(raw, list):
                items = raw
                break
    if isinstance(payload.get("items"), list):
        items = payload["items"]
    for item in items:
        if not isinstance(item, dict):
            continue
        chat_id = str(item.get("chat_id") or item.get("id") or "")
        if not chat_id.startswith("oc_"):
            continue
        mode = str(item.get("chat_mode") or item.get("chat_type") or "").lower()
        if mode and mode not in {"p2p", "private", "private_chat"}:
            continue
        return chat_id
    return ""


def setup_text(*, name: str = "", weekly_query: str = "", force: bool = False) -> str:
    display = (name or "").strip() or (USER_NAMES[0] if USER_NAMES else "")
    if not display:
        return "用法：feishu setup --name 你的名字\n会写入 ~/.feishu-partner/config.json（不进 git）。"
    if identity_ready() and not force:
        return (
            f"身份已配置（{display_name_safe()}）。\n"
            f"配置文件：{config_path()}\n"
            "若要重写，加 --force。"
        )

    user = run_lark(["whoami", "--as", "user"], as_identity="user")
    bot = run_lark(["whoami", "--as", "bot"], as_identity="bot")
    user_oid = _open_id(user) or USER_OPEN_ID
    bot_oid = _open_id(bot) or BOT_OPEN_ID
    if not user_oid or not bot_oid:
        return (
            "无法从 lark-cli whoami 读到 open_id。\n"
            "请先：lark-cli auth login（user + bot），再重试 feishu setup。"
        )

    chats = run_lark(["im", "+chat-list", "--types=p2p", "--as", "bot"], as_identity="bot")
    p2p = _pick_p2p_chat(chats, bot_oid) or P2P_CHAT_ID
    if not p2p:
        return (
            "已拿到 user/bot open_id，但还没有与机器人的单聊。\n"
            "请在飞书里给机器人发一条「你好」，再运行：feishu setup --name "
            f"{display}"
        )

    weekly = (weekly_query or "").strip() or f"{display} 周报"
    path = save_config(
        {
            "user_open_id": user_oid,
            "bot_open_id": bot_oid,
            "p2p_chat_id": p2p,
            "user_names": display,
            "weekly_query": weekly,
        }
    )
    from .ids import reload_identity
    from .hermes_setup import ensure_profile

    reload_identity()
    ensure_profile()
    from .memory import ensure_agents_md

    agents = ensure_agents_md()
    return (
        "已写入本地身份配置（勿提交 git）：\n"
        f"- 文件：{path}\n"
        f"- 用户：{user_oid}\n"
        f"- 机器人：{bot_oid}\n"
        f"- 单聊：{p2p}\n"
        f"- 称呼：{display}\n"
        f"- 周报检索：{weekly}\n"
        f"- Agents.md：{agents}\n\n"
        "下一步：feishu doctor && feishu serve --install\n"
        "若 serve 已在跑：launchctl kickstart -k gui/$(id -u)/com.feishu.partner.serve"
    )


def display_name_safe() -> str:
    from .ids import display_name

    return display_name()
