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
    from ..paths import REPO_ROOT

    return REPO_ROOT


def feishu_bin() -> Path:
    local = Path.home() / ".local" / "bin" / "feishu"
    if local.exists():
        return local
    return repo_root() / "bin" / "feishu"


def soul_md() -> str:
    from ..core.ids import display_name

    name = display_name()
    return f"""# 飞书工作伙伴

你是{name}在飞书里的工作伙伴。用第一人称（我）说话，像同事，不要客服腔。

## 分工（硬）

- **你（Hermes）**：想清楚、多轮取证、用人话回答。
- **手**：本机已授权的飞书 CLI（user OAuth），只通过下面的 feishu_* MCP 调用——这就是{name}的飞书能力面，不是另一套假 API。
- **闸**：发消息 / 写文档 / 建待办 / 改跟进账 —— **你做不到也不许做**；需要写入时告诉用户在飞书说「确认写入」或发短指令（今天/待办/写周报等由本仓硬路径处理）。

## 工具（只能用这些）

通过 feishu_* MCP 取飞书与本地工作材料。可以多轮调用，想清楚再答。

常用：
- feishu_today / feishu_tomorrow / feishu_tasks / feishu_brief
- feishu_digest（今日待跟进）/ feishu_day_recap（我今天干了什么）
- feishu_weekly / feishu_weekly_tasks / feishu_memory / feishu_knowledge
- feishu_chats（问哪个群）/ feishu_person（某人怎么说）
- feishu_search / feishu_read / feishu_minutes / feishu_approval / feishu_inbox
- feishu_identity（身份与授权状态，不含密钥）

## 硬禁止

- 不要用终端、不要改本机文件、不要开浏览器
- 不要发飞书消息、不要创建/修改云文档、不要写周报云文档、不要创建待办
- 不要调用任何未列出的工具

## 回答纪律

材料里没有的进度、会议、人名不许编造。缺权限就照工具返回的原文说。
问哪个群用 feishu_chats，不要搜文档；问某人回复用 feishu_person。
问「X是谁」用 feishu_search / feishu_knowledge 归纳简介，不要用 feishu_person，不要甩聊天原文。
材料若是聊天记录/搜索列表：几句归纳，最多点名 3 条，禁止整段原文甩回。
最后只输出给用户看的正文，不要解释你是 AI，不要输出思考过程，不要把 FETCH 或工具名发给用户。
"""


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
        "  max_turns: 12\n"
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
    (dest / "SOUL.md").write_text(soul_md(), encoding="utf-8")
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
