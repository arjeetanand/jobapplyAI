"""Document generation utilities.

write_docx_rich  — python-docx based writer with headings and bullets (ATS-friendly)
write_docx       — lightweight raw XML fallback (no extra dependency)
write_pdf        — minimal PDF-1.4 writer
"""

from __future__ import annotations

import html
import textwrap
import zipfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Rich DOCX writer (python-docx)
# ---------------------------------------------------------------------------

def write_docx_rich(path: Path, title: str, paragraphs: list[str]) -> None:
    """Write a formatted DOCX using python-docx.

    Paragraphs prefixed with ``__HEADING__`` are rendered as bold section
    headers. Lines starting with ``•`` or ``-`` are rendered as bullet items.
    Falls back to write_docx() if python-docx is not installed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        doc = Document()

        # --- Document-level style tweaks ---
        style = doc.styles["Normal"]
        style.font.name = "Calibri"
        style.font.size = Pt(11)

        # Title
        title_para = doc.add_paragraph()
        title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title_para.add_run(title)
        run.bold = True
        run.font.size = Pt(14)

        doc.add_paragraph()  # spacer

        for line in paragraphs:
            if line.startswith("__HEADING__"):
                text = line[len("__HEADING__"):]
                heading_para = doc.add_paragraph()
                run = heading_para.add_run(text.upper())
                run.bold = True
                run.font.size = Pt(11)
                run.font.color.rgb = RGBColor(0x1F, 0x3A, 0x8A)  # dark blue
                # Add a thin rule effect via bottom border (simple approach)
            elif line.startswith("•") or line.startswith("-"):
                bullet_para = doc.add_paragraph(style="List Bullet")
                bullet_para.add_run(line.lstrip("•-").strip())
            else:
                doc.add_paragraph(line)

        doc.save(path)

    except ImportError:
        # Fallback to raw XML writer
        write_docx(path, title, paragraphs)


# ---------------------------------------------------------------------------
# Raw XML DOCX writer (no dependencies)
# ---------------------------------------------------------------------------

def write_docx(path: Path, title: str, paragraphs: list[str]) -> None:
    """Lightweight DOCX writer with no external dependencies."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Strip heading markers for plain XML
    clean = [p.replace("__HEADING__", "") for p in paragraphs]
    body = "".join(
        f"<w:p><w:r><w:t>{html.escape(paragraph)}</w:t></w:r></w:p>"
        for paragraph in [title, *clean]
        if paragraph
    )
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>{body}<w:sectPr /></w:body>
</w:document>"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", content_types)
        docx.writestr("_rels/.rels", rels)
        docx.writestr("word/document.xml", document_xml)


# ---------------------------------------------------------------------------
# PDF writer (no dependencies)
# ---------------------------------------------------------------------------

def write_pdf(path: Path, title: str, paragraphs: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for paragraph in [title, "", *paragraphs]:
        lines.extend(textwrap.wrap(paragraph, width=88) or [""])
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines[:60]]
    text_ops = ["BT", "/F1 11 Tf", "72 760 Td"]
    for index, line in enumerate(escaped):
        if index:
            text_ops.append("0 -15 Td")
        text_ops.append(f"({line}) Tj")
    text_ops.append("ET")
    stream = "\n".join(text_ops).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = [b"%PDF-1.4\n"]
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(sum(len(part) for part in output))
        output.append(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref_offset = sum(len(part) for part in output)
    output.append(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        output.append(f"{offset:010d} 00000 n \n".encode())
    output.append(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    )
    path.write_bytes(b"".join(output))
