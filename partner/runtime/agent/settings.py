"""Agent Runtime settings: ~/.feishu-partner/agent.json (env overrides)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ...paths import REPO_ROOT

_DEFAULTS: dict[str, Any] = {
    "brain": "auto",  # auto | hermes | heuristic | openai
    "legacy_runner": False,
    "max_steps": 8,
    "no_llm": False,
    "require_llm": False,
    "hermes_bin": "",
    "openai_api_key": "",
    "openai_api_keys": [],
    "openai_base_url": "",
    "openai_model": "",
}


def agent_config_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_AGENT_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner" / "agent.json"


def example_path() -> Path:
    return REPO_ROOT / "agent.example.json"


def _as_bool(val: Any, default: bool = False) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_keys(blob: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    raw_list = blob.get("openai_api_keys")
    if isinstance(raw_list, list):
        for item in raw_list:
            s = str(item or "").strip()
            if s and s not in keys:
                keys.append(s)
    single = str(blob.get("openai_api_key") or "").strip()
    if single and single not in keys:
        keys.insert(0, single)
    return keys


def _coerce(blob: dict[str, Any]) -> dict[str, Any]:
    out = dict(_DEFAULTS)
    out["openai_api_keys"] = []
    for key, default in _DEFAULTS.items():
        if key == "openai_api_keys":
            continue
        if key not in blob:
            continue
        val = blob[key]
        if isinstance(default, bool):
            out[key] = _as_bool(val, default)
        elif isinstance(default, int):
            try:
                out[key] = int(val)
            except (TypeError, ValueError):
                out[key] = default
        elif isinstance(default, list):
            continue
        else:
            out[key] = str(val or "").strip()
    keys = _normalize_keys(blob)
    out["openai_api_keys"] = keys
    out["openai_api_key"] = keys[0] if keys else str(out.get("openai_api_key") or "").strip()
    brain = str(out.get("brain") or "auto").strip().lower()
    if brain not in {"auto", "hermes", "heuristic", "openai"}:
        brain = "auto"
    out["brain"] = brain
    out["max_steps"] = max(1, min(int(out.get("max_steps") or 8), 32))
    return out


def load_agent_config() -> dict[str, Any]:
    path = agent_config_path()
    file_cfg: dict[str, Any] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                file_cfg = raw
        except (OSError, json.JSONDecodeError):
            file_cfg = {}
    cfg = _coerce(file_cfg)

    if os.environ.get("FEISHU_PARTNER_NO_LLM") == "1":
        cfg["no_llm"] = True
    if os.environ.get("FEISHU_PARTNER_LEGACY_RUNNER") == "1":
        cfg["legacy_runner"] = True
    brain_env = (os.environ.get("FEISHU_PARTNER_AGENT_BRAIN") or "").strip().lower()
    if brain_env in {"auto", "hermes", "heuristic", "openai"}:
        cfg["brain"] = brain_env
    max_env = (os.environ.get("FEISHU_PARTNER_AGENT_MAX_STEPS") or "").strip()
    if max_env.isdigit():
        cfg["max_steps"] = max(1, min(int(max_env), 32))
    if (os.environ.get("FEISHU_PARTNER_REQUIRE_LLM") or "").strip() in {"1", "true", "yes"}:
        cfg["require_llm"] = True
    hermes = (os.environ.get("HERMES_BIN") or "").strip()
    if hermes:
        cfg["hermes_bin"] = hermes

    env_keys: list[str] = []
    for env_name in (
        "OPENAI_API_KEY",
        "FEISHU_PARTNER_OPENAI_API_KEY",
        "FEISHU_PARTNER_OPENAI_API_KEYS",
    ):
        raw = (os.environ.get(env_name) or "").strip()
        if not raw:
            continue
        if env_name.endswith("KEYS"):
            for part in raw.split(","):
                s = part.strip()
                if s and s not in env_keys:
                    env_keys.append(s)
        elif raw not in env_keys:
            env_keys.append(raw)
    if env_keys:
        cfg["openai_api_keys"] = env_keys
        cfg["openai_api_key"] = env_keys[0]

    base = (os.environ.get("OPENAI_BASE_URL") or os.environ.get("FEISHU_PARTNER_OPENAI_BASE_URL") or "").strip()
    if base:
        cfg["openai_base_url"] = base
    model = (os.environ.get("OPENAI_MODEL") or os.environ.get("FEISHU_PARTNER_OPENAI_MODEL") or "").strip()
    if model:
        cfg["openai_model"] = model
    return cfg


def save_agent_config(data: dict[str, Any]) -> Path:
    path = agent_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = _coerce(data)
    # Persist keys list explicitly for dual-key setups.
    to_write = {
        "brain": cleaned["brain"],
        "legacy_runner": cleaned["legacy_runner"],
        "max_steps": cleaned["max_steps"],
        "no_llm": cleaned["no_llm"],
        "require_llm": cleaned["require_llm"],
        "hermes_bin": cleaned["hermes_bin"],
        "openai_api_key": cleaned["openai_api_key"],
        "openai_api_keys": cleaned["openai_api_keys"],
        "openai_base_url": cleaned["openai_base_url"],
        "openai_model": cleaned["openai_model"],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(to_write, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def ensure_agent_config(*, force: bool = False) -> tuple[Path, bool]:
    """Write default agent.json from example if missing. Returns (path, created)."""
    path = agent_config_path()
    if path.is_file() and not force:
        return path, False
    src = example_path()
    if src.is_file():
        blob = json.loads(src.read_text(encoding="utf-8"))
    else:
        blob = dict(_DEFAULTS)
    save_agent_config(blob if isinstance(blob, dict) else dict(_DEFAULTS))
    return path, True


def openai_keys() -> list[str]:
    cfg = load_agent_config()
    keys = cfg.get("openai_api_keys")
    if isinstance(keys, list) and keys:
        return [str(k).strip() for k in keys if str(k).strip()]
    single = str(cfg.get("openai_api_key") or "").strip()
    return [single] if single else []


def agent_config_status_text() -> str:
    path, created = ensure_agent_config()
    cfg = load_agent_config()
    keys = openai_keys()
    lines = [
        "Agent 配置",
        f"- 文件：{path}" + ("（刚写入默认）" if created else ""),
        f"- brain：{cfg.get('brain')}（auto=先 Hermes，再 OpenAI 兼容；heuristic=不用模型）",
        f"- max_steps：{cfg.get('max_steps')}",
        f"- no_llm：{cfg.get('no_llm')}  require_llm：{cfg.get('require_llm')}",
        f"- legacy_runner：{cfg.get('legacy_runner')}",
        f"- Hermes：{cfg.get('hermes_bin') or 'PATH/默认探测'}",
        f"- OpenAI keys：{len(keys)} 把" + ("（已配置）" if keys else "（未配置；Hermes 优先时可不配）"),
        f"- OpenAI base/model：{(cfg.get('openai_base_url') or '默认')}/{(cfg.get('openai_model') or '默认')}",
        "",
        "改配置：编辑上述 JSON，或复制仓内 agent.example.json。",
        "环境变量可覆盖同名项（FEISHU_PARTNER_AGENT_BRAIN / OPENAI_API_KEY 等）。",
    ]
    return "\n".join(lines)


def use_legacy_runner() -> bool:
    return bool(load_agent_config().get("legacy_runner"))


def agent_max_steps() -> int:
    return int(load_agent_config().get("max_steps") or 8)


def llm_disabled() -> bool:
    return bool(load_agent_config().get("no_llm"))


def brain_mode() -> str:
    return str(load_agent_config().get("brain") or "auto")


def require_llm() -> bool:
    return bool(load_agent_config().get("require_llm"))
