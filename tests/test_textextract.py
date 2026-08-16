"""Tests for textextract — real files built in-test, no fixtures on disk."""

from __future__ import annotations

import zipfile

from tg_cli.textextract import extract_text, extractable

_DOCX_XML = """<?xml version="1.0"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Первый абзац отчёта.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Второй </w:t></w:r><w:r><w:t>абзац.</w:t></w:r></w:p>
  </w:body>
</w:document>"""

_SLIDE_XML = """<?xml version="1.0"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree>
    <p:sp><p:txBody><a:p><a:r><a:t>Заголовок слайда</a:t></a:r></a:p></p:txBody></p:sp>
  </p:spTree></p:cSld>
</p:sld>"""

def _mini_pdf() -> bytes:
    """Build a minimal valid one-page PDF with a text object and real xref."""
    stream = b"BT /F1 24 Tf 72 720 Td (Hello PDF) Tj ET"
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n%s\nendstream" % (len(stream), stream),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)


class TestExtractText:
    def test_docx(self, tmp_path):
        p = tmp_path / "отчёт.docx"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("word/document.xml", _DOCX_XML)
        text = extract_text(p)
        assert text == "Первый абзац отчёта.\nВторой абзац."

    def test_pptx(self, tmp_path):
        p = tmp_path / "деки.pptx"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("ppt/slides/slide1.xml", _SLIDE_XML)
        text = extract_text(p)
        assert "slide 1" in text
        assert "Заголовок слайда" in text

    def test_xlsx(self, tmp_path):
        from openpyxl import Workbook

        p = tmp_path / "таблица.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "Смета"
        ws.append(["Позиция", "Сумма"])
        ws.append(["Дизайн", 100500])
        wb.save(p)
        text = extract_text(p)
        assert "=== Смета ===" in text
        assert "Позиция\tСумма" in text
        assert "Дизайн\t100500" in text

    def test_pdf(self, tmp_path):
        p = tmp_path / "doc.pdf"
        p.write_bytes(_mini_pdf())
        text = extract_text(p)
        assert text is not None
        assert "Hello PDF" in text

    def test_plain_csv(self, tmp_path):
        p = tmp_path / "data.csv"
        p.write_text("a,b\n1,2\n", encoding="utf-8")
        assert extract_text(p) == "a,b\n1,2"

    def test_unknown_format(self, tmp_path):
        p = tmp_path / "video.mp4"
        p.write_bytes(b"\x00\x01")
        assert extract_text(p) is None
        assert extractable(p) is False

    def test_corrupt_docx_returns_none(self, tmp_path):
        p = tmp_path / "broken.docx"
        p.write_bytes(b"not a zip at all")
        assert extract_text(p) is None
