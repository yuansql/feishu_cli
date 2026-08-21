"""Install and check the 09:00 daily brief LaunchAgent. This job must exist."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..core.lark import find_lark_cli

BRIEF_LABEL = "com.feishu.partner.brief"
SERVE_LABEL = "com.feishu.partner.serve"
WEEKLY_LABEL = "com.feishu.partner.weekly-tasks"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{BRIEF_LABEL}.plist"
SERVE_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{SERVE_LABEL}.plist"
WEEKLY_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{WEEKLY_LABEL}.plist"


def partner_bin() -> Path:
    from ..paths import BIN_FEISHU

    return BIN_FEISHU


def plist_body(feishu_bin: str, path_value: str) -> str:
    log = str(Path.home() / ".feishu-partner" / "brief.launchd.log")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{BRIEF_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{feishu_bin}</string>
    <string>brief</string>
    <string>--push</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>{path_value}</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>9</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>{log}</string>
  <key>StandardErrorPath</key>
  <string>{log}</string>
</dict>
</plist>
"""


def weekly_plist_body(feishu_bin: str, path_value: str) -> str:
    log = str(Path.home() / ".feishu-partner" / "weekly.launchd.log")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{WEEKLY_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{feishu_bin}</string>
    <string>followup</string>
    <string>--weekly-once</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>{path_value}</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>1</integer>
    <key>Hour</key>
    <integer>9</integer>
    <key>Minute</key>
    <integer>5</integer>
  </dict>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>{log}</string>
  <key>StandardErrorPath</key>
  <string>{log}</string>
</dict>
</plist>
"""


def _path_value() -> str:
    extras = [str(Path.home() / ".local" / "bin"), "/usr/bin", "/bin", "/usr/sbin"]
    try:
        extras.insert(0, str(find_lark_cli().parent))
    except FileNotFoundError:
        pass
    existing = os.environ.get("PATH", "")
    parts = extras + [p for p in existing.split(":") if p and p not in extras]
    return ":".join(parts)


def serve_plist_body(feishu_bin: str, path_value: str, env: dict[str, str]) -> str:
    log = str(Path.home() / ".feishu-partner" / "serve.launchd.log")
    env_xml = "".join(
        f"    <key>{key}</key>\n    <string>{value}</string>\n"
        for key, value in env.items()
        if key and value
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{SERVE_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{feishu_bin}</string>
    <string>serve</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>{path_value}</string>
{env_xml}  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{log}</string>
  <key>StandardErrorPath</key>
  <string>{log}</string>
</dict>
</plist>
"""


def _serve_env() -> dict[str, str]:
    from . import ids as _ids

    _ids.reload_identity()
    return {
        "FEISHU_PARTNER_USER_OPEN_ID": _ids.USER_OPEN_ID,
        "FEISHU_PARTNER_BOT_OPEN_ID": _ids.BOT_OPEN_ID,
        "FEISHU_PARTNER_P2P_CHAT_ID": _ids.P2P_CHAT_ID,
        "FEISHU_PARTNER_USER_NAMES": ",".join(_ids.USER_NAMES),
        "FEISHU_PARTNER_WEEKLY_QUERY": _ids.WEEKLY_QUERY,
    }


def _launch_loaded(label: str) -> bool:
    uid = os.getuid()
    proc = subprocess.run(
        ["launchctl", "print", f"gui/{uid}/{label}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def schedule_loaded() -> bool:
    return _launch_loaded(BRIEF_LABEL)


def serve_loaded() -> bool:
    return _launch_loaded(SERVE_LABEL)


def weekly_loaded() -> bool:
    return _launch_loaded(WEEKLY_LABEL)


def weekly_status_line() -> str:
    return _agent_status(
        "周一 09:05 本周任务", WEEKLY_LABEL, WEEKLY_PLIST_PATH, "feishu followup --install"
    )


def _bootstrap(label: str, plist: Path) -> str | None:
    uid = os.getuid()
    target = f"gui/{uid}/{label}"
    subprocess.run(["launchctl", "bootout", target], capture_output=True, check=False)
    loaded = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist)],
        capture_output=True,
        text=True,
        check=False,
    )
    if loaded.returncode != 0:
        fallback = subprocess.run(
            ["launchctl", "load", "-w", str(plist)],
            capture_output=True,
            text=True,
            check=False,
        )
        if fallback.returncode != 0 and not _launch_loaded(label):
            return (loaded.stderr or fallback.stderr or "launchctl 失败").strip()
    if not _launch_loaded(label):
        return f"plist 在 {plist}，launchctl 未看见 {label}。"
    return None


def install_schedule() -> str:
    feishu = partner_bin()
    if not feishu.exists():
        return f"找不到 {feishu}，定时任务没装上。"
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    Path.home().joinpath(".feishu-partner").mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist_body(str(feishu), _path_value()), encoding="utf-8")
    err = _bootstrap(BRIEF_LABEL, PLIST_PATH)
    if err:
        return f"LaunchAgent 写入了 {PLIST_PATH}，但没加载上：{err}"
    weekly = install_weekly()
    return f"09:00 简报定时已装：{BRIEF_LABEL} → {feishu} brief --push\n{weekly}"


def install_serve() -> str:
    from ..core.ids import identity_hint, identity_ready, reload_identity

    reload_identity()
    if not identity_ready():
        return identity_hint()
    feishu = partner_bin()
    if not feishu.exists():
        return f"找不到 {feishu}，收消息定时没装上。"
    SERVE_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    Path.home().joinpath(".feishu-partner").mkdir(parents=True, exist_ok=True)
    SERVE_PLIST_PATH.write_text(
        serve_plist_body(str(feishu), _path_value(), _serve_env()),
        encoding="utf-8",
    )
    err = _bootstrap(SERVE_LABEL, SERVE_PLIST_PATH)
    if err:
        return f"LaunchAgent 写入了 {SERVE_PLIST_PATH}，但没加载上：{err}"
    return f"收消息常驻已装：{SERVE_LABEL} → {feishu} serve（开机/掉线会拉起）"


def install_weekly() -> str:
    feishu = partner_bin()
    if not feishu.exists():
        return f"找不到 {feishu}，周一任务定时没装上。"
    WEEKLY_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    Path.home().joinpath(".feishu-partner").mkdir(parents=True, exist_ok=True)
    WEEKLY_PLIST_PATH.write_text(
        weekly_plist_body(str(feishu), _path_value()), encoding="utf-8"
    )
    err = _bootstrap(WEEKLY_LABEL, WEEKLY_PLIST_PATH)
    if err:
        return f"LaunchAgent 写入了 {WEEKLY_PLIST_PATH}，但没加载上：{err}"
    return f"周一 09:05 本周任务已装：{WEEKLY_LABEL} → {feishu} followup --weekly-once"


def _agent_status(name: str, label: str, plist: Path, install: str) -> str:
    exists = plist.exists()
    loaded = _launch_loaded(label)
    if exists and loaded:
        return f"{name}：OK（{label}）"
    if exists:
        return f"{name}：FAIL plist 在、未加载。`{install}`"
    return f"{name}：FAIL 未安装。`{install}`"


def schedule_status_line() -> str:
    return _agent_status("09:00 简报定时", BRIEF_LABEL, PLIST_PATH, "feishu brief --install")


def serve_status_line() -> str:
    return _agent_status("收消息常驻", SERVE_LABEL, SERVE_PLIST_PATH, "feishu serve --install")


def schedule_status_lines() -> list[str]:
    return [schedule_status_line(), weekly_status_line(), serve_status_line()]
