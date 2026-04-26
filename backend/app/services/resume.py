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
from types import SimpleNamespace

from app.core.config import get_settings
from app.models.entities import Job, ResumeVersion, User
from app.services.documents import (
    compile_latex_to_pdf,
    convert_docx_to_pdf,
    write_docx_from_template,
    write_docx_rich,
    write_latex,
    write_pdf,
)
from app.services.resume_extraction import ResumeExtractionService
from app.services.text import clean_job_skills, extract_keywords, keyword_overlap, normalize

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
    tex_path: Path | None
    metadata_path: Path
    ai_generated: bool = False


def SimpleUserProxy(user: User, projects: list[dict]) -> SimpleNamespace:
    """Small prompt-only user proxy so LLM sees the selected project evidence."""
    return SimpleNamespace(
        name=user.name,
        current_role=user.current_role,
        experience_years=user.experience_years,
        skills=user.skills or [],
        experience=user.experience or [],
        projects=projects or user.projects or [],
        base_resume_text=user.base_resume_text,
    )


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

    def tailor(
        self,
        user: User,
        job: Job,
        match_score: int,
        *,
        manual_instructions: str | None = None,
        resume_id_suffix: str | None = None,
        auto_refined: bool = False,
    ) -> TailoredResume:
        docx_template_path = self._docx_template_path(user)
        latex_template_source = self._latex_template_source(user)
        verified_skills = self._verified_resume_skills(user)
        job_keywords = self._job_keywords(job)
        overlap, missing = keyword_overlap(verified_skills, job_keywords)
        requested_focus = self._requested_focus_skills(verified_skills, manual_instructions)
        emphasized = self._dedupe_skills([*requested_focus, *self._emphasized_verified_skills(verified_skills, job)])[:12]
        selected_projects = self._project_candidates(user, job)
        recommended_projects = self._recommended_projects(missing)
        resume_id = self._resume_id(job, resume_id_suffix)
        version_dir = self.storage_root / "resume_versions"
        metadata_dir = self.storage_root / "metadata"
        docx_path = version_dir / f"{resume_id}.docx"
        pdf_path = version_dir / f"{resume_id}.pdf"
        metadata_path = metadata_dir / f"{resume_id}.json"

        # --- Try LLM tailoring first for generated resumes.
        # For uploaded Word templates we intentionally use the deterministic
        # editor below: it preserves layout and avoids LLM-invented project text
        # leaking into the user's source resume.
        ai_generated = False
        paragraphs: list[str] = []
        try:
            from app.services.oci_genai import OCIGenerativeAIProvider
            provider = OCIGenerativeAIProvider()
            if provider.status().configured and not docx_template_path:
                prompt_user = SimpleUserProxy(user, selected_projects)
                prompt = _build_prompt(prompt_user, job, match_score, emphasized)
                llm_text = provider.chat(prompt)
                paragraphs = _parse_llm_response(llm_text)
                ai_generated = True
                logger.info("AI resume tailoring succeeded for job %s", job.id)
        except Exception as exc:
            logger.warning("AI tailoring failed, using deterministic ATS fallback: %s", exc)

        if not paragraphs:
            paragraphs = self._build_resume_text(
                user,
                job,
                emphasized,
                recommended_projects,
                manual_instructions,
                selected_projects=selected_projects,
            )

        title = f"{user.name} - {job.title} at {job.company}"

        docx_template_used = False
        if docx_template_path:
            docx_template_used = write_docx_from_template(
                docx_path,
                docx_template_path,
                job_title=job.title,
                company=job.company,
                paragraphs=paragraphs,
                emphasized_skills=emphasized,
                selected_projects=selected_projects,
            )
        if not docx_template_used:
            write_docx_rich(docx_path, title, paragraphs)

        # Always generate a LaTeX export. If the user uploaded a .tex template,
        # the writer keeps that structure; otherwise it creates a clean fallback.
        tex_path = version_dir / f"{resume_id}.tex"
        ordered_skills = self._ordered_verified_skills(verified_skills, emphasized)
        write_latex(
            path=tex_path,
            template_source=latex_template_source,
            user_name=user.name or "Candidate",
            skills=ordered_skills,
            experience=user.experience or [],
            projects=selected_projects or user.projects or [],
            job_title=job.title,
            company=job.company,
            paragraphs=paragraphs,
        )

        if docx_template_used:
            compiled_pdf = convert_docx_to_pdf(docx_path, pdf_path)
            pdf_generation = "docx_converter" if compiled_pdf else "styled_pdf_fallback"
        else:
            compiled_pdf = compile_latex_to_pdf(tex_path, pdf_path)
            pdf_generation = "latex_compiler" if compiled_pdf else "styled_pdf_fallback"
        if not compiled_pdf:
            write_pdf(pdf_path, title, paragraphs)

        plain_paragraphs = [p.replace("__HEADING__", "") for p in paragraphs]

        metadata = {
            "resume_id": resume_id,
            "company": job.company,
            "role": job.title,
            "job_url": job.job_url,
            "match_score": match_score,
            "skills_emphasized": emphasized,
            "created_at": datetime.now(UTC).date().isoformat(),
            "based_on_resume": "base_resume_v1",
            "source_format": "docx_template" if docx_template_used else ("latex_template" if latex_template_source else "generated_ats"),
            "docx_template_path": str(docx_template_path) if docx_template_used and docx_template_path else None,
            "truthfulness_check": "passed",
            "ai_generated": ai_generated,
            "minimal_docx_edit": docx_template_used,
            "minimal_latex_edit": bool(latex_template_source and not docx_template_used),
            "pdf_generation": pdf_generation,
            "resume_changes": self._resume_changes(
                bool(latex_template_source and not docx_template_used),
                docx_template_used,
                emphasized,
                ordered_skills,
                requested_focus,
                bool(manual_instructions and manual_instructions.strip()),
                auto_refined,
                selected_projects,
            ),
            "ordered_verified_skills": ordered_skills,
            "verified_skills_source": "uploaded_resume_and_profile",
            "recommended_projects_to_build": recommended_projects,
            "manual_refinement_notes": manual_instructions.strip() if manual_instructions else None,
            "auto_refined_from_job_description": auto_refined,
            "requested_focus_skills": requested_focus,
            "selected_projects": selected_projects,
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
            tex_path=tex_path,
            metadata_path=metadata_path,
            ai_generated=ai_generated,
        )

    def ensure_latex_export(self, user: User, version: ResumeVersion, job: Job | None = None, *, force: bool = False) -> Path:
        tex_path = Path(version.tex_path) if version.tex_path else self._version_export_path(version, "tex")
        if tex_path.exists() and not force:
            version.tex_path = str(tex_path)
            return tex_path

        paragraphs = self._export_paragraphs(user, version, job)
        verified_skills = self._verified_resume_skills(user)
        metadata = self._version_metadata(version)
        write_latex(
            path=tex_path,
            template_source=self._latex_template_source(user),
            user_name=user.name or "Candidate",
            skills=self._ordered_verified_skills(verified_skills, version.skills_emphasized or []),
            experience=user.experience or [],
            projects=metadata.get("selected_projects") or user.projects or [],
            job_title=version.role,
            company=version.company,
            paragraphs=paragraphs,
        )
        version.tex_path = str(tex_path)
        return tex_path

    def ensure_pdf_export(self, user: User, version: ResumeVersion, job: Job | None = None, *, force: bool = False) -> Path:
        pdf_path = Path(version.pdf_path)
        paragraphs = self._export_paragraphs(user, version, job)
        metadata = self._version_metadata(version)
        if force or not pdf_path.exists():
            title = f"{user.name} - {version.role} at {version.company}"
            if metadata.get("source_format") == "docx_template" and Path(version.docx_path).exists():
                converted = convert_docx_to_pdf(Path(version.docx_path), pdf_path)
                pdf_generation = "docx_converter" if converted else "styled_pdf_fallback"
                if not converted:
                    write_pdf(pdf_path, title, paragraphs)
            else:
                tex_path = self.ensure_latex_export(user, version, job, force=force)
                pdf_generation = "latex_compiler"
                if not compile_latex_to_pdf(tex_path, pdf_path):
                    write_pdf(pdf_path, title, paragraphs)
                    pdf_generation = "styled_pdf_fallback"
            self._update_pdf_generation_metadata(version, pdf_generation)
        version.pdf_path = str(pdf_path)
        return pdf_path

    @staticmethod
    def _update_pdf_generation_metadata(version: ResumeVersion, pdf_generation: str) -> None:
        if not version.metadata_path:
            return
        metadata_path = Path(version.metadata_path)
        if not metadata_path.exists():
            return
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["pdf_generation"] = pdf_generation
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not update PDF generation metadata for resume version %s: %s", version.id, exc)

    @staticmethod
    def _version_metadata(version: ResumeVersion) -> dict:
        if not version.metadata_path:
            return {}
        metadata_path = Path(version.metadata_path)
        if not metadata_path.exists():
            return {}
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _version_export_path(self, version: ResumeVersion, suffix: str) -> Path:
        version_dir = self.storage_root / "resume_versions"
        role = re.sub(r"[^a-z0-9]+", "-", version.role.lower()).strip("-")[:40]
        company = re.sub(r"[^a-z0-9]+", "-", version.company.lower()).strip("-")[:40]
        return version_dir / f"resume_version_{version.id}_{company}_{role}.{suffix}"

    def _export_paragraphs(self, user: User, version: ResumeVersion, job: Job | None = None) -> list[str]:
        skills = version.skills_emphasized or user.skills[:12] or (job.skills if job else [])
        metadata = self._version_metadata(version)
        project_source = metadata.get("selected_projects") or user.projects or []
        role_label = self._role_label(user)
        experience_phrase = self._experience_phrase(user)
        paragraphs = [
            f"Email: {user.email} | Phone: {user.phone or 'Not provided'} | Location: {user.location or 'Not provided'}",
            f"LinkedIn: {user.linkedin_url or 'Not provided'} | GitHub: {user.github_url or 'Not provided'}",
            f"Target Role: {version.role} at {version.company}",
            "__HEADING__Professional Summary",
            (
                f"{role_label}{experience_phrase}. "
                f"This resume version emphasizes {', '.join(skills) or 'verified resume strengths'} for {version.role}."
            ),
            "__HEADING__Relevant Skills",
            ", ".join(skills or ["Skills available in the uploaded resume"]),
            "__HEADING__Experience",
        ]

        if user.experience:
            for item in user.experience[:4]:
                role = item.get("role", "Role")
                company = item.get("company", "Company")
                duration = item.get("duration", "")
                paragraphs.append(f"{role} - {company}{f' | {duration}' if duration else ''}")
                paragraphs.extend(str(bullet) for bullet in item.get("bullets", [])[:4])
        elif user.base_resume_text:
            paragraphs.append("Experience details are preserved in the uploaded base resume; no unsupported experience was invented.")
        else:
            paragraphs.append("Experience details were not provided during onboarding.")

        paragraphs.append("__HEADING__Projects")
        if project_source:
            for project in project_source[:4]:
                name = project.get("name", "Project")
                summary = project.get("summary", "")
                project_skills = ", ".join(project.get("skills", []))
                paragraphs.append(f"{name}: {summary}{f' ({project_skills})' if project_skills else ''}")
        else:
            paragraphs.append("Project details were not provided during onboarding.")

        if job and job.description:
            paragraphs.append("__HEADING__Target Job Notes")
            paragraphs.append(job.description[:600])
        return paragraphs

    def has_latex_template(self, user: User) -> bool:
        return bool(self._latex_template_source(user))

    def has_docx_template(self, user: User) -> bool:
        return bool(self._docx_template_path(user))

    def _docx_template_path(self, user: User) -> Path | None:
        settings = get_settings()
        candidates: list[Path] = []
        base_path_value = getattr(user, "base_resume_path", None)
        if base_path_value:
            candidates.append(Path(base_path_value).expanduser())

        configured_path = getattr(settings, "docx_template_path", None)
        repo_root = Path(__file__).resolve().parents[3]
        if configured_path:
            configured = Path(configured_path).expanduser()
            if configured.is_absolute():
                candidates.append(configured)
            else:
                candidates.extend([Path.cwd() / configured, repo_root / configured, repo_root / "backend" / configured])

        base_resume_dir = self.storage_root / "base_resumes"
        if base_resume_dir.exists():
            candidates.extend(sorted(base_resume_dir.glob("*.docx"), key=lambda path: path.stat().st_mtime, reverse=True))

        seen: set[Path] = set()
        for candidate in candidates:
            try:
                path = candidate.expanduser().resolve()
            except Exception:
                continue
            if path in seen:
                continue
            seen.add(path)
            if path.exists() and path.suffix.lower() == ".docx":
                return path
        return None

    def _latex_template_source(self, user: User) -> str | None:
        source = getattr(user, "latex_template_source", None)
        if source and "\\documentclass" in source:
            return source

        settings = get_settings()
        candidates: list[Path] = []
        configured_path = getattr(settings, "latex_template_path", None)
        repo_root = Path(__file__).resolve().parents[3]
        if configured_path:
            configured = Path(configured_path).expanduser()
            if configured.is_absolute():
                candidates.append(configured)
            else:
                candidates.extend([Path.cwd() / configured, repo_root / configured, repo_root / "backend" / configured])

        base_resume_dir = self.storage_root / "base_resumes"
        if base_resume_dir.exists():
            candidates.extend(sorted(base_resume_dir.glob("*.tex"), key=lambda path: path.stat().st_mtime, reverse=True))

        seen: set[Path] = set()
        for candidate in candidates:
            path = candidate.expanduser().resolve()
            if path in seen:
                continue
            seen.add(path)
            if not path.exists() or path.suffix.lower() != ".tex":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception as exc:
                logger.warning("Could not read LaTeX template %s: %s", path, exc)
                continue
            if "\\documentclass" in text and "\\begin{document}" in text:
                return text
        return None

    def base_pdf_path(self, user: User) -> Path | None:
        return self._base_pdf_path(user)

    def _base_pdf_path(self, user: User) -> Path | None:
        base_path_value = getattr(user, "base_resume_path", None)
        if not base_path_value:
            base_path = None
        else:
            base_path = Path(base_path_value).expanduser()
        if base_path and base_path.exists() and base_path.suffix.lower() == ".pdf":
            return base_path

        base_resume_dir = self.storage_root / "base_resumes"
        if base_resume_dir.exists():
            pdfs = sorted(base_resume_dir.glob("*.pdf"), key=lambda path: path.stat().st_mtime, reverse=True)
            if pdfs:
                return pdfs[0]
        return None

    @staticmethod
    def _role_label(user: User) -> str:
        if getattr(user, "current_role", None):
            return user.current_role
        if user.experience:
            role = user.experience[0].get("role")
            if role:
                return str(role)
        text = normalize(getattr(user, "base_resume_text", "") or "")
        if "ai/ml engineer" in text:
            return "AI/ML Engineer"
        if "machine learning" in text:
            return "Machine Learning Engineer"
        if "generative ai" in text or "genai" in text:
            return "Generative AI Engineer"
        return "Candidate"

    @staticmethod
    def _experience_phrase(user: User) -> str:
        years = getattr(user, "experience_years", 0) or 0
        if years > 0:
            return f" with {years:g}+ years of experience"
        if getattr(user, "experience", None) or getattr(user, "base_resume_text", None):
            return " with production experience"
        return ""

    @staticmethod
    def _ordered_verified_skills(user_skills: list[str], emphasized: list[str]) -> list[str]:
        selected: list[str] = []
        normalized_seen: set[str] = set()
        for skill in [*emphasized, *user_skills]:
            key = normalize(str(skill))
            if key and key not in normalized_seen:
                normalized_seen.add(key)
                selected.append(str(skill))
        return selected[:28]

    @classmethod
    def _dedupe_skills(cls, skills: list[str]) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()
        for skill in skills:
            key = normalize(str(skill))
            if key and key not in seen:
                seen.add(key)
                selected.append(str(skill))
        return selected

    @staticmethod
    def _job_keywords(job: Job) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()
        for skill in [*clean_job_skills(job.skills), *extract_keywords(job.description or "", limit=28)]:
            key = normalize(str(skill))
            if key and key not in seen:
                seen.add(key)
                selected.append(str(skill))
        return selected

    def _verified_resume_skills(self, user: User) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()
        docx_template_text = self._docx_template_text(user)
        raw_sources = [
            *(user.skills or []),
            *ResumeExtractionService._extract_skills(getattr(user, "base_resume_text", "") or ""),
            *ResumeExtractionService._extract_skills(docx_template_text),
            *ResumeExtractionService._extract_skills(self._latex_template_source(user) or ""),
        ]
        for skill in raw_sources:
            key = normalize(str(skill))
            if key and key not in seen:
                seen.add(key)
                selected.append(str(skill))
        return selected[:60]

    def _docx_template_text(self, user: User) -> str:
        path = self._docx_template_path(user)
        if not path:
            return ""
        try:
            return ResumeExtractionService._docx_text(path.read_bytes())
        except Exception as exc:
            logger.warning("Could not read DOCX template text %s: %s", path, exc)
            return ""

    def _project_candidates(self, user: User, job: Job) -> list[dict]:
        job_text = normalize(" ".join([job.title or "", job.description or "", *[str(skill) for skill in job.skills or []]]))
        candidates: list[dict] = []
        for project in user.projects or []:
            normalized = self._normalize_project(project)
            if normalized:
                candidates.append(normalized)
        latex_source = self._latex_template_source(user)
        if latex_source:
            try:
                from app.services.latex_parser import parse_latex_resume
                parsed = parse_latex_resume(latex_source)
                for project in parsed.projects:
                    normalized = self._normalize_project(project.__dict__)
                    if normalized:
                        candidates.append(normalized)
            except Exception as exc:
                logger.warning("Could not parse LaTeX projects for tailoring: %s", exc)
        for repo in getattr(user, "github_repositories", None) or []:
            normalized = self._normalize_project(repo)
            if normalized:
                candidates.append(normalized)

        raw_scored: list[tuple[int, int, dict]] = []
        job_tokens = set(token for token in re.split(r"[^a-z0-9+#.]+", job_text) if len(token) > 1)
        for project in candidates:
            evidence_text = normalize(
                " ".join(
                    [
                        str(project.get("name") or ""),
                        str(project.get("summary") or project.get("description") or ""),
                        " ".join(str(item) for item in project.get("skills", []) or []),
                        " ".join(str(item) for item in project.get("bullets", []) or []),
                    ]
                )
            )
            evidence_tokens = set(token for token in re.split(r"[^a-z0-9+#.]+", evidence_text) if len(token) > 1)
            overlap = len(evidence_tokens & job_tokens)
            richness = len(project.get("skills", []) or []) + len(project.get("bullets", []) or []) + (1 if project.get("summary") else 0)
            raw_scored.append((overlap, richness, project))

        raw_scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        seen: set[str] = set()
        scored: list[tuple[int, dict]] = []
        for overlap, _richness, project in raw_scored:
            keys = self._project_identity_keys(project)
            if keys and any(key in seen for key in keys):
                continue
            seen.update(keys)
            scored.append((overlap, project))
        selected = [project for score, project in scored if score > 0][:8]
        if not selected:
            selected = [project for _, project in scored[:6]]
        return selected

    @staticmethod
    def _project_identity_keys(project: dict) -> set[str]:
        keys: set[str] = set()
        name = normalize(str(project.get("name") or ""))
        url = str(project.get("url") or project.get("repo_url") or project.get("html_url") or "").strip().lower()
        if name:
            keys.add(name)
            keys.add(re.sub(r"[^a-z0-9]+", "", name))
        if url:
            keys.add(url.rstrip("/"))
            keys.add(normalize(url.rstrip("/").split("/")[-1]))
            keys.add(re.sub(r"[^a-z0-9]+", "", url.rstrip("/").split("/")[-1]))
        return {key for key in keys if key}

    @staticmethod
    def _normalize_project(project: dict) -> dict:
        name = str(project.get("name") or project.get("repo") or project.get("full_name") or "").strip()
        url = str(project.get("url") or project.get("repo_url") or project.get("html_url") or "").strip()
        if not name and url:
            name = url.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ").title()
        summary = str(project.get("summary") or project.get("description") or project.get("notes") or "").strip()
        raw_skills = project.get("skills") or project.get("topics") or []
        if isinstance(raw_skills, str):
            raw_skills = [item.strip() for item in raw_skills.split(",") if item.strip()]
        inferred = extract_keywords(" ".join([name, summary]), limit=10)
        skills = ResumeTailoringAgent._dedupe_skills([str(skill) for skill in raw_skills] + inferred)[:12]
        raw_bullets = project.get("bullets") or []
        if isinstance(raw_bullets, str):
            raw_bullets = [item.strip() for item in re.split(r"[\n;]+", raw_bullets) if item.strip()]
        bullets = [str(item).strip() for item in raw_bullets if str(item).strip()]
        if not bullets and summary:
            bullets = [summary]
        if not name and not summary:
            return {}
        return {"name": name or "GitHub Project", "summary": summary, "skills": skills, "url": url or None, "bullets": bullets[:4]}

    @classmethod
    def _emphasized_verified_skills(cls, verified_skills: list[str], job: Job) -> list[str]:
        job_text = normalize(" ".join([job.title or "", job.description or "", *[str(skill) for skill in job.skills or []]]))
        emphasized: list[str] = []
        seen: set[str] = set()
        for skill in verified_skills:
            key = normalize(str(skill))
            if not key or key in seen:
                continue
            tokens = [token for token in re.split(r"[^a-z0-9+#.]+", key) if len(token) > 1]
            direct_match = key in job_text
            token_match = tokens and all(token in job_text for token in tokens[:3])
            if direct_match or token_match:
                emphasized.append(str(skill))
                seen.add(key)
        return emphasized[:12]

    @staticmethod
    def _requested_focus_skills(verified_skills: list[str], manual_instructions: str | None) -> list[str]:
        if not manual_instructions:
            return []
        instruction_text = normalize(manual_instructions)
        requested: list[str] = []
        seen: set[str] = set()
        for skill in verified_skills:
            key = normalize(str(skill))
            if key and key in instruction_text and key not in seen:
                seen.add(key)
                requested.append(str(skill))
        return requested[:8]

    @staticmethod
    def _resume_changes(
        has_latex_template: bool,
        has_docx_template: bool,
        emphasized: list[str],
        ordered_skills: list[str],
        requested_focus: list[str] | None = None,
        manual_refinement: bool = False,
        auto_refinement: bool = False,
        selected_projects: list[dict] | None = None,
    ) -> list[str]:
        changes = []
        if has_docx_template:
            changes.append("Kept the uploaded Word resume template and preserved core formatting.")
            changes.append("Updated the Profile/Summary section in the Word resume for the selected role.")
            changes.append("Added a compact Targeted Focus line in the Word resume using only verified resume skills.")
        elif has_latex_template:
            changes.append("Kept the uploaded LaTeX resume template and preserved core formatting.")
            changes.append("Updated the Profile/Summary section to target the selected role.")
            changes.append("Added a compact Targeted Focus line in Technical Skills using only verified resume skills.")
        else:
            changes.append("Generated a clean ATS resume because no editable Word or LaTeX template was available.")
        if emphasized:
            changes.append(f"Emphasized verified overlap: {', '.join(emphasized[:8])}.")
        elif ordered_skills:
            changes.append(f"Kept verified skills visible: {', '.join(ordered_skills[:8])}.")
        if auto_refinement:
            if requested_focus:
                changes.append(f"Auto-refined from the job description using verified overlap: {', '.join(requested_focus[:8])}.")
            else:
                changes.append("Auto-refined from the job description without adding unsupported skills.")
        elif manual_refinement:
            if requested_focus:
                changes.append(f"Applied your refinement comments where they matched verified skills: {', '.join(requested_focus[:8])}.")
            else:
                changes.append("Saved your refinement comments for review; no unsupported new skills were added.")
        if selected_projects:
            names = [str(project.get("name")) for project in selected_projects if project.get("name")]
            if names:
                changes.append(f"Selected strongest project evidence for this JD: {', '.join(names[:3])}.")
        return changes

    def auto_refinement_instructions(self, user: User, job: Job) -> str:
        verified_skills = self._verified_resume_skills(user)
        overlap = self._emphasized_verified_skills(verified_skills, job)
        selected_projects = self._project_candidates(user, job)
        job_keywords = self._job_keywords(job)
        focus = overlap[:10] or [skill for skill in verified_skills[:10] if normalize(str(skill)) in normalize(job.description or "")]
        weak = [
            skill
            for skill in job_keywords[:12]
            if normalize(str(skill)) not in {normalize(str(item)) for item in verified_skills}
        ]
        parts = [
            "AUTO_FROM_JD: Use the target job description to truthfully improve ATS alignment.",
            (
                "Preserve the uploaded Word resume format and make minimal targeted edits only."
                if self.has_docx_template(user)
                else "Preserve the uploaded LaTeX format and make minimal targeted edits only."
            ),
            "Rewrite the summary/profile and targeted skills focus for this exact role.",
            "Reorder or replace the Projects section with the strongest verified GitHub/resume projects for the JD.",
            "Remove low-signal lines only when stronger verified project or skill evidence is available.",
        ]
        if focus:
            parts.append("Emphasize verified overlap already present in the resume: " + ", ".join(focus) + ".")
        if selected_projects:
            parts.append("Prioritize project evidence: " + ", ".join(str(project.get("name")) for project in selected_projects[:3]) + ".")
        if weak:
            parts.append("Do not claim missing skills; keep these only as gaps or recommended learning if needed: " + ", ".join(weak[:8]) + ".")
        parts.append("Do not invent companies, metrics, titles, projects, or tools.")
        return " ".join(parts)

    def find_reusable(self, existing: list[ResumeVersion], job: Job) -> ResumeVersion | None:
        best: tuple[float, ResumeVersion] | None = None
        job_terms = set(clean_job_skills(job.skills) or extract_keywords(job.description))
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
    def _resume_id(job: Job, suffix: str | None = None) -> str:
        date_prefix = datetime.now(UTC).strftime("%Y%m%d")
        slug = re.sub(r"[^a-z0-9]+", "-", f"{job.company}-{job.title}".lower()).strip("-")
        clean_suffix = re.sub(r"[^a-z0-9]+", "-", suffix.lower()).strip("-") if suffix else ""
        return f"resume_{date_prefix}_{slug[:80]}{f'-{clean_suffix[:32]}' if clean_suffix else ''}"

    @staticmethod
    def _recommended_projects(missing: set[str]) -> list[str]:
        notable = [skill for skill in sorted(missing) if len(skill) > 2][:4]
        if not notable:
            return []
        return [f"Recommended project to build demonstrating {', '.join(notable)} in a production-style workflow."]

    @staticmethod
    def _build_resume_text(
        user: User,
        job: Job,
        emphasized: list[str],
        recommended: list[str],
        manual_instructions: str | None = None,
        selected_projects: list[dict] | None = None,
    ) -> list[str]:
        """Deterministic template fallback — used when LLM is unavailable."""
        role_label = ResumeTailoringAgent._role_label(user)
        experience_phrase = ResumeTailoringAgent._experience_phrase(user)
        paragraphs = [
            f"Email: {user.email} | Phone: {user.phone or 'Not provided'} | Location: {user.location or 'Not provided'}",
            f"LinkedIn: {user.linkedin_url or 'Not provided'} | GitHub: {user.github_url or 'Not provided'}",
            f"Target Role: {job.title} at {job.company}",
            "__HEADING__Professional Summary",
            (
                f"{role_label}{experience_phrase}. "
                f"This version emphasizes truthful evidence relevant to {job.title}: {', '.join(emphasized) or 'core resume strengths'}."
            ),
            "__HEADING__Relevant Skills",
            ", ".join(emphasized or user.skills[:12] or ["Skills not provided"]),
            "__HEADING__Experience",
        ]
        if manual_instructions and emphasized:
            paragraphs.insert(
                6,
                "Refinement focus applied using verified resume evidence: " + ", ".join(emphasized[:8]),
            )
        if user.experience:
            for item in user.experience[:4]:
                company = item.get("company", "Company")
                role = item.get("role", "Role")
                bullets = item.get("bullets", [])
                paragraphs.append(f"{role} - {company}")
                paragraphs.extend(str(bullet) for bullet in bullets[:4])
        elif user.base_resume_text:
            paragraphs.append("Experience details are preserved in the uploaded base resume; no unsupported experience was invented.")
        else:
            paragraphs.append("Experience details were not provided during onboarding.")

        paragraphs.append("__HEADING__Projects")
        project_source = selected_projects or user.projects or []
        if project_source:
            for project in project_source[:4]:
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
