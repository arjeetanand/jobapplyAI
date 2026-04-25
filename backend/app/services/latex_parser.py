"""Parse a LaTeX resume source into structured profile data.

This module extracts experience, projects, education, skills, and personal
information from a LaTeX resume that uses common resume template macros
like \\resumeSubheading, \\resumeProjectHeading, \\resumeItem, etc.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ParsedExperience:
    company: str
    role: str
    duration: str
    location: str
    bullets: list[str] = field(default_factory=list)


@dataclass
class ParsedProject:
    name: str
    summary: str
    skills: list[str] = field(default_factory=list)
    url: str | None = None


@dataclass
class ParsedEducation:
    institution: str
    degree: str
    duration: str
    score: str


@dataclass
class ParsedResume:
    name: str
    phone: str | None
    email: str | None
    linkedin_url: str | None
    github_url: str | None
    skills: list[str]
    experience: list[ParsedExperience]
    projects: list[ParsedProject]
    education: list[ParsedEducation]
    achievements: list[str]
    positions_of_responsibility: list[dict]


def _strip_latex(text: str) -> str:
    """Remove common LaTeX commands and return plain text."""
    # Remove \textbf{...}, \textit{...}, \small{...}, etc.
    text = re.sub(r"\\(?:textbf|textit|small|scshape|Huge|large|bfseries)\{([^}]*)\}", r"\1", text)
    # Remove \href{url}{text} -> text
    text = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", text)
    # Remove font-awesome icons
    text = re.sub(r"\\fa\w+(?:\{[^}]*\})?", "", text)
    text = re.sub(r"\\faIcon\{[^}]*\}", "", text)
    text = re.sub(r"\\raisebox\{[^}]*\}", "", text)
    # Remove remaining simple commands
    text = re.sub(r"\\[a-zA-Z]+\*?\s*", "", text)
    # Remove braces
    text = text.replace("{", "").replace("}", "")
    # Clean whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_href(text: str, domain: str) -> str | None:
    """Extract a URL from \\href{url}{...} matching a domain."""
    match = re.search(rf"\\href\{{(https?://[^}}]*{re.escape(domain)}[^}}]*)\}}", text)
    return match.group(1) if match else None


def parse_latex_resume(source: str) -> ParsedResume:
    """Parse a LaTeX resume source string into structured data."""

    # --- Name ---
    name_match = re.search(r"\\Huge\s*\\scshape\s*([^}\\]+)", source)
    name = name_match.group(1).strip() if name_match else "Unknown"

    # --- Phone ---
    phone_match = re.search(r"\\faPhone[^}]*\}\s*([0-9\s\-+()]+)", source)
    if not phone_match:
        phone_match = re.search(r"(\d{10})", source[:800])
    phone = phone_match.group(1).strip() if phone_match else None

    # --- Email ---
    email_match = re.search(r"\\href\{mailto:([^}]+)\}", source)
    email = email_match.group(1).strip() if email_match else None

    # --- LinkedIn ---
    linkedin_url = _extract_href(source, "linkedin.com")

    # --- GitHub ---
    github_url = _extract_href(source, "github.com")

    # --- Skills ---
    skills_section = re.search(
        r"\\section\{Technical Skills\}(.*?)(?=\\section\{|\\end\{document\})",
        source, re.DOTALL
    )
    skills: list[str] = []
    if skills_section:
        block = skills_section.group(1)
        # Extract from "Programming Languages:" C, C++, Python, ...
        for line in block.split("\\\\"):
            cleaned = _strip_latex(line)
            # Split on common delimiters
            if ":" in cleaned:
                _, _, values = cleaned.partition(":")
                skills.extend(s.strip() for s in values.split(",") if s.strip() and len(s.strip()) > 1)
            else:
                skills.extend(s.strip() for s in cleaned.split(",") if s.strip() and len(s.strip()) > 1)

    # --- Experience ---
    experience: list[ParsedExperience] = []
    exp_section = re.search(
        r"\\section\{Experience\}(.*?)(?=\\section\{|$)",
        source, re.DOTALL
    )
    if exp_section:
        block = exp_section.group(1)
        # Find all \resumeSubheading{Company}{Duration}{Role}{Location}
        subheadings = re.findall(
            r"\\resumeSubheading\s*\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}",
            block
        )
        # Split block by subheadings to get bullets for each
        parts = re.split(r"\\resumeSubheading", block)[1:]  # skip preamble
        for i, (company, duration, role, location) in enumerate(subheadings):
            bullets: list[str] = []
            if i < len(parts):
                items = re.findall(r"\\resumeItem\s*\{([^}]+)\}", parts[i])
                bullets = [_strip_latex(item) for item in items]
            experience.append(ParsedExperience(
                company=_strip_latex(company),
                role=_strip_latex(role),
                duration=_strip_latex(duration),
                location=_strip_latex(location),
                bullets=bullets,
            ))

    # --- Projects ---
    projects: list[ParsedProject] = []
    proj_section = re.search(
        r"\\section\{Projects\}(.*?)(?=\\section\{|$)",
        source, re.DOTALL
    )
    if proj_section:
        block = proj_section.group(1)
        # Find \resumeProjectHeading{\textbf{Name ...}}{Date}
        proj_headings = re.findall(
            r"\\resumeProjectHeading\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}\s*\{([^}]*)\}",
            block
        )
        parts = re.split(r"\\resumeProjectHeading", block)[1:]
        for i, (heading_raw, _date) in enumerate(proj_headings):
            proj_name = _strip_latex(heading_raw).strip()
            # Extract GitHub URL if present
            proj_url = _extract_href(heading_raw, "github.com")
            # Get items
            proj_bullets: list[str] = []
            proj_skills: list[str] = []
            if i < len(parts):
                items = re.findall(r"\\resumeItem\s*\{([^}]+)\}", parts[i])
                for item in items:
                    clean = _strip_latex(item)
                    proj_bullets.append(clean)
                    # Extract "Technologies Used: ..." line
                    tech_match = re.search(r"Technologies Used:\s*(.+)", clean)
                    if tech_match:
                        proj_skills = [s.strip() for s in tech_match.group(1).split(",") if s.strip()]

            projects.append(ParsedProject(
                name=proj_name,
                summary="; ".join(proj_bullets[:2]) if proj_bullets else "",
                skills=proj_skills,
                url=proj_url,
            ))

    # --- Education ---
    education: list[ParsedEducation] = []
    edu_section = re.search(
        r"\\section\{Education\}(.*?)(?=\\section\{|$)",
        source, re.DOTALL
    )
    if edu_section:
        block = edu_section.group(1)
        edu_headings = re.findall(
            r"\\resumeSubheading\s*\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}",
            block
        )
        for inst, duration, degree, score in edu_headings:
            education.append(ParsedEducation(
                institution=_strip_latex(inst),
                degree=_strip_latex(degree),
                duration=_strip_latex(duration),
                score=_strip_latex(score),
            ))

    # --- Achievements ---
    achievements: list[str] = []
    ach_section = re.search(
        r"\\section\{Achievement\}(.*?)(?=\\section\{|$)",
        source, re.DOTALL
    )
    if ach_section:
        items = re.findall(r"\\resumeItem\s*\{([^}]+)\}", ach_section.group(1))
        achievements = [_strip_latex(item) for item in items]

    # --- Positions of Responsibility ---
    por: list[dict] = []
    por_section = re.search(
        r"\\section\{Position of Responsibility\}(.*?)(?=\\section\{|\\end\{document\})",
        source, re.DOTALL
    )
    if por_section:
        por_headings = re.findall(
            r"\\resumeProjectHeading\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}\s*\{([^}]*)\}",
            por_section.group(1)
        )
        por_parts = re.split(r"\\resumeProjectHeading", por_section.group(1))[1:]
        for i, (heading_raw, date) in enumerate(por_headings):
            items = []
            if i < len(por_parts):
                items = [_strip_latex(it) for it in re.findall(r"\\resumeItem\s*\{([^}]+)\}", por_parts[i])]
            por.append({
                "title": _strip_latex(heading_raw),
                "duration": _strip_latex(date),
                "bullets": items,
            })

    return ParsedResume(
        name=name,
        phone=phone,
        email=email,
        linkedin_url=linkedin_url,
        github_url=github_url,
        skills=skills,
        experience=experience,
        projects=projects,
        education=education,
        achievements=achievements,
        positions_of_responsibility=por,
    )
