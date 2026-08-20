"""Deployer identity. Prefer env, then ~/.feishu-partner/config.json. No personal IDs in git."""

from __future__ import annotations

import json
import os
from pathlib import Path


def config_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner" / "config.json"


def load_config() -> dict[str, str]:
    path = config_path()
    if not path.is_file():
        return {}
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(blob, dict):
        return {}
    return {str(k): str(v) for k, v in blob.items() if v is not None}


def save_config(data: dict[str, str]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = {str(k): str(v).strip() for k, v in data.items() if str(v).strip()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def _pick(env_key: str, config_key: str, default: str = "") -> str:
    env = (os.environ.get(env_key) or "").strip()
    if env:
        return env
    cfg = load_config()
    return (cfg.get(config_key) or default).strip()


def reload_identity() -> None:
    """Re-read env/config into module globals (call after feishu setup)."""
    global USER_OPEN_ID, BOT_OPEN_ID, P2P_CHAT_ID, USER_NAMES, WEEKLY_QUERY
    USER_OPEN_ID = _pick("FEISHU_PARTNER_USER_OPEN_ID", "user_open_id")
    BOT_OPEN_ID = _pick("FEISHU_PARTNER_BOT_OPEN_ID", "bot_open_id")
    P2P_CHAT_ID = _pick("FEISHU_PARTNER_P2P_CHAT_ID", "p2p_chat_id")
    names_raw = _pick("FEISHU_PARTNER_USER_NAMES", "user_names", "")
    USER_NAMES = tuple(n.strip() for n in names_raw.split(",") if n.strip())
    WEEKLY_QUERY = _pick(
        "FEISHU_PARTNER_WEEKLY_QUERY",
        "weekly_query",
        f"{USER_NAMES[0]} 周报" if USER_NAMES else "",
    )


USER_OPEN_ID = _pick("FEISHU_PARTNER_USER_OPEN_ID", "user_open_id")
BOT_OPEN_ID = _pick("FEISHU_PARTNER_BOT_OPEN_ID", "bot_open_id")
P2P_CHAT_ID = _pick("FEISHU_PARTNER_P2P_CHAT_ID", "p2p_chat_id")
_names_raw = _pick("FEISHU_PARTNER_USER_NAMES", "user_names", "")
USER_NAMES = tuple(n.strip() for n in _names_raw.split(",") if n.strip())
WEEKLY_QUERY = _pick(
    "FEISHU_PARTNER_WEEKLY_QUERY",
    "weekly_query",
    f"{USER_NAMES[0]} 周报" if USER_NAMES else "",
)


def display_name() -> str:
    return USER_NAMES[0] if USER_NAMES else "用户"


def identity_ready() -> bool:
    return bool(USER_OPEN_ID and BOT_OPEN_ID and P2P_CHAT_ID and USER_NAMES)


def identity_hint() -> str:
    missing: list[str] = []
    if not USER_OPEN_ID:
        missing.append("user_open_id / FEISHU_PARTNER_USER_OPEN_ID")
    if not BOT_OPEN_ID:
        missing.append("bot_open_id / FEISHU_PARTNER_BOT_OPEN_ID")
    if not P2P_CHAT_ID:
        missing.append("p2p_chat_id / FEISHU_PARTNER_P2P_CHAT_ID")
    if not USER_NAMES:
        missing.append("user_names / FEISHU_PARTNER_USER_NAMES")
    return (
        "身份未配置。请运行：`feishu setup --name 你的名字`\n"
        f"缺少：{', '.join(missing)}\n"
        f"或编辑：{config_path()}"
    )
