"""Document generation utilities.

write_docx_rich  — python-docx based writer with headings and bullets (ATS-friendly)
write_docx       — lightweight raw XML fallback (no extra dependency)
write_pdf        — minimal PDF-1.4 writer
"""

from __future__ import annotations

import html
import logging
import shutil
import subprocess
import tempfile
import textwrap
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)


def latex_compiler_available() -> bool:
    return any(shutil.which(engine) for engine in ["latexmk", "xelatex", "pdflatex", "lualatex"])


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
    page_width = 612
    page_height = 792
    margin_x = 54
    top_y = 744
    bottom_y = 54
    leading = 14
    content_width = page_width - (margin_x * 2)
    pages: list[list[str]] = [[]]
    y = top_y

    def new_page() -> None:
        nonlocal y
        pages.append([])
        y = top_y

    def ensure_space(height: int = leading) -> None:
        if y - height < bottom_y:
            new_page()

    def escape_text(value: str) -> str:
        clean = value.replace("•", "-")
        clean = clean.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        return clean

    def draw_text(value: str, *, x: int = margin_x, font: str = "F1", size: float = 10.5) -> None:
        pages[-1].append(f"BT /{font} {size:g} Tf {x} {y} Td ({escape_text(value)}) Tj ET")

    def draw_rule() -> None:
        rule_y = y - 4
        pages[-1].append(f"{margin_x} {rule_y} m {page_width - margin_x} {rule_y} l S")

    def wrap(value: str, *, indent: int = 0, size: float = 10.5) -> list[str]:
        chars = max(32, int((content_width - indent) / (size * 0.48)))
        return textwrap.wrap(value.strip(), width=chars, break_long_words=False) or [""]

    ensure_space(30)
    title_lines = wrap(title, size=15)
    for line in title_lines[:2]:
        draw_text(line, x=margin_x, font="F2", size=15)
        y -= 18
    y -= 6

    for raw in paragraphs:
        line = raw.strip()
        if not line:
            y -= 5
            continue

        if line.startswith("__HEADING__"):
            ensure_space(28)
            heading = line[len("__HEADING__"):].strip().upper()
            y -= 5
            draw_text(heading, font="F2", size=10.8)
            draw_rule()
            y -= 16
            continue

        bullet = line.startswith(("•", "-"))
        text = line.lstrip("•-").strip() if bullet else line
        indent = 18 if bullet else 0
        for index, wrapped in enumerate(wrap(text, indent=indent)):
            ensure_space(leading)
            prefix = "- " if bullet and index == 0 else "  " if bullet else ""
            draw_text(f"{prefix}{wrapped}", x=margin_x + indent, size=10.3)
            y -= leading
        if not bullet:
            y -= 2

    page_objects: list[tuple[int, int, bytes]] = []
    next_id = 5
    for page_ops in pages:
        stream = "\n".join(page_ops).encode("latin-1", errors="replace")
        page_objects.append((next_id, next_id + 1, stream))
        next_id += 2

    kids = " ".join(f"{page_id} 0 R" for page_id, _, _ in page_objects)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_objects)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    ]
    for page_id, content_id, stream in page_objects:
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
                f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")

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


def compile_latex_to_pdf(tex_path: Path, pdf_path: Path) -> bool:
    """Compile a LaTeX resume to PDF when a local TeX engine is available."""
    engines: list[list[str]] = []
    if shutil.which("latexmk"):
        engines.append(["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error"])
    for engine in ["xelatex", "pdflatex", "lualatex"]:
        executable = shutil.which(engine)
        if executable:
            engines.append([executable, "-interaction=nonstopmode", "-halt-on-error"])

    if not engines:
        return False

    with tempfile.TemporaryDirectory(prefix="seekapply_latex_") as tmp:
        tmp_path = Path(tmp)
        for engine in engines:
            command = [*engine, "-output-directory", str(tmp_path), tex_path.name]
            try:
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=45,
                    cwd=str(tex_path.parent),
                )
            except Exception as exc:
                logger.warning("LaTeX compilation could not start with %s: %s", engine[0], exc)
                continue
            generated = tmp_path / f"{tex_path.stem}.pdf"
            if result.returncode == 0 and generated.exists():
                pdf_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(generated, pdf_path)
                return True
            logger.warning("LaTeX compilation failed with %s: %s", engine[0], result.stderr[-1000:])
    return False


# ---------------------------------------------------------------------------
# LaTeX writer (uses user's Overleaf template)
# ---------------------------------------------------------------------------

def write_latex(
    path: Path,
    template_source: str | None,
    user_name: str,
    skills: list[str],
    experience: list[dict],
    projects: list[dict],
    job_title: str,
    company: str,
    paragraphs: list[str] | None = None,
) -> None:
    """Generate a tailored LaTeX resume.

    If the user uploaded a LaTeX/Overleaf template, keep that template and
    replace common resume sections. Otherwise, create a compact ATS-friendly
    LaTeX resume so the LaTeX download button always has a real file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    sectioned = _paragraph_sections(paragraphs or [])
    has_user_template = bool(template_source)
    tex = template_source or _default_latex_template(user_name, job_title, company)

    summary_block = _latex_summary_block(
        sectioned.get("professional summary") or sectioned.get("summary") or sectioned.get("preamble") or []
    )

    # Uploaded LaTeX resumes are treated as the formatting source of truth.
    # Tune only the two highest-signal sections so downloads keep the user's
    # original layout, spacing, projects, education, and experience.
    if has_user_template:
        if summary_block:
            tex = _replace_user_template_summary(tex, summary_block)
        paragraph_skills = _skills_from_paragraphs(sectioned)
        tex = _tune_user_template_skills(tex, skills or paragraph_skills)
        path.write_text(tex, encoding="utf-8")
        return

    if summary_block:
        tex = _replace_section(tex, "Professional Summary", summary_block)
        tex = _replace_section(tex, "Summary", summary_block)
        tex = _replace_section(tex, "Profile", summary_block)

    # ---- Replace Technical Skills section ----
    paragraph_skills = _skills_from_paragraphs(sectioned)
    skills_block = _latex_skills_block(skills or paragraph_skills)
    tex = _replace_section(tex, "Technical Skills", skills_block)
    tex = _replace_section(tex, "Skills", skills_block)

    # ---- Replace Experience section for generated fallback templates ----
    if experience:
        exp_block = _latex_plain_experience_block(experience)
    else:
        exp_block = _latex_list_block(sectioned.get("experience", []))
    tex = _replace_section(tex, "Experience", exp_block)

    # ---- Replace Projects section for generated fallback templates ----
    if projects:
        proj_block = _latex_plain_projects_block(projects)
    else:
        proj_block = _latex_list_block(sectioned.get("projects", []))
    tex = _replace_section(tex, "Projects", proj_block)
    tex = _replace_section(tex, "AI Projects", proj_block)

    education_block = _latex_list_block(sectioned.get("education", []))
    if education_block:
        tex = _replace_section(tex, "Education", education_block)

    path.write_text(tex, encoding="utf-8")


def _default_latex_template(user_name: str, job_title: str, company: str) -> str:
    return rf"""\documentclass[10pt,letterpaper]{{article}}
\usepackage[margin=0.55in]{{geometry}}
\usepackage[hidelinks]{{hyperref}}
\usepackage{{enumitem}}
\setlength{{\parindent}}{{0pt}}
\setlist[itemize]{{leftmargin=0.16in,itemsep=1pt,topsep=2pt}}
\begin{{document}}
\begin{{center}}
    {{\Large \textbf{{{_latex_escape(user_name)}}}}}\\
    \small Tailored for {_latex_escape(job_title)} at {_latex_escape(company)}
\end{{center}}
\vspace{{-8pt}}
\section*{{Professional Summary}}
\section*{{Technical Skills}}
\section*{{Experience}}
\section*{{Projects}}
\section*{{Education}}
\end{{document}}
"""


def _replace_section(tex: str, section_name: str, new_content: str) -> str:
    """Replace a \\section{Name} or \\section*{Name} block with new content."""
    import re
    pattern = (
        r"(\\section\*?\{" + re.escape(section_name) + r"\})"
        r"(.*?)"
        r"(?=\\section\*?\{|\\end\{document\})"
    )
    match = re.search(pattern, tex, re.DOTALL)
    if match:
        replacement = match.group(1) + "\n" + new_content + "\n"
        tex = tex[:match.start()] + replacement + tex[match.end():]
    return tex


def _replace_user_template_summary(tex: str, new_content: str) -> str:
    for section_name in ["Professional Summary", "Summary", "Profile"]:
        updated = _replace_section_preserving_wrappers(tex, section_name, new_content)
        if updated != tex:
            return updated
    return tex


def _replace_section_preserving_wrappers(tex: str, section_name: str, new_content: str) -> str:
    """Replace one section body while keeping common resume spacing wrappers."""
    import re

    pattern = (
        r"(\\section\*?\{" + re.escape(section_name) + r"\})"
        r"(.*?)"
        r"(?=\\section\*?\{|\\end\{document\})"
    )
    match = re.search(pattern, tex, re.DOTALL)
    if not match:
        return tex

    block = match.group(2)

    small_pattern = r"(\\small\s*\{)(.*?)(\}\s*(?:\\vspace\{[^}]+\}\s*)?)$"
    small_match = re.search(small_pattern, block, re.DOTALL)
    if small_match:
        replacement_block = (
            block[: small_match.start()]
            + small_match.group(1)
            + "\n"
            + new_content
            + "\n"
            + small_match.group(3)
        )
    else:
        leading_match = re.match(r"(\s*(?:\\vspace\{[^}]+\}\s*)*)", block)
        trailing_match = re.search(r"((?:\s*\\vspace\{[^}]+\})\s*)$", block)
        leading = leading_match.group(1) if leading_match else "\n"
        trailing = trailing_match.group(1) if trailing_match else "\n"
        replacement_block = f"{leading}{new_content}{trailing}"

    replacement = match.group(1) + replacement_block
    return tex[: match.start()] + replacement + tex[match.end():]


def _tune_user_template_skills(tex: str, skills: list[str]) -> str:
    focus_skills = [skill for skill in skills if skill][:10]
    if not focus_skills:
        return tex
    focus = ", ".join(_latex_escape(skill) for skill in focus_skills)
    focus_line = rf"\textbf{{Targeted Focus:}} {focus} \\"

    for section_name in ["Technical Skills", "Skills"]:
        updated = _replace_or_insert_focus_line(tex, section_name, focus_line)
        if updated != tex:
            return updated
    return tex


def _replace_or_insert_focus_line(tex: str, section_name: str, focus_line: str) -> str:
    import re

    pattern = (
        r"(\\section\*?\{" + re.escape(section_name) + r"\})"
        r"(.*?)"
        r"(?=\\section\*?\{|\\end\{document\})"
    )
    match = re.search(pattern, tex, re.DOTALL)
    if not match:
        return tex

    block = match.group(2)
    if r"\textbf{Targeted Focus:}" in block:
        block = re.sub(
            r"\\textbf\{Targeted Focus:\}.*?(?:\\\\)?",
            focus_line,
            block,
            count=1,
            flags=re.DOTALL,
        )
    elif r"\small{\item{" in block:
        block = block.replace(r"\small{\item{", r"\small{\item{" + "\n    " + focus_line + "\n    ", 1)
    elif r"\item{" in block:
        block = block.replace(r"\item{", r"\item{" + "\n    " + focus_line + "\n    ", 1)
    elif r"\item " in block:
        block = re.sub(r"(\\item\s+)", r"\1" + focus_line + "\n    ", block, count=1)
    else:
        block = "\n" + _latex_focus_itemize(focus_line) + block

    replacement = match.group(1) + block
    return tex[: match.start()] + replacement + tex[match.end():]


def _latex_focus_itemize(focus_line: str) -> str:
    return (
        r"\begin{itemize}[leftmargin=0.1in, label={}]" "\n"
        r"    \item " + focus_line + "\n"
        r"\end{itemize}" "\n"
    )


def _paragraph_sections(paragraphs: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = "preamble"
    for raw in paragraphs:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("__HEADING__"):
            current = line[len("__HEADING__"):].strip().lower()
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _skills_from_paragraphs(sections: dict[str, list[str]]) -> list[str]:
    for key in ["skills", "relevant skills", "technical skills"]:
        lines = sections.get(key, [])
        if lines:
            return [item.strip() for item in ",".join(lines).split(",") if item.strip()]
    return []


def _latex_summary_block(lines: list[str]) -> str:
    summary = " ".join(line.strip("•- ") for line in lines if line.strip())
    return _latex_escape(summary) if summary else ""


def _latex_escape(text: str) -> str:
    """Escape special LaTeX characters."""
    normalized = []
    for char in text.replace("\t", " ").replace("•", "-"):
        if ord(char) < 128:
            normalized.append(char)
        elif char in {"–", "—", "−"}:
            normalized.append("-")
        elif char in {"“", "”"}:
            normalized.append('"')
        elif char in {"‘", "’"}:
            normalized.append("'")
    text = "".join(normalized)
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
    if not skills_line:
        skills_line = "Skills available in the uploaded resume"
    return (
        r"\begin{itemize}[leftmargin=0.1in, label={}]" "\n"
        r"    \item \textbf{Technical Skills:} " + skills_line + "\n"
        r"\end{itemize}" "\n"
        r"\vspace{-8pt}"
    )


def _latex_list_block(lines: list[str]) -> str:
    cleaned = [line.strip() for line in lines if line.strip()]
    if not cleaned:
        return ""
    output = [r"\begin{itemize}"]
    for line in cleaned[:12]:
        output.append(r"    \item " + _latex_escape(line.lstrip("•- ")))
    output.append(r"\end{itemize}")
    output.append(r"\vspace{-8pt}")
    return "\n".join(output)


def _latex_plain_experience_block(experience: list[dict]) -> str:
    output: list[str] = []
    for exp in experience[:4]:
        company = _latex_escape(str(exp.get("company", "")))
        role = _latex_escape(str(exp.get("role", "")))
        duration = _latex_escape(str(exp.get("duration", "")))
        location = _latex_escape(str(exp.get("location", "")))
        output.append(r"\textbf{" + role + r"} \hfill " + duration + r"\\")
        output.append(company + (f" -- {location}" if location else "") + "\n")
        bullets = [str(bullet) for bullet in exp.get("bullets", [])[:5]]
        if bullets:
            output.append(r"\begin{itemize}")
            for bullet in bullets:
                output.append(r"    \item " + _latex_escape(bullet))
            output.append(r"\end{itemize}")
    return "\n".join(output)


def _latex_plain_projects_block(projects: list[dict]) -> str:
    output = [r"\begin{itemize}"]
    for proj in projects[:5]:
        name = _latex_escape(str(proj.get("name", "Project")))
        summary = _latex_escape(str(proj.get("summary", "")))
        skills = ", ".join(_latex_escape(str(skill)) for skill in proj.get("skills", []))
        details = summary
        if skills:
            details = f"{details} ({skills})" if details else skills
        output.append(r"    \item \textbf{" + name + r"}: " + details)
    output.append(r"\end{itemize}")
    output.append(r"\vspace{-8pt}")
    return "\n".join(output)


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
