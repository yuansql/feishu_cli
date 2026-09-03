"""表格分析技能：取数 → 统计 → 结论 → 图表 → 回写。对标飞书豆包工作「表格」内置技能。

原则：外部权威取数——所有数字直接来自飞书表格，LLM 只做归纳，绝不编造。
LLM 不可用时降级为确定性统计结论，链路永远可用。
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..compose.formatters import format_lark_error
from ..core.lark import run_lark

CN_TZ = timezone(timedelta(hours=8))
CHART_DIR = Path.home() / ".feishu-partner" / "sheet-charts"
_SHEET_TOKEN_RE = re.compile(r"(shtcn[A-Za-z0-9]+|sht[A-Za-z0-9]{10,})")
_RANGE_RE = re.compile(r"^[A-Za-z]{1,3}\d+(:[A-Za-z]{1,3}\d+)?$")
_NUM_STRIP_RE = re.compile(r"[,，\s￥¥$€%％]")


def parse_target(source: str) -> dict[str, str]:
    """解析表格来源：sheets URL / token / bitable spec。

    支持格式：
    - https://xxx/sheets/shtXXX?sheet=abc&range=A1:C10（wiki URL 同理）
    - shtXXX（默认取第一个工作表）
    - shtXXX|sheetId 或 shtXXX!A1:C10
    - bitable:BASE_TOKEN:TABLE_ID（多维表）
    """
    raw = (source or "").strip()
    if not raw:
        return {}
    if raw.lower().startswith(("bitable:", "base:")):
        parts = raw.split(":", 2)
        token = parts[1].strip() if len(parts) > 1 else ""
        table = parts[2].strip() if len(parts) > 2 else ""
        if token and table:
            return {"kind": "bitable", "token": token, "table": table}
        return {}
    if "://" in raw or raw.startswith("www."):
        parsed = urlparse(raw if "://" in raw else "https://" + raw)
        match = _SHEET_TOKEN_RE.search(parsed.path or "")
        if not match:
            return {}
        out = {"kind": "sheets", "token": match.group(1), "sheet_id": "", "range": ""}
        query = parse_qs(parsed.query or "")
        frag = parse_qs((parsed.fragment or "").split("?", 1)[-1])
        for bag in (frag, query):
            sid = (bag.get("sheet") or [""])[0]
            if sid:
                out["sheet_id"] = sid
            rng = (bag.get("range") or [""])[0]
            if rng:
                out["range"] = rng
        return out
    # 裸 token，可能带 |sheetId 或 !range
    out = {"kind": "sheets", "token": "", "sheet_id": "", "range": ""}
    for chunk in re.split(r"[|\s]+", raw):
        chunk = chunk.strip()
        if not chunk:
            continue
        if _SHEET_TOKEN_RE.fullmatch(chunk):
            out["token"] = chunk
        elif "!" in chunk:
            head, rng = chunk.split("!", 1)
            if _SHEET_TOKEN_RE.fullmatch(head.strip()):
                out["token"] = head.strip()
            else:
                out["sheet_id"] = head.strip()
            if _RANGE_RE.match(rng.strip()):
                out["range"] = rng.strip()
        elif chunk.startswith("sheetId="):
            out["sheet_id"] = chunk.split("=", 1)[1]
        elif _RANGE_RE.match(chunk):
            out["range"] = chunk
    return out if out["token"] else {}


def _ok(payload: dict[str, Any]) -> bool:
    if payload.get("ok") is False:
        return False
    return not payload.get("error")


def _sheets_first_id(payload: dict[str, Any]) -> tuple[str, str]:
    """从 +info 结果里拿第一个工作表 (sheet_id, title)。"""
    stack: list[Any] = [payload, payload.get("data")]
    sheets: list[dict[str, Any]] = []
    while stack:
        cur = stack.pop(0)
        if not isinstance(cur, dict):
            continue
        raw = cur.get("sheets")
        if isinstance(raw, list):
            sheets = [s for s in raw if isinstance(s, dict)]
            break
        for key in ("data", "spreadsheet"):
            if isinstance(cur.get(key), (dict, list)):
                stack.append(cur[key])
    for sheet in sheets:
        sid = str(sheet.get("sheet_id") or sheet.get("id") or "")
        if sid:
            return sid, str(sheet.get("title") or "")
    return "", ""


def _sheets_values(payload: dict[str, Any]) -> tuple[list[list[Any]], bool]:
    """从 +read 结果里拿 values 二维数组 + truncated 标记。"""
    stack: list[Any] = [payload, payload.get("data")]
    while stack:
        cur = stack.pop(0)
        if not isinstance(cur, dict):
            continue
        raw = cur.get("values")
        if isinstance(raw, list):
            truncated = bool(cur.get("truncated"))
            return [row if isinstance(row, list) else [] for row in raw], truncated
        if isinstance(cur.get("data"), dict):
            stack.append(cur["data"])
    return [], False


def _cell_value(val: Any) -> Any:
    if isinstance(val, list) and len(val) == 1 and isinstance(val[0], (str, int, float)):
        return val[0]
    if isinstance(val, dict):
        return str(val.get("name") or val.get("text") or val.get("url") or "").strip()
    return val


def _bitable_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """兼容 base +record-list 的两种返回形状（rows+fields 名单 / items dict 列表）。"""
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
                str(names[col]): _cell_value(row[col]) if col < len(row) else None
                for col in range(len(names))
            }
            out.append({"record_id": rid, "fields": fields})
        return out
    for key in ("items", "records", "items_list"):
        raw = data.get(key)
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
    return []


def fetch_table(source: str, *, limit: int = 500) -> dict[str, Any]:
    """取数：sheets 或 bitable → {"headers", "rows", "truncated", ...}。"""
    target = parse_target(source)
    if not target:
        return {
            "error": (
                "没认出表格来源。支持：飞书表格 URL、sht 开头的 token、"
                "或 bitable:BASE_TOKEN:TABLE_ID。"
            )
        }
    if target["kind"] == "bitable":
        payload = run_lark(
            [
                "base",
                "+record-list",
                "--base-token",
                target["token"],
                "--table-id",
                target["table"],
                "--limit",
                str(max(1, min(limit, 1000))),
            ],
            as_identity="user",
        )
        if not _ok(payload):
            return {"error": "读多维表失败：\n" + format_lark_error(payload)}
        records = _bitable_records(payload)
        headers = sorted({key for rec in records for key in (rec.get("fields") or {})})
        rows: list[list[Any]] = []
        for rec in records:
            fields = rec.get("fields") or {}
            rows.append([fields.get(name) for name in headers])
        return {
            "kind": "bitable",
            "token": target["token"],
            "title": f"多维表 {target['table'][:12]}",
            "headers": headers,
            "rows": rows,
            "truncated": len(records) >= limit,
        }
    token = target["token"]
    sheet_id = target.get("sheet_id") or ""
    rng = target.get("range") or ""
    title = f"表格 {token[:12]}"
    if not sheet_id:
        info = run_lark(["sheets", "+info", "--spreadsheet-token", token], as_identity="user")
        if not _ok(info):
            return {"error": "读表格信息失败：\n" + format_lark_error(info)}
        sheet_id, sheet_title = _sheets_first_id(info)
        if sheet_title:
            title = sheet_title
        if not sheet_id:
            return {"error": "这个表格里没有工作表。"}
    argv = [
        "sheets",
        "+read",
        "--spreadsheet-token",
        token,
        "--sheet-id",
        sheet_id,
    ]
    if rng:
        argv.extend(["--range", rng])
    else:
        argv.extend(["--range", "A1:Z2000"])
    payload = run_lark(argv, as_identity="user")
    if not _ok(payload):
        return {"error": "读表格失败：\n" + format_lark_error(payload)}
    values, truncated = _sheets_values(payload)
    if not values:
        return {"error": "表里没有读到数据。"}
    headers = [str(cell if cell is not None else "") for cell in values[0]]
    rows = values[1:]
    # 补齐空表头
    for i in range(len(headers)):
        if not headers[i].strip():
            headers[i] = f"列{chr(ord('A') + i) if i < 26 else i + 1}"
    return {
        "kind": "sheets",
        "token": token,
        "sheet_id": sheet_id,
        "title": title,
        "headers": headers,
        "rows": rows,
        "truncated": truncated,
    }


def coerce_number(val: Any) -> float | None:
    """把单元格值转成数字；'1,234' / '¥12.5' / '45%' 可转，日期/文本返回 None。"""
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s:
        return None
    neg = s[0] in "-－−"
    cleaned = _NUM_STRIP_RE.sub("", s.lstrip("+-＋－"))
    if not cleaned:
        return None
    if not re.fullmatch(r"\d+(\.\d+)?", cleaned):
        return None
    num = float(cleaned)
    return -num if neg else num


def column_profiles(headers: list[str], rows: list[list[Any]]) -> list[dict[str, Any]]:
    """每列的类型推断与基础统计。数值列给 count/sum/mean/min/max。"""
    profiles: list[dict[str, Any]] = []
    for index, name in enumerate(headers):
        values = [row[index] for row in rows if index < len(row)]
        non_empty = [v for v in values if v is not None and str(v).strip() != ""]
        nums = [n for n in (coerce_number(v) for v in non_empty) if n is not None]
        numeric = len(nums) >= 3 and len(nums) >= 0.7 * max(1, len(non_empty))
        distinct = {str(v) for v in non_empty}
        profile: dict[str, Any] = {
            "name": name,
            "kind": "numeric" if numeric else "text",
            "non_empty": len(non_empty),
            "missing": len(values) - len(non_empty),
            "distinct": len(distinct),
        }
        if numeric and nums:
            profile.update(
                {
                    "count": len(nums),
                    "sum": sum(nums),
                    "mean": sum(nums) / len(nums),
                    "min": min(nums),
                    "max": max(nums),
                }
            )
        profiles.append(profile)
    return profiles


def group_stats(
    headers: list[str], rows: list[list[Any]], group_col: str, value_col: str
) -> list[dict[str, Any]]:
    """按 group_col 分组聚合 value_col：count / sum / mean，按 sum 降序。"""
    if group_col not in headers or value_col not in headers:
        return []
    gi = headers.index(group_col)
    vi = headers.index(value_col)
    buckets: dict[str, list[float]] = {}
    for row in rows:
        label = str(_cell_value(row[gi]) if gi < len(row) else "").strip() or "（空）"
        num = coerce_number(row[vi]) if vi < len(row) else None
        if num is None:
            continue
        buckets.setdefault(label, []).append(num)
    out = [
        {
            "label": label,
            "count": len(nums),
            "sum": sum(nums),
            "mean": sum(nums) / len(nums),
        }
        for label, nums in buckets.items()
    ]
    return sorted(out, key=lambda item: item["sum"], reverse=True)


_VALUE_HINTS = (
    "数量",
    "金额",
    "分数",
    "时长",
    "次数",
    "销量",
    "工时",
    "单价",
    "总分",
    "score",
    "amount",
    "count",
)
_GROUP_HINTS = (
    "区域",
    "部门",
    "分类",
    "类型",
    "状态",
    "组",
    "城市",
    "渠道",
    "项目",
    "负责人",
    "category",
    "type",
    "status",
    "group",
)


def pick_chart_series(
    headers: list[str], rows: list[list[Any]], profiles: list[dict[str, Any]]
) -> tuple[str, str, list[dict[str, Any]]]:
    """启发式选图：低基数文本列 × 数值列 → 分组柱状图。选不出返回空。"""
    text_cols = [
        p
        for p in profiles
        if p["kind"] == "text" and 2 <= p["distinct"] <= 12 and p["non_empty"] > 0
    ]
    num_cols = [p for p in profiles if p["kind"] == "numeric" and p.get("count")]
    if not text_cols or not num_cols:
        return "", "", []

    def _value_score(p: dict[str, Any]) -> tuple[int, float]:
        hint = 1 if any(h in p["name"].lower() for h in _VALUE_HINTS) else 0
        return (hint, float(p.get("count") or 0))

    def _group_score(p: dict[str, Any]) -> tuple[int, int, int]:
        hint = 1 if any(h in p["name"].lower() for h in _GROUP_HINTS) else 0
        return (hint, p["non_empty"], -abs(p["distinct"] - 5))

    value_col = max(num_cols, key=_value_score)["name"]
    group_col = max(text_cols, key=_group_score)["name"]
    groups = group_stats(headers, rows, group_col, value_col)
    if len(groups) < 2:
        return "", "", []
    return group_col, value_col, groups


def _fmt_num(val: float) -> str:
    if val == int(val) and abs(val) < 1e12:
        return f"{int(val):,}"
    return f"{val:,.1f}"


def build_material(
    table: dict[str, Any],
    profiles: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    group_col: str,
    value_col: str,
) -> str:
    """把统计结果整理成给 LLM 的材料文本。"""
    headers = table["headers"]
    rows = table["rows"]
    lines = [
        f"【表格】{table.get('title', '')}（{len(rows)} 行 × {len(headers)} 列"
        + ("，数据被截断" if table.get("truncated") else "")
        + "）",
        "【列统计】",
    ]
    for p in profiles:
        if p["kind"] == "numeric":
            lines.append(
                f"- {p['name']}（数值）：非空 {p['non_empty']}，总和 {_fmt_num(p['sum'])}，"
                f"均值 {_fmt_num(p['mean'])}，最小 {_fmt_num(p['min'])}，最大 {_fmt_num(p['max'])}"
            )
        else:
            lines.append(
                f"- {p['name']}（文本）：非空 {p['non_empty']}，去重 {p['distinct']} 种取值"
            )
    if groups:
        lines.append(f"【分组统计】按「{group_col}」聚合「{value_col}」：")
        for g in groups[:12]:
            lines.append(
                f"- {g['label']}：{g['count']} 条，合计 {_fmt_num(g['sum'])}，均值 {_fmt_num(g['mean'])}"
            )
    sample = ["【前 5 行样例】"]
    for row in rows[:5]:
        cells = [
            f"{headers[i]}={_cell_value(row[i]) if i < len(row) else ''}"
            for i in range(min(len(headers), 10))
        ]
        sample.append("- " + "；".join(cells))
    return "\n".join(lines + sample)


def _conclusion(question: str, material: str) -> str:
    """LLM 归纳结论；失败/禁用时返回空串走确定性降级。"""
    if os.environ.get("FEISHU_PARTNER_NO_LLM") == "1":
        return ""
    from ..compose.llm import _invoke_hermes

    prompt = (
        "你是飞书里的表格分析助手。根据材料用中文写分析结论。\n"
        "结构：先一两句直接回答用户问题（如有），再给 3-5 条要点（必须引用材料里的具体数字），"
        "最后一条给可执行的下一步建议。\n"
        "只准使用材料里出现的数字，禁止编造或推算材料外的事实；不要解释你是 AI；不要输出思考过程。\n\n"
        f"【用户问题】{question or '（无，先整体概括这张表）'}\n\n{material[:9000]}"
    )
    raw = _invoke_hermes(prompt, timeout=90)
    text = (raw or "").strip()
    if not text or "Error:" in text[:80] or len(text) < 12 or len(text) > 4000:
        return ""
    return text


def fallback_conclusion(
    table: dict[str, Any],
    profiles: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    group_col: str,
    value_col: str,
) -> str:
    """无 LLM 时的确定性结论：只陈述算得出的数字。"""
    lines = [f"这张表共 {len(table['rows'])} 行 {len(table['headers'])} 列。"]
    for p in profiles:
        if p["kind"] == "numeric":
            lines.append(
                f"- 「{p['name']}」合计 {_fmt_num(p['sum'])}，均值 {_fmt_num(p['mean'])}，"
                f"范围 {_fmt_num(p['min'])} ~ {_fmt_num(p['max'])}。"
            )
    if groups:
        top = groups[0]
        lines.append(
            f"- 按「{group_col}」看「{value_col}」：最高是「{top['label']}」"
            f"（{_fmt_num(top['sum'])}，{top['count']} 条），共 {len(groups)} 组。"
        )
        if len(groups) > 1:
            low = groups[-1]
            lines.append(f"- 最低是「{low['label']}」（{_fmt_num(low['sum'])}）。")
    lines.append("- 建议：对最高组复核原因，对最低组确认是否有异常或漏填。")
    return "\n".join(lines)


def write_chart_svg(
    *, title: str, group_col: str, value_col: str, groups: list[dict[str, Any]]
) -> Path:
    """纯 Python 生成横向柱状图 SVG（零依赖，可测试）。"""
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(CN_TZ).strftime("%Y%m%d-%H%M%S")
    path = CHART_DIR / f"{stamp}-chart.svg"
    rows = groups[:12]
    max_v = max((g["sum"] for g in rows), default=1.0) or 1.0
    width = 560
    bar_x = 150
    bar_max = 330
    height = 46 + 30 * len(rows)
    parts: list[str] = []
    for index, g in enumerate(rows):
        y = 40 + index * 30
        bar_w = max(2, int(bar_max * (g["sum"] / max_v)))
        label = html.escape(str(g["label"])[:10])
        parts.append(
            f'<text x="8" y="{y + 13}" font-size="12" fill="#1f2329">{label}</text>'
            f'<rect x="{bar_x}" y="{y}" width="{bar_w}" height="18" fill="#3370ff" rx="3"/>'
            f'<text x="{bar_x + bar_w + 6}" y="{y + 13}" font-size="11" fill="#646a73">'
            f"{_fmt_num(g['sum'])}（{g['count']}条）</text>"
        )
    safe_title = html.escape((title or "图表").strip() or "图表")
    safe_axis = html.escape(f"{group_col} → {value_col}（按合计）")
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">\n'
        '<rect width="100%" height="100%" fill="#ffffff"/>\n'
        f'<text x="8" y="20" font-size="14" font-weight="600" fill="#1f2329">{safe_title}</text>\n'
        f'<text x="8" y="34" font-size="11" fill="#8f959e">{safe_axis}</text>\n'
        + "".join(parts)
        + "\n</svg>\n"
    )
    path.write_text(svg, encoding="utf-8")
    return path


def svg_to_png(svg_path: Path) -> Path | None:
    """macOS qlmanage 把 SVG 缩略成 PNG（发飞书图片用）。失败返回 None。"""
    if shutil.which("qlmanage") is None:
        return None
    try:
        proc = subprocess.run(
            ["qlmanage", "-t", "-s", "1400", "-o", str(svg_path.parent), str(svg_path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    cand = svg_path.parent / (svg_path.name + ".png")
    return cand if cand.exists() and cand.stat().st_size > 0 else None


def send_chart_image(chat_id: str, image_path: Path) -> str:
    """发图片消息；本地文件由 lark-cli 自动上传。"""
    from .messaging import _send_args

    payload = run_lark(
        ["im", "+messages-send", *_send_args(chat_id), "--image", str(image_path)],
        as_identity="bot",
    )
    if payload.get("ok"):
        return "已发送。"
    return format_lark_error(payload)


def write_back(token: str, block_rows: list[list[Any]]) -> str:
    """回写：在源表格里建「分析结果」工作表并写入统计块。"""
    created = run_lark(
        [
            "sheets",
            "+create-sheet",
            "--spreadsheet-token",
            token,
            "--title",
            "分析结果",
        ],
        as_identity="user",
    )
    if not _ok(created):
        return "建「分析结果」工作表失败：\n" + format_lark_error(created)
    sheet_id = ""
    stack: list[Any] = [created, created.get("data")]
    while stack and not sheet_id:
        cur = stack.pop(0)
        if not isinstance(cur, dict):
            continue
        for key in ("sheet", "sheets"):
            raw = cur.get(key)
            if isinstance(raw, dict) and str(raw.get("sheet_id") or ""):
                sheet_id = str(raw["sheet_id"])
                break
            if isinstance(raw, list) and raw and isinstance(raw[0], dict):
                sheet_id = str(raw[0].get("sheet_id") or "")
                break
        for key in ("data", "spreadsheet"):
            if isinstance(cur.get(key), (dict, list)):
                stack.append(cur[key])
    if not sheet_id:
        return "工作表建了但没拿到 sheet_id，回写中止。"
    payload = run_lark(
        [
            "sheets",
            "+write",
            "--spreadsheet-token",
            token,
            "--sheet-id",
            sheet_id,
            "--range",
            "A1",
            "--values",
            json.dumps(block_rows, ensure_ascii=False),
        ],
        as_identity="user",
    )
    if not _ok(payload):
        return "统计结果写入失败：\n" + format_lark_error(payload)
    return f"已回写「分析结果」工作表（{len(block_rows) - 1} 行统计）。"


def _write_back_block(
    profiles: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    group_col: str,
    value_col: str,
) -> list[list[Any]]:
    block: list[list[Any]] = [["列", "类型", "非空", "总和", "均值", "最小", "最大"]]
    for p in profiles:
        block.append(
            [
                p["name"],
                p["kind"],
                p["non_empty"],
                _fmt_num(p["sum"]) if p["kind"] == "numeric" else "",
                _fmt_num(p["mean"]) if p["kind"] == "numeric" else "",
                _fmt_num(p["min"]) if p["kind"] == "numeric" else "",
                _fmt_num(p["max"]) if p["kind"] == "numeric" else "",
            ]
        )
    if groups:
        block.append([])
        block.append([f"{group_col} → {value_col}", "条数", "合计", "均值"])
        for g in groups:
            block.append([g["label"], g["count"], _fmt_num(g["sum"]), _fmt_num(g["mean"])])
    return block


def analyze(
    source: str,
    *,
    question: str = "",
    write_back_flag: bool = False,
    chat_id: str = "",
    with_chart: bool = True,
) -> str:
    """完整链路：取数 → 统计 → 结论 → 图表 → 回写。"""
    table = fetch_table(source)
    if table.get("error"):
        return str(table["error"])
    headers = table["headers"]
    rows = table["rows"]
    if not rows:
        return f"「{table.get('title', '')}」只有表头没有数据行。"
    profiles = column_profiles(headers, rows)
    group_col, value_col, groups = pick_chart_series(headers, rows, profiles)
    material = build_material(table, profiles, groups, group_col, value_col)
    conclusion = _conclusion(question, material) or fallback_conclusion(
        table, profiles, groups, group_col, value_col
    )
    parts = [f"【{table.get('title', '表格')}·分析】", conclusion]
    if with_chart and groups:
        svg = write_chart_svg(
            title=str(table.get("title") or "图表"),
            group_col=group_col,
            value_col=value_col,
            groups=groups,
        )
        parts.append(f"图表：{svg}")
        png = svg_to_png(svg)
        if png and chat_id:
            sent = send_chart_image(chat_id, png)
            parts.append(f"图表消息：{sent}")
    if write_back_flag:
        if table.get("kind") != "sheets":
            parts.append("多维表暂不支持自动回写，统计结果见上文。")
        else:
            parts.append(
                write_back(
                    str(table["token"]), _write_back_block(profiles, groups, group_col, value_col)
                )
            )
    return "\n\n".join(parts)


def sheet_analysis_cli(argv: list[str]) -> int:
    """CLI 入口：feishu sheet-analyze <url|token> [options]"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="feishu sheet-analyze", description="表格分析：取数→统计→结论→图表→回写"
    )
    parser.add_argument("source", help="飞书表格 URL / sht token / bitable:base:table")
    parser.add_argument("--question", "-q", default="", help="要回答的问题")
    parser.add_argument("--write-back", action="store_true", help="把统计结果回写成新工作表")
    parser.add_argument("--chat-id", default="", help="发图表图片到指定会话")
    parser.add_argument("--no-chart", action="store_true", help="不生成图表")
    args = parser.parse_args(argv)
    print(
        analyze(
            args.source,
            question=args.question,
            write_back_flag=args.write_back,
            chat_id=args.chat_id,
            with_chart=not args.no_chart,
        )
    )
    return 0
