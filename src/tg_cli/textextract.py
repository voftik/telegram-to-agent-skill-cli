"""Extract readable text from downloaded attachments.

pdf via pypdf, xlsx via openpyxl; docx/pptx are unzipped and parsed with
the standard library — no heavyweight dependencies. Images are not OCRed:
agents read image files directly with their own vision.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from xml.etree import ElementTree

log = logging.getLogger(__name__)

MAX_CHARS = 1_000_000
_PLAIN_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".json", ".log", ".xml", ".html", ".yaml", ".yml"}

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def extractable(path: Path | str) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in _PLAIN_SUFFIXES or suffix in {".pdf", ".docx", ".pptx", ".xlsx"}


def extract_text(path: Path | str) -> str | None:
    """Return extracted text, or None when the format has no extractor
    or extraction failed. Output is capped at MAX_CHARS."""
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            text = _pdf(path)
        elif suffix == ".docx":
            text = _docx(path)
        elif suffix == ".pptx":
            text = _pptx(path)
        elif suffix == ".xlsx":
            text = _xlsx(path)
        elif suffix in _PLAIN_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="replace")
        else:
            return None
    except Exception as e:
        log.warning("text extraction failed for %s: %s", path, e)
        return None
    if text is None:
        return None
    text = text.strip()
    return text[:MAX_CHARS] if text else None


def _pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _docx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        root = ElementTree.fromstring(zf.read("word/document.xml"))
    paragraphs = []
    for p in root.iter(f"{_W_NS}p"):
        runs = [t.text for t in p.iter(f"{_W_NS}t") if t.text]
        if runs:
            paragraphs.append("".join(runs))
    return "\n".join(paragraphs)


def _pptx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        slide_names = sorted(
            (n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")),
            key=lambda n: int("".join(ch for ch in n if ch.isdigit()) or 0),
        )
        parts = []
        for idx, name in enumerate(slide_names, 1):
            root = ElementTree.fromstring(zf.read(name))
            texts = [t.text for t in root.iter(f"{_A_NS}t") if t.text]
            if texts:
                parts.append(f"--- slide {idx} ---\n" + "\n".join(texts))
    return "\n\n".join(parts)


def _xlsx(path: Path) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(str(path), read_only=True, data_only=True)
    parts = []
    try:
        for ws in wb.worksheets:
            rows = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= 2000:
                    rows.append("… (truncated at 2000 rows)")
                    break
                cells = ["" if c is None else str(c) for c in row]
                if any(cells):
                    rows.append("\t".join(cells))
            if rows:
                parts.append(f"=== {ws.title} ===\n" + "\n".join(rows))
    finally:
        wb.close()
    return "\n\n".join(parts)
