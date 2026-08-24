"""Feishu Base tables for weekly tasks. External authority: fail → don't fake writes."""

from __future__ import annotations

from datetime import date, datetime
import json
import os
from pathlib import Path
from typing import Any

from .followup import (
    DEFAULT_TEMPLATES,
    add_bitable_hit,
    digest_text,
    load_items,
    record_mentions_user,
    record_submit_date,
    weekly_rows,
)
from ..compose.formatters import format_lark_error
from ..core.ids import P2P_CHAT_ID, USER_NAMES, USER_OPEN_ID
from ..core.lark import run_lark

CONFIG_PATH = Path.home() / ".feishu-partner" / "bitable.json"
SEEN_PATH = Path.home() / ".feishu-partner" / "bitable-seen.json"

_WEEKLY_FIELDS = (
    '[{"name":"标题","type":"text"},'
    '{"name":"类型","type":"select","options":[{"name":"例行"},{"name":"跟进"}]},'
    '{"name":"对接人","type":"text"},'
    '{"name":"截止","type":"text"},'
    '{"name":"状态","type":"select","options":[{"name":"未完成"},{"name":"已完成"}]},'
    '{"name":"来源","type":"text"}]'
)
_TEMPLATE_FIELDS = (
    '[{"name":"标题","type":"text"},'
    '{"name":"说明","type":"text"},'
    '{"name":"启用","type":"select","options":[{"name":"是"},{"name":"否"}]}]'
)


def config_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    override = os.environ.get("FEISHU_PARTNER_BITABLE")
    if override:
        return Path(override).expanduser()
    return CONFIG_PATH


def load_config(path: Path | None = None) -> dict[str, Any]:
    dest = config_path(path)
    if not dest.exists():
        return {}
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict[str, Any], path: Path | None = None) -> None:
    dest = config_path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _pick_token(payload: dict[str, Any], *keys: str) -> str:
    stack: list[Any] = [payload, payload.get("data")]
    while stack:
        cur = stack.pop(0)
        if not isinstance(cur, dict):
            continue
        for key in keys:
            val = cur.get(key)
            if isinstance(val, str) and val:
                return val
        for nested in ("app", "base", "table", "data"):
            if isinstance(cur.get(nested), dict):
                stack.append(cur[nested])
    return ""


def _ok(payload: dict[str, Any]) -> bool:
    if payload.get("ok") is False:
        return False
    return "error" not in payload or not payload.get("error")


def setup_tables() -> str:
    created = run_lark(
        [
            "base",
            "+base-create",
            "--name",
            "工作伙伴跟进",
            "--time-zone",
            "Asia/Shanghai",
            "--table-name",
            "本周任务",
            "--fields",
            _WEEKLY_FIELDS,
        ],
        as_identity="user",
    )
    if not _ok(created):
        return format_lark_error(created) + "\n多维表没建上，本地跟进账仍可用。"
    base_token = _pick_token(created, "app_token", "base_token", "token")
    if not base_token:
        return "飞书回了建表结果，但没有 app_token，没写入本地配置。"
    weekly_table = _pick_token(created, "table_id") or "本周任务"
    extra = run_lark(
        [
            "base",
            "+table-create",
            "--base-token",
            base_token,
            "--name",
            "每周例行模板",
            "--fields",
            _TEMPLATE_FIELDS,
        ],
        as_identity="user",
    )
    template_table = "每周例行模板"
    notes = []
    if not _ok(extra):
        notes.append("每周例行模板没建成：" + format_lark_error(extra))
    else:
        template_table = _pick_token(extra, "table_id") or template_table
        seeded = run_lark(
            [
                "base",
                "+record-batch-create",
                "--base-token",
                base_token,
                "--table-id",
                template_table,
                "--json",
                json.dumps(
                    {
                        "create_records": [
                            {"标题": row["标题"], "说明": "", "启用": ["是"]}
                            for row in DEFAULT_TEMPLATES
                        ]
                    },
                    ensure_ascii=False,
                ),
            ],
            as_identity="user",
        )
        if not _ok(seeded):
            notes.append("例行模板行没写入：" + format_lark_error(seeded))
    cfg = load_config()
    cfg.update(
        {
            "base_token": base_token,
            "weekly_table": weekly_table,
            "template_table": template_table,
            "scan_tables": list(cfg.get("scan_tables") or []),
        }
    )
    save_config(cfg)
    grant = created.get("data", {}) if isinstance(created.get("data"), dict) else created
    perm = ""
    if isinstance(grant, dict) and grant.get("permission_grant"):
        perm = "\n" + str(grant.get("permission_grant"))
    extra_txt = ("\n" + "\n".join(notes)) if notes else ""
    return (
        f"已建多维表。base={base_token} 本周任务={weekly_table} "
        f"每周例行模板={template_table}。扫缺陷表请把 scan_tables 写进 "
        f"{config_path()}。"
        + perm
        + extra_txt
    )


def _template_records(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    token = str(cfg.get("base_token") or "")
    table = str(cfg.get("template_table") or "")
    if not token or not table:
        return [dict(row) for row in DEFAULT_TEMPLATES]
    payload = run_lark(
        [
            "base",
            "+record-list",
            "--base-token",
            token,
            "--table-id",
            table,
            "--limit",
            "50",
        ],
        as_identity="user",
    )
    records = _records_of(payload)
    rows: list[dict[str, Any]] = []
    for rec in records:
        fields = _fields_of(rec)
        title = str(fields.get("标题") or fields.get("title") or "").strip()
        if title:
            rows.append(fields if "标题" in fields else {"标题": title, "启用": "是"})
    return rows or [dict(row) for row in DEFAULT_TEMPLATES]


def _cell(value: Any) -> Any:
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
        return value[0]
    return value


def _records_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return []
    inner = data.get("data")
    names = data.get("fields")
    ids = data.get("record_id_list")
    if (
        isinstance(inner, list)
        and inner
        and isinstance(inner[0], list)
        and isinstance(names, list)
        and names
    ):
        out: list[dict[str, Any]] = []
        for index, row in enumerate(inner):
            rid = ""
            if isinstance(ids, list) and index < len(ids):
                rid = str(ids[index] or "")
            fields = {
                str(names[col]): _cell(row[col]) if col < len(row) else None
                for col in range(len(names))
            }
            out.append({"record_id": rid, "fields": fields})
        return out
    for key in ("items", "records", "items_list"):
        raw = data.get(key)
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
    if isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    return []


def _fields_of(record: dict[str, Any]) -> dict[str, Any]:
    fields = record.get("fields") or record.get("field_values") or record
    return fields if isinstance(fields, dict) else {}


def _record_id(record: dict[str, Any]) -> str:
    return str(
        record.get("record_id")
        or record.get("id")
        or _fields_of(record).get("record_id")
        or ""
    )


def weekly_tasks_text() -> str:
    cfg = load_config()
    templates = _template_records(cfg)
    rows = weekly_rows(templates, load_items(), datetime.now().date())
    local = "本周任务\n" + "\n".join(
        f"- [{row['类型']}] {row['标题']}" + (f" · {row['对接人']}" if row.get("对接人") else "")
        for row in rows
    )
    if not rows:
        local = "本周任务：例行模板和跟进账都是空的。"
    wrote = ""
    token = str(cfg.get("base_token") or "")
    table = str(cfg.get("weekly_table") or "")
    if token and table and rows:
        payload = run_lark(
            [
                "base",
                "+record-batch-create",
                "--base-token",
                token,
                "--table-id",
                table,
                "--json",
                json.dumps(
                    {
                        "create_records": [
                            {
                                "标题": row["标题"],
                                "类型": [row["类型"]],
                                "对接人": row.get("对接人") or "",
                                "截止": row.get("截止") or "",
                                "状态": [row["状态"]],
                                "来源": row.get("来源") or "",
                            }
                            for row in rows
                        ]
                    },
                    ensure_ascii=False,
                ),
            ],
            as_identity="user",
        )
        if _ok(payload):
            wrote = "已写入多维表「本周任务」。"
        else:
            wrote = "多维表没写上（飞书权威失败，本地清单仍给你）：\n" + format_lark_error(
                payload
            )
    elif not token:
        wrote = "还没建表。先 `feishu followup --setup-tables`。本地清单如下。"
    return wrote + "\n\n" + local


def weekly_once() -> str:
    from ..actions import send_text

    text = weekly_tasks_text()
    sent = send_text(P2P_CHAT_ID, text, as_identity="bot")
    if sent != "已发送。":
        return "私聊没发出：\n" + sent + "\n\n" + text
    return "已私聊本周任务。\n\n" + text


def _load_seen() -> set[str]:
    if not SEEN_PATH.exists():
        return set()
    try:
        data = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if isinstance(data, list):
        return {str(item) for item in data}
    return set()


def _save_seen(seen: set[str]) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(
        json.dumps(sorted(seen), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _who_from_fields(fields: dict[str, Any]) -> str:
    blob = json.dumps(fields, ensure_ascii=False)
    for key in ("提交人", "创建人", "填写人", "name", "姓名"):
        val = fields.get(key)
        if isinstance(val, str) and val.strip() and val.strip() not in USER_NAMES:
            return val.strip()
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, dict):
                name = str(first.get("name") or "").strip()
                if name and name not in USER_NAMES:
                    return name
            elif isinstance(first, str) and first not in USER_NAMES:
                return first
    for name in USER_NAMES:
        blob = blob.replace(name, "")
    return "有人"


def _record_title(fields: dict[str, Any]) -> str:
    for key in ("Bug描述", "标题", "title", "缺陷", "任务", "名称"):
        val = str(fields.get(key) or "").strip()
        if val:
            return val[:80]
    return ""


def scan_bitable(*, today: date | None = None) -> str:
    from ..actions import send_text

    today = today or datetime.now().date()
    cfg = load_config()
    tables = list(cfg.get("scan_tables") or [])
    extra = os.environ.get("FEISHU_PARTNER_SCAN_TABLES") or ""
    for chunk in extra.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            token, table = chunk.split(":", 1)
            tables.append({"base_token": token.strip(), "table": table.strip()})
    if not tables:
        return (
            "没有配置要扫的表。把 scan_tables 写进 ~/.feishu-partner/bitable.json。\n"
            "例：AR101 缺陷表 base=OUDhbl3qZarmnNsMcQPcunNWnZd table=tblwwvOxUAIokY9t"
        )
    seen = _load_seen()
    notes: list[str] = []
    for spec in tables:
        if not isinstance(spec, dict):
            continue
        token = str(spec.get("base_token") or spec.get("app_token") or "")
        table = str(spec.get("table") or spec.get("table_id") or "")
        view_id = str(spec.get("view_id") or spec.get("view") or "")
        label = str(spec.get("name") or spec.get("label") or "多维表")
        lookback = int(spec.get("lookback_days") or 14)
        if not token or not table:
            continue
        argv = [
            "base",
            "+record-list",
            "--base-token",
            token,
            "--table-id",
            table,
            "--limit",
            "200",
        ]
        if view_id:
            argv.extend(["--view-id", view_id])
        payload = run_lark(argv, as_identity="user")
        if not _ok(payload):
            notes.append(format_lark_error(payload))
            continue
        for rec in _records_of(payload):
            rid = _record_id(rec)
            if not rid or rid in seen:
                continue
            fields = _fields_of(rec)
            mentions = record_mentions_user(
                fields, names=USER_NAMES, user_open_id=USER_OPEN_ID
            )
            submitted = record_submit_date(fields)
            within = (
                submitted is None
                or (today - submitted).days <= lookback
            )
            seen.add(rid)
            if not mentions:
                continue
            if not within:
                continue
            who = _who_from_fields(fields)
            title = _record_title(fields)
            line = f"【{label}】{who}指派你了"
            if title:
                line += f"：{title}"
            if submitted:
                line += f"（{submitted.isoformat()}）"
            sent = send_text(P2P_CHAT_ID, line, as_identity="bot")
            if sent != "已发送。":
                notes.append("艾特通知没发出：" + sent)
                continue
            add_bitable_hit(
                who=who, title=title or line, record_id=rid, table=label
            )
            notes.append(line)
    _save_seen(seen)
    return "\n".join(notes) if notes else "这轮表格里没有新的艾特。"


def followup_cli_text(
    *,
    setup: bool,
    digest: bool,
    weekly: bool,
    scan: bool,
    scan_chats: bool = False,
) -> str:
    chunks: list[str] = []
    if setup:
        chunks.append(setup_tables())
    if digest:
        from .followup import push_digest

        chunks.append(push_digest(force=True))
    if weekly:
        chunks.append(weekly_once())
    if scan:
        chunks.append(scan_bitable())
    if scan_chats:
        from .followup import scan_recent_p2p

        n = scan_recent_p2p()
        chunks.append(f"回扫今天的单聊，新记下 {n} 条派活。" if n else "今天单聊里没有新的派活句。")
    if not chunks:
        return digest_text()
    return "\n\n".join(chunks)
