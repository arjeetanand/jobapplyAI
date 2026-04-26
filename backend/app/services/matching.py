from dataclasses import dataclass

from app.models.entities import Job, JobPreference, User
from app.services.safety import SafetyComplianceAgent
from app.services.text import clean_job_skills, extract_keywords, keyword_overlap, normalize, tokenize


@dataclass
class MatchResult:
    score: int
    reasons: list[str]
    concerns: list[str]
    recommendation: str


class JobMatchingAgent:
    def __init__(self, safety_agent: SafetyComplianceAgent | None = None) -> None:
        self.safety_agent = safety_agent or SafetyComplianceAgent()

    # ---- fuzzy title helpers ----
    _ROLE_SYNONYMS: dict[str, set[str]] = {
        "ai engineer": {"artificial intelligence engineer", "ai/ml engineer", "machine learning engineer", "ml engineer"},
        "ai scientist": {"artificial intelligence scientist", "artificial intelligence engineer", "data scientist", "ml scientist"},
        "software engineer": {"developer", "sde", "swe", "programmer", "coder"},
        "backend engineer": {"backend developer", "server engineer", "api engineer"},
        "frontend engineer": {"frontend developer", "ui engineer", "ui developer"},
        "full stack": {"fullstack", "full-stack"},
        "ml engineer": {"machine learning engineer", "ai engineer", "ai/ml engineer"},
        "data engineer": {"data platform engineer", "analytics engineer"},
        "devops engineer": {"site reliability engineer", "sre", "infrastructure engineer", "platform engineer"},
        "python developer": {"python engineer", "python programmer"},
    }

    @classmethod
    def _fuzzy_title_match(cls, job_title: str, roles: list[str]) -> tuple[bool, str]:
        """Check if job_title is a fuzzy match for any role in the list."""
        t = normalize(job_title)
        for role in roles:
            r = normalize(role)
            # Exact / substring
            if r and (r in t or t in r):
                return True, role
            t_expanded = t.replace("ai", "artificial intelligence")
            r_expanded = r.replace("ai", "artificial intelligence")
            t_expanded_tokens = set(t_expanded.split())
            r_expanded_tokens = set(r_expanded.split())
            if r_expanded_tokens and len(t_expanded_tokens & r_expanded_tokens) / len(r_expanded_tokens) >= 0.6:
                return True, role
            # Token overlap: e.g. "software engineer" vs "software engineer, backend"
            t_tokens = set(t.split())
            r_tokens = set(r.split())
            if r_tokens and len(t_tokens & r_tokens) / len(r_tokens) >= 0.6:
                return True, role
            # Synonym expansion
            for base, syns in cls._ROLE_SYNONYMS.items():
                base_norm = normalize(base)
                all_variants = {base_norm} | {normalize(s) for s in syns}
                if any(v in t for v in all_variants) and any(v in r for v in all_variants):
                    return True, role
        return False, ""

    def score(self, user: User, preferences: JobPreference, job: Job) -> MatchResult:
        score = 0
        reasons: list[str] = []
        concerns: list[str] = []

        # ---- 1. Title match (max 22) ----
        title = normalize(job.title)
        target_roles = [normalize(role) for role in preferences.target_roles]
        similar_roles = [normalize(role) for role in preferences.similar_roles]

        matched, matched_role = self._fuzzy_title_match(job.title, preferences.target_roles)
        if matched:
            score += 22
            reasons.append(f"Role title strongly matches target role: {matched_role}.")
        else:
            matched, matched_role = self._fuzzy_title_match(job.title, preferences.similar_roles)
            if matched:
                score += 16
                reasons.append(f"Role title matches similar role: {matched_role}.")
            else:
                concerns.append("Role title is not an obvious target-role match.")

        # ---- 2. Skill overlap (max 28) ----
        job_skills = clean_job_skills([*clean_job_skills(job.skills), *extract_keywords(job.description or "", limit=16)])
        overlap, missing = keyword_overlap(user.skills, job_skills)
        if job_skills:
            skill_ratio = len(overlap) / len(set(normalize(skill) for skill in job_skills if skill))
            skill_points = round(skill_ratio * 28)
            score += skill_points
            if overlap:
                reasons.append(f"Skill overlap includes {', '.join(sorted(overlap)[:8])}.")
            if missing:
                concerns.append(f"Missing or weakly evidenced skills: {', '.join(sorted(missing)[:8])}.")
        else:
            score += 10
            concerns.append("Job did not provide structured skills; score uses description context.")

        # ---- 3. Resume text <-> job description overlap (max 10 bonus) ----
        description = normalize(job.description)
        if user.base_resume_text and description:
            resume_tokens = set(tokenize(user.base_resume_text))
            desc_tokens = set(tokenize(job.description))
            # Remove very common words
            stop = {"the", "and", "for", "with", "you", "are", "will", "our", "this",
                    "that", "from", "have", "has", "experience", "role", "team", "work",
                    "about", "your", "job", "working", "including", "more", "also",
                    "should", "can", "all", "not", "but", "these", "each", "into"}
            resume_tokens -= stop
            desc_tokens -= stop
            if desc_tokens:
                text_overlap = len(resume_tokens & desc_tokens) / len(desc_tokens)
                text_points = min(10, round(text_overlap * 15))
                if text_points > 0:
                    score += text_points
                    reasons.append(f"Resume text has {round(text_overlap * 100)}% keyword overlap with job description.")

        # ---- 4. Experience (max 10) ----
        if user.experience_years:
            score += 10
            reasons.append(f"User has {user.experience_years}+ years of experience.")

        # ---- 5. Salary (max 10) ----
        if preferences.minimum_salary and job.salary_min:
            if job.salary_min >= preferences.minimum_salary:
                score += 10
                reasons.append("Salary meets the configured minimum expectation.")
            else:
                concerns.append("Salary is below the configured minimum expectation.")
        elif preferences.minimum_salary:
            score += 4
            concerns.append("Salary was not available in the job post.")

        # ---- 6. Location (max 8) ----
        location = normalize(job.location)
        preferred_locations = [normalize(item) for item in preferences.preferred_locations]
        if preferred_locations and any(item and item in location for item in preferred_locations):
            score += 8
            reasons.append("Location matches preferred locations.")
        elif normalize(preferences.remote_preference) in normalize(job.work_mode or ""):
            score += 8
            reasons.append("Work mode matches remote/hybrid/onsite preference.")
        elif preferred_locations:
            concerns.append("Location does not clearly match preferences.")

        # ---- 7. Project evidence (max 10) ----
        project_hits = [
            project.get("name", "")
            for project in user.projects or []
            if any(normalize(skill) in description for skill in project.get("skills", []))
        ]
        if project_hits:
            score += 10
            reasons.append(f"Relevant project evidence found: {', '.join(project_hits[:3])}.")
        else:
            concerns.append("No strong project match found for the job context.")

        # ---- 8. Base resume available (max 7) ----
        if user.base_resume_text:
            score += 7
            reasons.append("Base resume is available for tailoring.")
        else:
            concerns.append("Base resume text is missing; tailoring quality will be limited.")

        # ---- Safety check ----
        safety = self.safety_agent.evaluate_job(user, preferences, job)
        concerns.extend(safety.warnings)
        if safety.blocks:
            concerns.extend(safety.blocks)
            score = min(score, 40)

        score = max(0, min(100, score))
        recommendation = self._recommendation(score, safety.allowed)
        return MatchResult(score=score, reasons=reasons, concerns=concerns, recommendation=recommendation)

    @staticmethod
    def _recommendation(score: int, allowed: bool) -> str:
        if not allowed:
            return "Blocked by safety rules"
        if score >= 90:
            return "Excellent match; prepare for user review"
        if score >= 75:
            return "Good match; tailor resume before review"
        if score >= 60:
            return "Possible match; ask user before proceeding"
        return "Do not apply unless user explicitly approves"
