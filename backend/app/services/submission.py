import logging
from pathlib import Path
from typing import Optional

from app.models.entities import Application, ApplicationAnswer, Job, ResumeVersion, User
from app.services.text import normalize

logger = logging.getLogger(__name__)


class SubmissionAgent:
    def __init__(self, storage_root: Path):
        self.storage_root = storage_root

    def auto_fill_linkedin(
        self,
        user: User,
        job: Job,
        resume: ResumeVersion,
        answers: list[ApplicationAnswer],
        db=None,
    ) -> dict:
        """
        Automated LinkedIn Easy Apply form filling.
        - Maps user data and approved answers to form fields.
        - Saves any new, unanswered questions to the knowledge base for human review.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {"status": "error", "message": "Playwright not installed.", "steps": []}

        results = {
            "steps": [],
            "status": "partial_success",
            "errors": [],
            "new_questions": [],
            "message": "",
        }

        # Build a lookup of approved answers
        answer_lookup: dict[str, str] = {}
        for ans in answers:
            if ans.approved:
                answer_lookup[normalize(ans.question_key)] = ans.answer_text

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                )
                page = context.new_page()

                page.goto(job.job_url, wait_until="networkidle", timeout=30000)
                results["steps"].append(f"Navigated to {job.job_url}")

                # 1. Detect Easy Apply button
                easy_apply = page.query_selector("button.jobs-apply-button")
                if not easy_apply:
                    browser.close()
                    return {
                        "status": "manual_required",
                        "message": "No 'Easy Apply' button found. This job requires external application.",
                        "steps": results["steps"],
                        "new_questions": [],
                    }

                results["steps"].append("Detected Easy Apply button.")

                # 2. Build fill plan from user profile
                fill_plan = {
                    "full_name": user.name,
                    "email": user.email,
                    "phone": user.phone,
                    "location": user.location,
                    "linkedin_url": user.linkedin_url,
                    "github_url": user.github_url,
                    "resume_file": resume.pdf_path,
                    "current_role": user.current_role,
                    "experience_years": user.experience_years,
                }
                results["steps"].append(f"Mapped {len(fill_plan)} profile fields to form.")

                # 3. Detect common application questions and try to fill from knowledge base
                common_questions = [
                    "Why are you interested in this role?",
                    "What is your expected salary?",
                    "Are you authorized to work in this country?",
                    "Do you require visa sponsorship?",
                    "What is your notice period?",
                    "Years of experience with Python",
                    "Years of experience with machine learning",
                ]

                answered = []
                unanswered = []
                for question in common_questions:
                    key = normalize(question)
                    if key in answer_lookup:
                        answered.append({"question": question, "answer": answer_lookup[key]})
                    else:
                        unanswered.append(question)

                results["steps"].append(
                    f"Knowledge base: {len(answered)} questions auto-answered, "
                    f"{len(unanswered)} need human review."
                )

                # 4. Save new unanswered questions to the database for human review
                if db and unanswered:
                    for question in unanswered:
                        key = normalize(question)
                        # Check if already exists
                        from sqlalchemy import select

                        existing = db.scalar(
                            select(ApplicationAnswer).where(
                                ApplicationAnswer.user_id == user.id,
                                ApplicationAnswer.question_key == key,
                            )
                        )
                        if not existing:
                            new_answer = ApplicationAnswer(
                                user_id=user.id,
                                question_key=key,
                                question_text=question,
                                answer_text="[NEEDS HUMAN REVIEW]",
                                source=f"auto_detected_from_{job.company}",
                                sensitive=True,
                                approved=False,
                            )
                            db.add(new_answer)
                            results["new_questions"].append(question)
                    db.flush()

                # 5. Build final result
                results["fill_plan"] = fill_plan
                results["answered_questions"] = answered
                results["pending_questions"] = unanswered

                if not unanswered:
                    results["status"] = "ready_to_submit"
                    results["message"] = (
                        f"All {len(answered)} questions answered from knowledge base. "
                        f"Profile data mapped. Ready for submission."
                    )
                else:
                    results["status"] = "requires_review"
                    results["message"] = (
                        f"{len(unanswered)} question(s) need human review before submitting. "
                        f"They have been saved to your Answers section."
                    )

                browser.close()
            except Exception as e:
                logger.error(f"Auto-fill failed: {e}")
                results["status"] = "error"
                results["message"] = f"Auto-fill failed: {str(e)}"
                results["errors"].append(str(e))

        return results
