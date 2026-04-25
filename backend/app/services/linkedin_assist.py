from dataclasses import dataclass, field
from urllib.parse import urlencode

from app.models.entities import JobPreference, User
from app.services.text import extract_keywords


@dataclass
class LinkedInSearchPlan:
    url: str
    keyword: str
    location: str
    filters: dict[str, str]
    safety_notes: list[str] = field(default_factory=list)


class LinkedInAssistAgent:
    date_posted = {
        "any": "",
        "past_24_hours": "r86400",
        "past_week": "r604800",
        "past_month": "r2592000",
    }
    work_mode = {
        "any": "",
        "onsite": "1",
        "remote": "2",
        "hybrid": "3",
    }
    easy_apply = {"any": "", "easy_apply": "true"}

    def build_search_plans(
        self,
        preferences: JobPreference,
        keywords: list[str] | None = None,
        location: str | None = None,
        date_since_posted: str = "past_week",
        work_mode: str = "any",
        easy_apply: str = "any",
        limit: int = 6,
    ) -> list[LinkedInSearchPlan]:
        selected_keywords = keywords or [*preferences.target_roles, *preferences.similar_roles]
        selected_locations = [location] if location else preferences.preferred_locations or ["India"]
        plans: list[LinkedInSearchPlan] = []
        for keyword in selected_keywords[:limit]:
            for selected_location in selected_locations[:2]:
                params = {
                    "keywords": keyword,
                    "location": selected_location,
                    "f_TPR": self.date_posted.get(date_since_posted, ""),
                    "f_WT": self.work_mode.get(work_mode, ""),
                    "f_AL": self.easy_apply.get(easy_apply, ""),
                    "sortBy": "DD",
                }
                clean = {key: value for key, value in params.items() if value}
                plans.append(
                    LinkedInSearchPlan(
                        url=f"https://www.linkedin.com/jobs/search/?{urlencode(clean)}",
                        keyword=keyword,
                        location=selected_location,
                        filters={
                            "date_since_posted": date_since_posted,
                            "work_mode": work_mode,
                            "easy_apply": easy_apply,
                            "sort": "most_recent",
                        },
                        safety_notes=[
                            "Open this URL manually in your browser.",
                            "Use visible job data only; do not bypass login, CAPTCHA, or access controls.",
                            "Import interesting jobs for scoring and resume tailoring before applying.",
                            "The app will not submit LinkedIn applications automatically.",
                        ],
                    )
                )
        return plans

    def parse_visible_job_text(self, text: str) -> dict:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        title = lines[0] if lines else "LinkedIn Imported Role"
        company = lines[1] if len(lines) > 1 else "Unknown Company"
        location = lines[2] if len(lines) > 2 else None
        return {
            "title": title[:180],
            "company": company[:180],
            "location": location,
            "description": text[:8000],
            "skills": extract_keywords(text, limit=16),
        }

    def application_checklist(self, user: User) -> list[str]:
        missing = []
        if not user.base_resume_text and not user.base_resume_path:
            missing.append("Upload or paste a base resume before tailoring.")
        if not user.phone:
            missing.append("Add a phone number for application forms.")
        if not user.linkedin_url:
            missing.append("Add LinkedIn profile URL.")
        if not user.skills:
            missing.append("Add skills so the match engine can score jobs.")
        if missing:
            return missing
        return [
            "Profile data is ready.",
            "Resume tailoring remains truthful and review-first.",
            "Sensitive questions must be answered by the user.",
            "Submission must happen manually on LinkedIn.",
        ]
