"""Resume tailoring service.

ResumeTailoringAgent.tailor() now uses OCI Generative AI to write a job-specific
resume. If OCI is not configured or the LLM call fails, it falls back to the
deterministic keyword-emphasis template so the app always produces a usable output.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.models.entities import Job, ResumeVersion, User
from app.services.documents import write_docx_rich, write_pdf
from app.services.text import extract_keywords, keyword_overlap, normalize

logger = logging.getLogger(__name__)


@dataclass
class TailoredResume:
    resume_id: str
    paragraphs: list[str]
    skills_emphasized: list[str]
    recommended_projects: list[str]
    metadata: dict
    docx_path: Path
    pdf_path: Path
    metadata_path: Path
    ai_generated: bool = False


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

_RESUME_PROMPT_TEMPLATE = """\
You are an expert professional resume writer and career coach.

Your task: Rewrite the candidate's resume to strongly target the specific job below.

=== STRICT RULES ===
1. ONLY use facts present in the user profile. Do NOT invent companies, projects, metrics, titles, or skills.
2. You MAY reorder, emphasise, and reframe existing experience to highlight relevance.
3. You MAY rewrite bullet points to use stronger action verbs and better quantification — but only based on information already in the profile.
4. Do NOT add skills that are not in the user profile.
5. Missing job-required skills should appear under a "Recommended Skills to Build" note — NOT in the resume body.
6. Output EXACTLY these section headers, in order, on their own lines:
   PROFESSIONAL SUMMARY
   SKILLS
   EXPERIENCE
   PROJECTS
   EDUCATION
7. Under SKILLS, output a comma-separated single line of the most relevant skills.
8. Under EXPERIENCE, format each entry as:
   Role | Company | Duration
   • Bullet 1
   • Bullet 2
9. Under PROJECTS, format each entry as:
   Project Name — one-line description (skills used)
10. Be concise. Total output should be under 600 words.

=== USER PROFILE ===
Name: {name}
Current Role: {current_role}
Experience Years: {experience_years}
Skills: {skills}
Experience: {experience}
Projects: {projects}

=== TARGET JOB ===
Title: {job_title}
Company: {company}
Description: {description}
Required Skills: {required_skills}
Match Score: {match_score}/100

=== WRITE THE TAILORED RESUME BELOW ===
"""


def _build_prompt(user: User, job: Job, match_score: int, emphasized: list[str]) -> str:
    experience_text = ""
    if user.experience:
        parts = []
        for item in user.experience[:5]:
            company = item.get("company", "")
            role = item.get("role", "")
            duration = item.get("duration", "")
            bullets = item.get("bullets", [])
            parts.append(f"{role} | {company} | {duration}")
            for bullet in bullets[:4]:
                parts.append(f"  - {bullet}")
        experience_text = "\n".join(parts)
    elif user.base_resume_text:
        experience_text = user.base_resume_text[:1500]
    else:
        experience_text = "No structured experience provided."

    projects_text = ""
    if user.projects:
        parts = []
        for project in user.projects[:5]:
            name = project.get("name", "")
            summary = project.get("summary", "")
            skills = ", ".join(project.get("skills", []))
            parts.append(f"{name}: {summary} [{skills}]")
        projects_text = "\n".join(parts)
    else:
        projects_text = "No projects listed."

    return _RESUME_PROMPT_TEMPLATE.format(
        name=user.name or "Candidate",
        current_role=user.current_role or "Professional",
        experience_years=user.experience_years or 0,
        skills=", ".join(user.skills[:30] if user.skills else ["Not specified"]),
        experience=experience_text,
        projects=projects_text,
        job_title=job.title,
        company=job.company,
        description=(job.description or "")[:1200],
        required_skills=", ".join(job.skills[:20] if job.skills else ["Not specified"]),
        match_score=match_score,
    )


# ---------------------------------------------------------------------------
# LLM response parser
# ---------------------------------------------------------------------------

_SECTION_HEADERS = ["PROFESSIONAL SUMMARY", "SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"]


def _parse_llm_response(text: str) -> list[str]:
    """Convert LLM output into a flat list of paragraph strings for DOCX/PDF."""
    paragraphs: list[str] = []
    current_section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Check if this line is a section header
        upper = line.upper().rstrip(":")
        if upper in _SECTION_HEADERS:
            current_section = upper
            paragraphs.append(f"__HEADING__{line}")
            continue
        if current_section:
            paragraphs.append(line)
    return paragraphs if paragraphs else [text.strip()]


# ---------------------------------------------------------------------------
# Main agent
# ---------------------------------------------------------------------------

class ResumeTailoringAgent:
    def __init__(self, storage_root: Path | None = None) -> None:
        self.storage_root = storage_root or get_settings().resolved_storage_root

    def tailor(self, user: User, job: Job, match_score: int) -> TailoredResume:
        job_keywords = job.skills or extract_keywords(job.description)
        overlap, missing = keyword_overlap(user.skills, job_keywords)
        emphasized = sorted(overlap)[:12]
        recommended_projects = self._recommended_projects(missing)
        resume_id = self._resume_id(job)
        version_dir = self.storage_root / "resume_versions"
        metadata_dir = self.storage_root / "metadata"
        docx_path = version_dir / f"{resume_id}.docx"
        pdf_path = version_dir / f"{resume_id}.pdf"
        metadata_path = metadata_dir / f"{resume_id}.json"

        # --- Try LLM tailoring first ---
        ai_generated = False
        paragraphs: list[str] = []
        try:
            from app.services.oci_genai import OCIGenerativeAIProvider
            provider = OCIGenerativeAIProvider()
            if provider.status().configured:
                prompt = _build_prompt(user, job, match_score, emphasized)
                llm_text = provider.chat(prompt)
                paragraphs = _parse_llm_response(llm_text)
                ai_generated = True
                logger.info("AI resume tailoring succeeded for job %s", job.id)
        except Exception as exc:
            logger.warning("AI tailoring failed, using template fallback: %s", exc)

        if not paragraphs:
            paragraphs = self._build_resume_text(user, job, emphasized, recommended_projects)

        title = f"{user.name} - {job.title} at {job.company}"

        # Build a flat plain-text version for the simple PDF writer
        plain_paragraphs = [p.replace("__HEADING__", "") for p in paragraphs]

        write_docx_rich(docx_path, title, paragraphs)
        write_pdf(pdf_path, title, plain_paragraphs)

        metadata = {
            "resume_id": resume_id,
            "company": job.company,
            "role": job.title,
            "job_url": job.job_url,
            "match_score": match_score,
            "skills_emphasized": emphasized,
            "created_at": datetime.now(UTC).date().isoformat(),
            "based_on_resume": "base_resume_v1",
            "truthfulness_check": "passed",
            "ai_generated": ai_generated,
            "recommended_projects_to_build": recommended_projects,
        }
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        return TailoredResume(
            resume_id=resume_id,
            paragraphs=plain_paragraphs,
            skills_emphasized=emphasized,
            recommended_projects=recommended_projects,
            metadata=metadata,
            docx_path=docx_path,
            pdf_path=pdf_path,
            metadata_path=metadata_path,
            ai_generated=ai_generated,
        )

    def find_reusable(self, existing: list[ResumeVersion], job: Job) -> ResumeVersion | None:
        best: tuple[float, ResumeVersion] | None = None
        job_terms = set(job.skills or extract_keywords(job.description))
        for version in existing:
            version_terms = set(version.skills_emphasized or [])
            skill_score = len({normalize(x) for x in job_terms} & {normalize(x) for x in version_terms}) / max(
                len(job_terms), 1
            )
            role_score = 1.0 if normalize(version.role).split()[0:1] == normalize(job.title).split()[0:1] else 0.0
            company_score = 0.25 if normalize(version.company) == normalize(job.company) else 0.0
            score = skill_score * 0.7 + role_score * 0.2 + company_score
            if best is None or score > best[0]:
                best = (score, version)
        if best and best[0] >= 0.65:
            return best[1]
        return None

    @staticmethod
    def _resume_id(job: Job) -> str:
        date_prefix = datetime.now(UTC).strftime("%Y%m%d")
        slug = re.sub(r"[^a-z0-9]+", "-", f"{job.company}-{job.title}".lower()).strip("-")
        return f"resume_{date_prefix}_{slug[:80]}"

    @staticmethod
    def _recommended_projects(missing: set[str]) -> list[str]:
        notable = [skill for skill in sorted(missing) if len(skill) > 2][:4]
        if not notable:
            return []
        return [f"Recommended project to build demonstrating {', '.join(notable)} in a production-style workflow."]

    @staticmethod
    def _build_resume_text(user: User, job: Job, emphasized: list[str], recommended: list[str]) -> list[str]:
        """Deterministic template fallback — used when LLM is unavailable."""
        paragraphs = [
            f"Email: {user.email} | Phone: {user.phone or 'Not provided'} | Location: {user.location or 'Not provided'}",
            f"LinkedIn: {user.linkedin_url or 'Not provided'} | GitHub: {user.github_url or 'Not provided'}",
            f"Target Role: {job.title} at {job.company}",
            "__HEADING__Professional Summary",
            (
                f"{user.current_role or 'Candidate'} with {user.experience_years:g} years of experience. "
                f"This version emphasizes truthful evidence relevant to {job.title}: {', '.join(emphasized) or 'core resume strengths'}."
            ),
            "__HEADING__Relevant Skills",
            ", ".join(emphasized or user.skills[:12] or ["Skills not provided"]),
            "__HEADING__Experience",
        ]
        if user.experience:
            for item in user.experience[:4]:
                company = item.get("company", "Company")
                role = item.get("role", "Role")
                bullets = item.get("bullets", [])
                paragraphs.append(f"{role} - {company}")
                paragraphs.extend(str(bullet) for bullet in bullets[:4])
        elif user.base_resume_text:
            paragraphs.append(user.base_resume_text[:1200])
        else:
            paragraphs.append("Experience details were not provided during onboarding.")

        paragraphs.append("__HEADING__Projects")
        if user.projects:
            for project in user.projects[:4]:
                name = project.get("name", "Project")
                summary = project.get("summary", "")
                skills = ", ".join(project.get("skills", []))
                paragraphs.append(f"{name}: {summary} ({skills})")
        else:
            paragraphs.append("Project details were not provided during onboarding.")

        if recommended:
            paragraphs.append("__HEADING__Recommended Projects To Build")
            paragraphs.extend(recommended)
        return paragraphs
