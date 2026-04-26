from dataclasses import dataclass, field

from app.models.entities import Job, JobPreference, User
from app.services.text import clean_job_skills, normalize


@dataclass
class SafetyDecision:
    allowed: bool
    requires_review: bool = True
    blocks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


SENSITIVE_QUESTION_MARKERS = [
    "expected ctc",
    "current ctc",
    "work authorization",
    "willing to relocate",
    "previously worked",
    "why do you want",
]


class SafetyComplianceAgent:
    def evaluate_job(self, user: User, preferences: JobPreference, job: Job) -> SafetyDecision:
        blocks: list[str] = []
        warnings: list[str] = []
        company = normalize(job.company)
        location = normalize(job.location)
        title = normalize(job.title)
        description = normalize(job.description)

        excluded_companies = {normalize(company) for company in preferences.excluded_companies}
        if company in excluded_companies:
            blocks.append(f"{job.company} is in the excluded companies list.")

        excluded_locations = [normalize(item) for item in preferences.excluded_locations]
        if any(excluded and excluded in location for excluded in excluded_locations):
            blocks.append(f"{job.location} matches an excluded location.")

        if preferences.minimum_salary and job.salary_min and job.salary_min < preferences.minimum_salary:
            blocks.append("Job salary is below the configured minimum salary expectation.")

        allowed_roles = [*preferences.target_roles, *preferences.similar_roles]
        if allowed_roles:
            title_tokens = set(title.split())
            role_match = False
            for role in allowed_roles:
                normalized_role = normalize(role)
                role_tokens = set(normalized_role.split())
                if normalized_role in title or title in normalized_role:
                    role_match = True
                    break
                if role_tokens and len(title_tokens & role_tokens) / len(role_tokens) >= 0.6:
                    role_match = True
                    break
            if not role_match:
                warnings.append("Role title is outside target/similar roles and needs explicit review.")

        user_skills = {normalize(skill) for skill in user.skills}
        job_skills = {normalize(skill) for skill in clean_job_skills(job.skills)}
        if job_skills and user_skills:
            overlap = job_skills & user_skills
            if len(overlap) == 0:
                blocks.append("Required skills appear completely outside the user profile.")
            elif len(overlap) / max(len(job_skills), 1) < 0.25:
                warnings.append("Skill overlap is weak; review before proceeding.")

        if any(marker in description for marker in SENSITIVE_QUESTION_MARKERS):
            warnings.append("Application may include sensitive or subjective questions.")

        return SafetyDecision(allowed=not blocks, requires_review=True, blocks=blocks, warnings=warnings)

    def assert_truthful_resume(self, user: User, requested_claims: list[str]) -> SafetyDecision:
        source = normalize(
            " ".join(
                [
                    " ".join(user.skills or []),
                    " ".join(user.certifications or []),
                    " ".join(user.achievements or []),
                    str(user.projects or []),
                    str(user.experience or []),
                    user.base_resume_text or "",
                ]
            )
        )
        blocks = [claim for claim in requested_claims if normalize(claim) not in source]
        if blocks:
            return SafetyDecision(
                allowed=False,
                blocks=[f"Unsupported resume claim: {claim}" for claim in blocks],
                warnings=["Unsupported claims can be recommended as projects to build, not added as experience."],
            )
        return SafetyDecision(allowed=True)
