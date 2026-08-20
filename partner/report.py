"""Local HTML report artifacts (sandbox-safe). Optional Feishu doc upload via confirm path."""

from __future__ import annotations

import html
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .ids import display_name

CN_TZ = timezone(timedelta(hours=8))


def reports_dir() -> Path:
    override = os.environ.get("FEISHU_PARTNER_REPORTS")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feishu-partner" / "reports"


def _slug(text: str) -> str:
    raw = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", (text or "").strip())[:40].strip("-")
    return raw or "report"


def render_html(*, title: str, body_md_or_text: str, meta: str = "") -> str:
    safe_title = html.escape((title or "工作报告").strip() or "工作报告")
    lines = []
    for raw in (body_md_or_text or "").splitlines():
        line = raw.rstrip()
        if not line:
            lines.append("<p></p>")
            continue
        if line.startswith("# "):
            lines.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            lines.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("- "):
            lines.append(f"<li>{html.escape(line[2:])}</li>")
        else:
            lines.append(f"<p>{html.escape(line)}</p>")
    # wrap consecutive li
    joined: list[str] = []
    in_ul = False
    for item in lines:
        if item.startswith("<li>"):
            if not in_ul:
                joined.append("<ul>")
                in_ul = True
            joined.append(item)
        else:
            if in_ul:
                joined.append("</ul>")
                in_ul = False
            joined.append(item)
    if in_ul:
        joined.append("</ul>")
    stamp = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")
    owner = html.escape(display_name())
    meta_html = html.escape(meta) if meta else ""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{safe_title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
  max-width: 720px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; color: #1f2329; }}
h1,h2 {{ line-height: 1.3; }}
.meta {{ color: #646a73; font-size: 0.9rem; margin-bottom: 1.5rem; }}
ul {{ padding-left: 1.2rem; }}
footer {{ margin-top: 2rem; color: #8f959e; font-size: 0.85rem; }}
</style>
</head>
<body>
<h1>{safe_title}</h1>
<div class="meta">{owner} · {stamp}{" · " + meta_html if meta_html else ""}</div>
{"".join(joined)}
<footer>由飞书工作伙伴本地生成 · 不对接 Aily</footer>
</body>
</html>
"""


def write_report(*, title: str, body: str, meta: str = "") -> Path:
    reports_dir().mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(CN_TZ).strftime("%Y%m%d-%H%M%S")
    path = reports_dir() / f"{stamp}-{_slug(title)}.html"
    path.write_text(render_html(title=title, body_md_or_text=body, meta=meta), encoding="utf-8")
    return path


def report_from_goal(goal: str, materials: str = "") -> str:
    """Create a local HTML report; return path + tip for upload."""
    title = (goal or "工作报告").strip()[:80] or "工作报告"
    if materials.strip():
        body = f"# {title}\n\n{materials.strip()}"
    else:
        body = f"# {title}\n\n（尚无材料；可先任务模式收集事实后再生成报告。）"
    path = write_report(title=title, body=body)
    return (
        f"已生成本地 HTML 报告：\n{path}\n\n"
        "浏览器打开即可预览。若要写入飞书云文档：任务模式里说「上传飞书」并「确认写入」"
        "（走 docs_create，需确认闸）。"
    )


def report_cli(title_words: list[str] | None = None, body: str = "") -> str:
    title = " ".join(title_words or []).strip() or "工作报告"
    return report_from_goal(title, materials=body)
