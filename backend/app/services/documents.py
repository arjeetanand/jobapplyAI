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


# ---------------------------------------------------------------------------
# LaTeX writer (uses user's Overleaf template)
# ---------------------------------------------------------------------------

def write_latex(
    path: Path,
    template_source: str,
    user_name: str,
    skills: list[str],
    experience: list[dict],
    projects: list[dict],
    job_title: str,
    company: str,
) -> None:
    """Generate a tailored LaTeX resume from the user's Overleaf template.

    Replaces the Technical Skills, Experience, and Projects sections with
    the AI-tailored content while keeping the rest of the template intact.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    tex = template_source

    # ---- Replace Technical Skills section ----
    skills_block = _latex_skills_block(skills)
    tex = _replace_section(tex, "Technical Skills", skills_block)

    # ---- Replace Experience section ----
    exp_block = _latex_experience_block(experience)
    tex = _replace_section(tex, "Experience", exp_block)

    # ---- Replace Projects section ----
    proj_block = _latex_projects_block(projects)
    tex = _replace_section(tex, "Projects", proj_block)

    path.write_text(tex, encoding="utf-8")


def _replace_section(tex: str, section_name: str, new_content: str) -> str:
    """Replace a \\section{Name}...next section block with new content."""
    import re
    pattern = (
        r"(\\section\{" + re.escape(section_name) + r"\})"
        r"(.*?)"
        r"(?=\\section\{|\\end\{document\})"
    )
    match = re.search(pattern, tex, re.DOTALL)
    if match:
        replacement = match.group(1) + "\n" + new_content + "\n"
        tex = tex[:match.start()] + replacement + tex[match.end():]
    return tex


def _latex_escape(text: str) -> str:
    """Escape special LaTeX characters."""
    replacements = {
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    for char, escaped in replacements.items():
        text = text.replace(char, escaped)
    return text


def _latex_skills_block(skills: list[str]) -> str:
    """Generate the Technical Skills section content."""
    skills_line = ", ".join(_latex_escape(s) for s in skills)
    return (
        r" \begin{itemize}[leftmargin=0.1in, label={}]" "\n"
        r"    \small{\item{" "\n"
        r"    \textbf{Technical Skills:} " + skills_line + r"\\" "\n"
        r"    }}" "\n"
        r" \end{itemize}" "\n"
        r"\vspace{-18pt}"
    )


def _latex_experience_block(experience: list[dict]) -> str:
    """Generate the Experience section content."""
    lines = [r"  \resumeSubHeadingListStart"]
    for exp in experience[:4]:
        company = _latex_escape(exp.get("company", ""))
        role = _latex_escape(exp.get("role", ""))
        duration = _latex_escape(exp.get("duration", ""))
        location = _latex_escape(exp.get("location", ""))
        lines.append(f"    \\resumeSubheading")
        lines.append(f"      {{{company}}}{{{duration}}}")
        lines.append(f"      {{{role}}}{{{location}}}")
        lines.append(r"      \resumeItemListStart")
        for bullet in exp.get("bullets", [])[:5]:
            lines.append(f"        \\resumeItem {{{_latex_escape(bullet)}}}")
        lines.append(r"    \resumeItemListEnd")
        lines.append("")
    lines.append(r"  \resumeSubHeadingListEnd")
    lines.append(r"\vspace{-16pt}")
    return "\n".join(lines)


def _latex_projects_block(projects: list[dict]) -> str:
    """Generate the Projects section content."""
    lines = [r"    \vspace{-6pt}", r"    \resumeSubHeadingListStart"]
    for proj in projects[:5]:
        name = _latex_escape(proj.get("name", ""))
        url = proj.get("url", "")
        gh_link = ""
        if url:
            gh_link = r"{\href{" + url + r"}{\faGithub}}"
        lines.append(f"    \\resumeProjectHeading")
        lines.append(f"          {{\\textbf{{{name}   }}{gh_link}}}{{}}")
        lines.append(r"          \resumeItemListStart")
        summary = proj.get("summary", "")
        if summary:
            for part in summary.split(";"):
                part = part.strip()
                if part:
                    lines.append(f"            \\resumeItem{{{_latex_escape(part)}}}")
        skills_list = proj.get("skills", [])
        if skills_list:
            skills_str = ", ".join(_latex_escape(s) for s in skills_list)
            lines.append(f"            \\resumeItem{{Technologies Used: {skills_str}}}")
        lines.append(r"          \resumeItemListEnd")
        lines.append(r"          \vspace{-14pt}")
        lines.append("")
    lines.append(r"    \resumeSubHeadingListEnd")
    lines.append(r"\vspace{3pt}")
    return "\n".join(lines)

