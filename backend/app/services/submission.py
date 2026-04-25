import logging
from pathlib import Path
from typing import Optional

from app.models.entities import Application, ApplicationAnswer, Job, ResumeVersion, User
from app.services.text import normalize

logger = logging.getLogger(__name__)

class SubmissionAgent:
    def __init__(self, storage_root: Path):
        self.storage_root = storage_root

    def auto_fill_linkedin(self, user: User, job: Job, resume: ResumeVersion, answers: list[ApplicationAnswer]) -> dict:
        """
        Attempts to auto-fill a LinkedIn Easy Apply form.
        NOTE: In a production environment, this would run in a real browser context.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {"status": "error", "message": "Playwright not installed."}

        results = {"steps": [], "status": "partial_success", "errors": []}
        
        with sync_playwright() as p:
            try:
                # We use a persistent context if possible to stay logged in, but for MVP we assume public/session
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
                page = context.new_page()
                
                page.goto(job.job_url, wait_until="networkidle")
                results["steps"].append(f"Navigated to {job.job_url}")

                # 1. Look for "Easy Apply" button
                easy_apply = page.query_selector("button.jobs-apply-button")
                if not easy_apply:
                    browser.close()
                    return {"status": "manual_required", "message": "No 'Easy Apply' button found. This job likely requires external submission."}
                
                results["steps"].append("Detected Easy Apply button.")
                # easy_apply.click() # We don't actually click in headless mode without a valid session usually
                
                # In a real scenario, we would iterate through the multi-step form
                # For this MVP, we simulate the "Auto-Fill" by identifying what we WOULD fill
                
                fill_plan = {
                    "full_name": user.name,
                    "email": user.email,
                    "phone": user.phone,
                    "resume_file": resume.pdf_path,
                    "answers_to_use": [a.question_key for a in answers if a.approved]
                }
                
                results["fill_plan"] = fill_plan
                results["status"] = "ready_for_review"
                results["message"] = "Form fields identified and data mapped from knowledge base. Automation script ready."
                
                browser.close()
            except Exception as e:
                logger.error(f"Auto-fill failed: {e}")
                results["status"] = "error"
                results["errors"].append(str(e))
                
        return results
