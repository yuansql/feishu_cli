"""Install an isolated Hermes profile that only sees the Feishu MCP."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

PROFILE_NAME = "feishupartner"


def profile_dir() -> Path:
    override = os.environ.get("FEISHU_HERMES_PROFILE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".hermes" / "profiles" / PROFILE_NAME


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def feishu_bin() -> Path:
    local = Path.home() / ".local" / "bin" / "feishu"
    if local.exists():
        return local
    return repo_root() / "bin" / "feishu"


def profile_config_text() -> str:
    # ponytail: python -m partner, avoid bash wrapper + non-ascii path in argv0.
    root = repo_root()
    py = sys.executable
    return (
        "model:\n"
        "  provider: nous\n"
        "  base_url: https://inference-api.nousresearch.com/v1\n"
        "  default: stepfun/step-3.7-flash:free\n"
        "agent:\n"
        "  max_turns: 8\n"
        "platform_toolsets:\n"
        "  cli: []\n"
        "mcp_servers:\n"
        "  feishu:\n"
        f"    command: {py}\n"
        "    args:\n"
        "      - -u\n"
        "      - -m\n"
        "      - partner\n"
        "      - mcp\n"
        "    env:\n"
        f"      PYTHONPATH: {root}\n"
        "      PYTHONUNBUFFERED: \"1\"\n"
        "    enabled: true\n"
        "    connect_timeout: 20\n"
    )


SOUL_MD = """# 飞书工作伙伴

你是吴梦晨在飞书里的工作伙伴。用第一人称（我）说话，像同事，不要客服腔。

只通过 feishu_* 工具取飞书材料。不要用终端、不要改文件、不要开浏览器、不要发消息、不要写周报云文档。
问哪个群、交给测试的群：用 feishu_chats（可带 query），不要搜文档。
问某人回复、怎么说、回了没：用 feishu_person（query 用人名），不要搜文档。
材料里没有的进度、会议、人名不许编造。缺权限就照工具返回的原文说。
最后只输出给用户看的正文，不要解释你是 AI，不要输出思考过程，不要把 FETCH 或工具名发给用户。
"""


def profile_ready() -> bool:
    dest = profile_dir()
    config = dest / "config.yaml"
    if not config.is_file():
        return False
    text = config.read_text(encoding="utf-8")
    return "mcp_servers:" in text and "feishu:" in text and "cli: []" in text


def ensure_profile() -> bool:
    dest = profile_dir()
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "config.yaml").write_text(profile_config_text(), encoding="utf-8")
    (dest / "SOUL.md").write_text(SOUL_MD, encoding="utf-8")
    (dest / ".no-bundled-skills").write_text(
        "This profile opted out of bundled-skill seeding.\n",
        encoding="utf-8",
    )
    for name in ("auth.json", ".env"):
        src = Path.home() / ".hermes" / name
        dst = dest / name
        if src.is_file() and not dst.exists():
            shutil.copy2(src, dst)
            os.chmod(dst, 0o600)
    return profile_ready()


def profile_status_line() -> str:
    if profile_ready():
        return f"Hermes 隔离档案：OK（{PROFILE_NAME}，仅飞书取数）"
    return f"Hermes 隔离档案：未就绪（{PROFILE_NAME}）"
