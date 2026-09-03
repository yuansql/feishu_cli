"""消息图片/文件理解：下载消息附件 → OCR/文本提取 → 给 LLM 当材料。

- 图片：macOS Vision 框架 OCR（中英双语，系统自带，零安装）
- 文本类文件（txt/md/csv/json/代码）：直接读
- docx/xlsx：zipfile + XML 提取文字（stdlib，零依赖）
- 其他格式：如实报告文件名和大小，不假装读懂
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from ..compose.formatters import format_lark_error
from ..core.lark import run_lark

MEDIA_DIR = Path.home() / ".feishu-partner" / "media"
OCR_SCRIPT = Path.home() / ".feishu-partner" / "bin" / "ocr.swift"
MAX_TEXT_CHARS = 4000

_OCR_SWIFT = """import Foundation
import Vision

let args = CommandLine.arguments
guard args.count > 1 else { exit(2) }
let url = URL(fileURLWithPath: args[1])
guard let ciImage = CIImage(contentsOf: url) else { exit(3) }
let request = VNRecognizeTextRequest()
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
let handler = VNImageRequestHandler(ciImage: ciImage)
try? handler.perform([request])
let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
print(lines.joined(separator: "\\n"))
"""

_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".log",
    ".py", ".js", ".ts", ".java", ".go", ".rs", ".sh", ".yaml", ".yml",
    ".toml", ".ini", ".sql", ".html", ".xml",
}


def media_dir() -> Path:
    override = os.environ.get("FEISHU_PARTNER_MEDIA")
    if override:
        return Path(override).expanduser()
    return MEDIA_DIR


def media_ref_from_content(message_type: str, content: Any) -> dict[str, str]:
    """从消息体里拿 file_key / file_name。支持 image / file / media 消息。"""
    mtype = (message_type or "").strip()
    if mtype not in ("image", "file", "media"):
        return {}
    blob: dict[str, Any] = content if isinstance(content, dict) else {}
    if not blob and isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                blob = parsed
        except json.JSONDecodeError:
            return {}
    if not blob:
        return {}
    key = str(blob.get("image_key") or blob.get("file_key") or "").strip()
    if not key:
        return {}
    name = str(blob.get("file_name") or blob.get("name") or "").strip()
    rtype = "image" if (mtype == "image" or key.startswith("img_")) else "file"
    return {"file_key": key, "file_name": name, "rtype": rtype}


def download_resource(message_id: str, file_key: str, rtype: str) -> Path | None:
    """im +messages-resources-download 下载附件到 media 目录。"""
    dest_dir = media_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    payload = run_lark(
        [
            "im",
            "+messages-resources-download",
            "--message-id",
            message_id,
            "--file-key",
            file_key,
            "--type",
            rtype,
            "--output",
            str(dest_dir) + "/",
        ],
        as_identity="user",
        timeout=120,
    )
    if payload.get("ok") is False:
        return None
    # lark-cli 按服务器文件名落盘；找最新写入的文件
    candidates = sorted(
        (p for p in dest_dir.iterdir() if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for cand in candidates[:3]:
        if file_key.split("_")[0] in cand.name or cand.suffix:
            return cand
    return candidates[0] if candidates else None


def _ensure_ocr_script() -> Path | None:
    script = OCR_SCRIPT
    if os.environ.get("FEISHU_PARTNER_OCR_SWIFT"):
        script = Path(os.environ["FEISHU_PARTNER_OCR_SWIFT"]).expanduser()
    if script.exists():
        return script
    if shutil.which("swift") is None:
        return None
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(_OCR_SWIFT, encoding="utf-8")
    return script


def ocr_image(path: Path) -> str:
    """macOS Vision OCR。不可用/失败返回空串。"""
    if shutil.which("swift") is None:
        return ""
    script = _ensure_ocr_script()
    if script is None:
        return ""
    try:
        proc = subprocess.run(
            ["swift", str(script), str(path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()[:MAX_TEXT_CHARS]


def _xml_texts(blob: bytes) -> list[str]:
    """从 Office XML 里抽 <a:t> / <w:t> 文本。"""
    try:
        root = ElementTree.fromstring(blob)
    except ElementTree.ParseError:
        return []
    texts: list[str] = []
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag == "t" and elem.text:
            texts.append(elem.text.strip())
    return [t for t in texts if t]


def extract_office_text(path: Path) -> str:
    """docx/xlsx/pptx：zipfile 读 XML 提文字（stdlib）。"""
    try:
        zf = zipfile.ZipFile(str(path))
    except (OSError, zipfile.BadZipFile):
        return ""
    with zf:
        names = zf.namelist()
        suffix = path.suffix.lower()
        if suffix == ".docx":
            targets = [n for n in names if n == "word/document.xml"]
        elif suffix == ".xlsx":
            targets = [n for n in names if n == "xl/sharedStrings.xml"]
        elif suffix == ".pptx":
            targets = sorted(n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
        else:
            targets = []
        parts: list[str] = []
        for name in targets[:30]:
            try:
                parts.extend(_xml_texts(zf.read(name)))
            except (OSError, KeyError):
                continue
    return " ".join(parts)[:MAX_TEXT_CHARS]


def extract_file_text(path: Path) -> str:
    """按后缀提取文本。提不出来返回空串。"""
    suffix = path.suffix.lower()
    if suffix in _TEXT_EXTS:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:MAX_TEXT_CHARS]
        except OSError:
            return ""
    if suffix in (".docx", ".xlsx", ".pptx"):
        return extract_office_text(path)
    return ""


def understand_media_message(
    message_type: str, content: Any, message_id: str
) -> str:
    """完整链路：识别附件 → 下载 → OCR/提文字 → 返回可给 LLM 的材料文本。"""
    ref = media_ref_from_content(message_type, content)
    if not ref:
        return ""
    path = download_resource(message_id, ref["file_key"], ref["rtype"])
    if path is None:
        return f"（收到{ref['rtype']}附件，但下载失败：{ref['file_key']}）"
    label = ref["file_name"] or path.name
    if ref["rtype"] == "image":
        text = ocr_image(path)
        if text:
            return f"【图片内容】（{label}）\n{text}"
        return f"（收到图片 {label}，OCR 没识别出文字）"
    text = extract_file_text(path)
    if text:
        return f"【文件内容】（{label}）\n{text}"
    size = path.stat().st_size if path.exists() else 0
    return f"（收到文件 {label}，{size // 1024}KB，这种格式暂时读不了）"
