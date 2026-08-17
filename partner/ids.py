"""Known Feishu identities for this machine. Override with env if they rotate."""

from __future__ import annotations

import os

USER_OPEN_ID = os.environ.get(
    "FEISHU_PARTNER_USER_OPEN_ID",
    "ou_757b70ff62056f5c427a56f67b903ba9",
)
BOT_OPEN_ID = os.environ.get(
    "FEISHU_PARTNER_BOT_OPEN_ID",
    "ou_c3a38aa7d41cae6bede8eb2f2f9af91c74",
)
P2P_CHAT_ID = os.environ.get(
    "FEISHU_PARTNER_P2P_CHAT_ID",
    "oc_e1dac49bd713a3692b5a6b6bc6e3745f",
)
USER_NAMES = tuple(
    name.strip()
    for name in os.environ.get("FEISHU_PARTNER_USER_NAMES", "吴梦晨").split(",")
    if name.strip()
)
WEEKLY_QUERY = os.environ.get("FEISHU_PARTNER_WEEKLY_QUERY", "吴梦晨 周报")
