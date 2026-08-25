"""Local HTML / SVG / WAV report artifacts (sandbox-safe). Optional Feishu upload via confirm."""

from __future__ import annotations

import html
import math
import os
import re
import struct
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..core.ids import display_name

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


def _parse_series(raw: str) -> list[tuple[str, float]]:
    """Parse 'A:3,B:5' or 'A=3 B=5' into label/value pairs."""
    blob = (raw or "").strip()
    if not blob:
        return []
    parts = re.split(r"[,，;\s]+", blob)
    out: list[tuple[str, float]] = []
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        if ":" in piece:
            label, value = piece.split(":", 1)
        elif "=" in piece:
            label, value = piece.split("=", 1)
        else:
            continue
        try:
            out.append((label.strip()[:24] or "项", float(value.strip())))
        except ValueError:
            continue
    return out[:12]


def write_chart_svg(*, title: str, series: str) -> Path:
    """Write a simple bar chart SVG under reports/."""
    rows = _parse_series(series)
    if not rows:
        rows = [("示例", 1.0), ("待填", 2.0)]
    reports_dir().mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(CN_TZ).strftime("%Y%m%d-%H%M%S")
    path = reports_dir() / f"{stamp}-{_slug(title or 'chart')}.svg"
    max_v = max(v for _l, v in rows) or 1.0
    width = 480
    height = 40 + 28 * len(rows)
    bars: list[str] = []
    for index, (label, value) in enumerate(rows):
        y = 28 + index * 28
        bar_w = max(2, int(320 * (value / max_v)))
        safe_label = html.escape(label)
        bars.append(
            f'<text x="8" y="{y + 12}" font-size="12" fill="#1f2329">{safe_label}</text>'
            f'<rect x="120" y="{y}" width="{bar_w}" height="16" fill="#3370ff" rx="2"/>'
            f'<text x="{128 + bar_w}" y="{y + 12}" font-size="11" fill="#646a73">{value:g}</text>'
        )
    safe_title = html.escape((title or "图表").strip() or "图表")
    svg = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">\n'
        f'<rect width="100%" height="100%" fill="#fff"/>\n'
        f'<text x="8" y="18" font-size="14" font-weight="600" fill="#1f2329">{safe_title}</text>\n'
        f'{"".join(bars)}\n'
        f"</svg>\n"
    )
    path.write_text(svg, encoding="utf-8")
    return path


def write_tone_wav(*, title: str, seconds: float = 0.4, freq: float = 880.0) -> Path:
    """Write a short mono WAV tone (stdlib only). Not TTS."""
    reports_dir().mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(CN_TZ).strftime("%Y%m%d-%H%M%S")
    path = reports_dir() / f"{stamp}-{_slug(title or 'tone')}.wav"
    rate = 22050
    duration = max(0.1, min(float(seconds or 0.4), 3.0))
    n = int(rate * duration)
    hz = max(110.0, min(float(freq or 880.0), 2000.0))
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            sample = int(12000 * math.sin(2 * math.pi * hz * (i / rate)))
            frames.extend(struct.pack("<h", sample))
        handle.writeframes(frames)
    return path


def chart_from_goal(goal: str, series: str = "") -> str:
    path = write_chart_svg(title=goal or "图表", series=series)
    return f"已生成本地 SVG 图表：\n{path}\n\n浏览器打开预览。上传飞书仍走确认闸。"


def audio_from_goal(goal: str, seconds: str = "0.4") -> str:
    try:
        secs = float(seconds or "0.4")
    except ValueError:
        secs = 0.4
    path = write_tone_wav(title=goal or "提示音", seconds=secs)
    return (
        f"已生成本地 WAV 提示音：\n{path}\n\n"
        "这是短提示音，不是语音合成。上传飞书仍走确认闸。"
    )
