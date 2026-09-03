"""组织关系缓存与人员解析。

把 contact +search-user 结果缓存到本地，支持别名语义引用（「张总」「小李」→ open_id）。
缓存文件 ~/.feishu-partner/org-cache.json，可用 FEISHU_PARTNER_ORG_CACHE 覆盖。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..core.lark import run_lark

CN_TZ = timezone(timedelta(hours=8))
CACHE_PATH = Path.home() / ".feishu-partner" / "org-cache.json"
CACHE_TTL_DAYS = 30
# 常见称呼后缀，语义引用时剥掉再匹配
_HONORIFICS = ("总", "哥", "姐", "老师", "经理", "老板", "工", "同学", "兄")


def cache_path() -> Path:
    override = os.environ.get("FEISHU_PARTNER_ORG_CACHE")
    if override:
        return Path(override).expanduser()
    return CACHE_PATH


def load_cache() -> dict[str, Any]:
    path = cache_path()
    if not path.exists():
        return {"people": {}, "aliases": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"people": {}, "aliases": {}}
    if not isinstance(data, dict):
        return {"people": {}, "aliases": {}}
    data.setdefault("people", {})
    data.setdefault("aliases", {})
    return data


def save_cache(data: dict[str, Any]) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _norm(name: str) -> str:
    return (name or "").strip()


def _candidates(name: str) -> list[str]:
    """「张总」→ [张总, 张]；「王小明」→ [王小明]。用于语义别名匹配。"""
    raw = _norm(name)
    if not raw:
        return []
    out = [raw]
    for suffix in _HONORIFICS:
        if raw.endswith(suffix) and len(raw) > len(suffix) + 1:
            out.append(raw[: -len(suffix)])
            break
    return out


def _fresh(entry: dict[str, Any]) -> bool:
    stamp = str(entry.get("cached_at") or "")
    if not stamp:
        return False
    try:
        cached = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    return (datetime.now(CN_TZ) - cached).days < CACHE_TTL_DAYS


def learn(name: str, open_id: str, *, dept: str = "") -> None:
    """记录一条 名字→open_id 映射（供任务推进/语义引用复用）。"""
    name = _norm(name)
    open_id = (open_id or "").strip()
    if not name or not open_id.startswith("ou_"):
        return
    cache = load_cache()
    cache["people"][name] = {
        "open_id": open_id,
        "dept": dept,
        "cached_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
    }
    save_cache(cache)


def learn_alias(alias: str, canonical: str) -> None:
    """记录别名：「张总」→「张三」。canonical 必须已在 people 里或随后会学到。"""
    alias = _norm(alias)
    canonical = _norm(canonical)
    if alias and canonical and alias != canonical:
        cache = load_cache()
        cache["aliases"][alias] = canonical
        save_cache(cache)


def _search_remote(name: str) -> tuple[str, str]:
    """contact +search-user 拉一次，返回 (open_id, dept)。失败返回 ('', '')。"""
    payload = run_lark(
        ["contact", "+search-user", "--query", name, "--page-size", "5"],
        as_identity="user",
    )
    if payload.get("ok") is False:
        return "", ""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    users = data.get("users") or data.get("items") or []
    if not isinstance(users, list):
        return "", ""
    for user in users:
        if not isinstance(user, dict):
            continue
        uname = str(user.get("name") or user.get("localized_name") or "").strip()
        if uname != name:
            continue
        oid = str(user.get("open_id") or user.get("user_id") or "").strip()
        if oid.startswith("ou_"):
            dept = str(user.get("department_name") or user.get("department") or "")
            return oid, dept
    # 没有完全重名，取第一个
    for user in users:
        if isinstance(user, dict):
            oid = str(user.get("open_id") or user.get("user_id") or "").strip()
            if oid.startswith("ou_"):
                dept = str(user.get("department_name") or user.get("department") or "")
                return oid, dept
    return "", ""


def resolve(name: str, *, allow_remote: bool = True) -> str:
    """名字/别名 → open_id。先别名→本名，再缓存，最后远程搜索并缓存。"""
    for cand in _candidates(name):
        cache = load_cache()
        canonical = cache["aliases"].get(cand, cand)
        entry = cache["people"].get(canonical)
        if isinstance(entry, dict) and _fresh(entry):
            oid = str(entry.get("open_id") or "")
            if oid:
                return oid
        if not allow_remote:
            continue
        oid, dept = _search_remote(canonical)
        if oid:
            learn(canonical, oid, dept=dept)
            if canonical != cand:
                learn_alias(cand, canonical)
            return oid
    return ""


def org_cli(argv: list[str]) -> int:
    """CLI 入口：feishu org [cache|resolve <名字>|alias <别名> <本名>]"""
    import argparse

    parser = argparse.ArgumentParser(prog="feishu org", description="组织关系缓存")
    parser.add_argument("org_args", nargs="*", default=[])
    args = parser.parse_args(argv)
    words = list(args.org_args or [])
    if not words or words[0] == "cache":
        cache = load_cache()
        people = cache["people"]
        if not people:
            print("组织缓存是空的。")
            return 0
        lines = [f"组织缓存 {len(people)} 人："]
        for name, entry in sorted(people.items()):
            oid = str(entry.get("open_id") or "") if isinstance(entry, dict) else ""
            dept = str(entry.get("dept") or "") if isinstance(entry, dict) else ""
            alias_note = ""
            lines.append(f"- {name} → {oid}" + (f"（{dept}）" if dept else "") + alias_note)
        aliases = cache["aliases"]
        if aliases:
            lines.append("别名：" + "、".join(f"{a}→{c}" for a, c in sorted(aliases.items())))
        print("\n".join(lines))
        return 0
    if words[0] == "resolve" and len(words) > 1:
        oid = resolve(words[1])
        print(f"{words[1]} → {oid}" if oid else f"没找到「{words[1]}」。")
        return 0
    if words[0] == "alias" and len(words) > 2:
        learn_alias(words[1], words[2])
        print(f"已记别名：{words[1]} → {words[2]}")
        return 0
    print("用法：feishu org [cache|resolve <名字>|alias <别名> <本名>]")
    return 1
