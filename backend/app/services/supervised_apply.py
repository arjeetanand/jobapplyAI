from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.models.entities import ApplicationAnswer, Job, User
from app.services.text import normalize

logger = logging.getLogger(__name__)


@dataclass
class SupervisedApplyResult:
    status: str
    message: str
    steps: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    missing_questions: list[str] = field(default_factory=list)
    fill_report: dict = field(default_factory=dict)
    action_required: str | None = None


class SupervisedLinkedInApplyAgent:
    _active: dict | None = None
    _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="seekapply-linkedin-apply")

    def __init__(self, storage_root: Path) -> None:
        self.storage_root = storage_root
        self.session_dir = storage_root / "browser_sessions"
        self.profile_dir = self.session_dir / "linkedin_apply"
        self.state_path = self.session_dir / "linkedin_apply_state.json"

    def start(
        self,
        *,
        task_id: int,
        user: User,
        job: Job,
        resume_path: Path,
        answers: list[ApplicationAnswer],
        wait_seconds: int = 90,
    ) -> SupervisedApplyResult:
        if not resume_path.exists():
            return SupervisedApplyResult(
                status="failed",
                message="Resume file was not found on disk.",
                errors=[str(resume_path)],
            )

        return self.__class__._executor.submit(
            self._start_on_worker,
            task_id=task_id,
            user=user,
            job=job,
            resume_path=resume_path,
            answers=answers,
            wait_seconds=wait_seconds,
        ).result()

    def _start_on_worker(
        self,
        *,
        task_id: int,
        user: User,
        job: Job,
        resume_path: Path,
        answers: list[ApplicationAnswer],
        wait_seconds: int,
    ) -> SupervisedApplyResult:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError:
            return SupervisedApplyResult(
                status="failed",
                message="Playwright is not installed in the backend environment.",
                action_required="Install Playwright and Chromium, then retry.",
            )

        if self.__class__._active and self.__class__._active.get("task_id") != task_id:
            return SupervisedApplyResult(
                status="failed",
                message="Another supervised application browser is already open.",
                action_required="Finish or mark the active application before starting another.",
            )

        is_linkedin = "linkedin.com" in (job.job_url or "").lower()
        steps = [
            "Opening supervised LinkedIn Easy Apply browser."
            if is_linkedin
            else "Opening supervised external application browser."
        ]
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        try:
            if not self.__class__._active:
                playwright = sync_playwright().start()
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    headless=False,
                    viewport={"width": 1360, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
                    ),
                    accept_downloads=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                page = context.pages[0] if context.pages else context.new_page()
                self.__class__._active = {
                    "task_id": task_id,
                    "playwright": playwright,
                    "context": context,
                    "page": page,
                    "state_path": self.state_path,
                }
            else:
                context = self.__class__._active["context"]
                page = self.__class__._active["page"]

            start_url = job.job_url or job.apply_url
            page.goto(start_url, wait_until="domcontentloaded", timeout=max(wait_seconds, 15) * 1000)
            steps.append(f"Opened {start_url}.")
            page.wait_for_timeout(1500)

            if self._needs_user_login(page):
                logged_in = self._wait_for_login(page, start_url, wait_seconds, steps)
                if not logged_in:
                    self._save_state(context)
                    return SupervisedApplyResult(
                        status="needs_login",
                        message="The application site needs login, verification, or CAPTCHA before SeekApply can continue.",
                        steps=steps,
                        action_required=(
                            "Complete login or verification in the visible browser, then click Resume in SeekApply. "
                            "After one successful login, SeekApply reuses this saved browser session for later queue jobs."
                        ),
                    )

            answer_lookup = self._answer_lookup(answers)
            fill_report = {
                "resume_uploaded": False,
                "profile_fields_filled": 0,
                "answers_filled": 0,
                "final_submit_detected": False,
                "session_profile": str(self.profile_dir),
                "mode": "linkedin_easy_apply" if is_linkedin else "external_site",
            }

            if is_linkedin and self._click_easy_apply(page):
                steps.append("Opened the Easy Apply modal.")
                page.wait_for_timeout(1200)
                return self._fill_supervised_form(
                    page=page,
                    user=user,
                    resume_path=resume_path,
                    answer_lookup=answer_lookup,
                    steps=steps,
                    fill_report=fill_report,
                    final_action_label="Review the visible LinkedIn form, submit manually, then mark submitted in SeekApply.",
                    unsupported_message="Could not reach LinkedIn's final review/submit step.",
                    unsupported_error="The LinkedIn form may have changed or contains unsupported controls.",
                    next_step_label="Advanced to the next Easy Apply step.",
                    context=context,
                )

            if is_linkedin:
                steps.append("LinkedIn Easy Apply was not available; switching to the external apply/site link.")
                fill_report["mode"] = "external_from_linkedin"
                opened_external = self._open_external_apply(page, job, steps)
                if not opened_external:
                    self._save_state(context)
                    return SupervisedApplyResult(
                        status="needs_user_action",
                        message="LinkedIn Easy Apply was not found, and no external Apply link could be opened automatically.",
                        steps=steps,
                        fill_report=fill_report,
                        action_required="Use the visible browser to open the company apply link, then click Resume or Mark Submitted in SeekApply.",
                    )
                page.wait_for_timeout(1500)
            else:
                self._open_external_apply(page, job, steps)
                page.wait_for_timeout(1200)

            return self._fill_supervised_form(
                page=page,
                user=user,
                resume_path=resume_path,
                answer_lookup=answer_lookup,
                steps=steps,
                fill_report=fill_report,
                final_action_label="Review the visible external application form, submit manually, then mark submitted in SeekApply.",
                unsupported_message="Opened the external application site, but some steps need manual review.",
                unsupported_error="External portal controls vary; finish any unsupported fields in the visible browser.",
                next_step_label="Advanced to the next external application step.",
                context=context,
                allow_manual_finish=True,
            )
        except PlaywrightTimeoutError as exc:
            logger.exception("Supervised apply timed out")
            self.__class__._close_on_worker(task_id)
            return SupervisedApplyResult(
                status="failed",
                message="Supervised apply timed out.",
                steps=steps,
                errors=[str(exc)],
            )
        except PlaywrightError as exc:
            message = str(exc)
            if "ProcessSingleton" in message or "SingletonLock" in message or "profile directory" in message:
                self.__class__._active = None
                return SupervisedApplyResult(
                    status="failed",
                    message="The LinkedIn apply browser profile is already open.",
                    steps=steps,
                    errors=[message],
                    action_required="Close the existing LinkedIn apply browser window, then click Start/Resume again.",
                )
            logger.exception("Supervised apply browser error")
            self.__class__._close_on_worker(task_id)
            return SupervisedApplyResult(
                status="failed",
                message=f"Supervised apply browser error: {exc}",
                steps=steps,
                errors=[message],
            )
        except Exception as exc:
            logger.exception("Supervised apply automation failed")
            self.__class__._close_on_worker(task_id)
            return SupervisedApplyResult(
                status="failed",
                message=f"Supervised apply automation failed: {exc}",
                steps=steps,
                errors=[str(exc)],
            )

    @classmethod
    def close(cls, task_id: int | None = None) -> None:
        cls._executor.submit(cls._close_on_worker, task_id).result()

    @classmethod
    def _close_on_worker(cls, task_id: int | None = None) -> None:
        active = cls._active
        if not active:
            return
        if task_id is not None and active.get("task_id") != task_id:
            return
        context = active.get("context")
        try:
            state_path = active.get("state_path")
            if state_path:
                Path(state_path).parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(state_path))
        except Exception:
            pass
        try:
            context.close()
        except Exception:
            pass
        try:
            active["playwright"].stop()
        except Exception:
            pass
        cls._active = None

    def _save_state(self, context) -> None:
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(self.state_path))
        except Exception as exc:
            logger.warning("Could not save LinkedIn apply session: %s", exc)

    def _wait_for_login(self, page, job_url: str, wait_seconds: int, steps: list[str]) -> bool:
        steps.append("Application site asked for login or verification; waiting in the visible browser.")
        deadline = time.monotonic() + max(wait_seconds, 15)
        while time.monotonic() < deadline:
            page.wait_for_timeout(2000)
            try:
                if not self._needs_user_login(page):
                    if "linkedin.com" in job_url.lower() and "linkedin.com/jobs" not in (page.url or "").lower():
                        page.goto(job_url, wait_until="domcontentloaded", timeout=30_000)
                        page.wait_for_timeout(1500)
                    steps.append("Application site login/session is available.")
                    return True
            except Exception:
                continue
        return False

    def _fill_supervised_form(
        self,
        *,
        page,
        user: User,
        resume_path: Path,
        answer_lookup: dict[str, str],
        steps: list[str],
        fill_report: dict,
        final_action_label: str,
        unsupported_message: str,
        unsupported_error: str,
        next_step_label: str,
        context,
        allow_manual_finish: bool = False,
    ) -> SupervisedApplyResult:
        for _ in range(5):
            uploaded = self._upload_resume(page, resume_path)
            fill_report["resume_uploaded"] = fill_report["resume_uploaded"] or uploaded
            filled = self._fill_visible_fields(page, user, answer_lookup)
            fill_report["profile_fields_filled"] += filled["profile_fields_filled"]
            fill_report["answers_filled"] += filled["answers_filled"]
            missing_questions = self._missing_questions(page, answer_lookup)
            if missing_questions:
                self._save_state(context)
                return SupervisedApplyResult(
                    status="needs_answers",
                    message=f"{len(missing_questions)} question(s) need answers before continuing.",
                    steps=steps,
                    missing_questions=missing_questions,
                    fill_report=fill_report,
                    action_required="Answer and approve these questions in SeekApply, then click Resume.",
                )
            if self._final_submit_visible(page):
                fill_report["final_submit_detected"] = True
                self._save_state(context)
                return SupervisedApplyResult(
                    status="ready_for_submit",
                    message="Application form is filled and paused before final Submit.",
                    steps=[*steps, "Stopped before final submit."],
                    fill_report=fill_report,
                    action_required=final_action_label,
                )
            if not self._click_next_step(page):
                break
            steps.append(next_step_label)
            page.wait_for_timeout(900)

        if self._final_submit_visible(page):
            fill_report["final_submit_detected"] = True
            self._save_state(context)
            return SupervisedApplyResult(
                status="ready_for_submit",
                message="Application form is filled and paused before final Submit.",
                steps=[*steps, "Stopped before final submit."],
                fill_report=fill_report,
                action_required=final_action_label,
            )

        self._save_state(context)
        if allow_manual_finish:
            return SupervisedApplyResult(
                status="needs_user_action",
                message=unsupported_message,
                steps=steps,
                fill_report=fill_report,
                errors=[unsupported_error],
                action_required="Continue in the visible browser. SeekApply will keep the task open so you can mark it submitted after manual review.",
            )
        return SupervisedApplyResult(
            status="failed",
            message=unsupported_message,
            steps=steps,
            fill_report=fill_report,
            errors=[unsupported_error],
            action_required="Finish this application manually in the visible browser.",
        )

    @staticmethod
    def _open_external_apply(page, job: Job, steps: list[str]) -> bool:
        apply_url = (job.apply_url or "").strip()
        current_url = (page.url or job.job_url or "").strip()
        if apply_url and apply_url != current_url:
            try:
                page.goto(apply_url, wait_until="domcontentloaded", timeout=45_000)
                steps.append(f"Opened external apply URL: {apply_url}.")
                return True
            except Exception:
                steps.append("External apply URL could not be opened directly; trying the visible Apply button.")

        try:
            href = page.evaluate(
                """
                () => {
                  const candidates = Array.from(document.querySelectorAll('a[href], button'));
                  const match = candidates.find((el) => {
                    const text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
                    return /(apply|apply now|continue application|external apply|company website)/i.test(text);
                  });
                  if (!match) return '';
                  if (match.href) return match.href;
                  match.click();
                  return 'clicked';
                }
                """
            )
            if href and href != "clicked":
                page.goto(href, wait_until="domcontentloaded", timeout=45_000)
                steps.append(f"Opened external apply link: {href}.")
                return True
            if href == "clicked":
                steps.append("Clicked the visible Apply button on the job page.")
                return True
        except Exception:
            return False
        return False

    @staticmethod
    def _needs_user_login(page) -> bool:
        url = (page.url or "").lower()
        if any(marker in url for marker in ["login", "checkpoint", "challenge"]):
            return True
        body = normalize(page.locator("body").inner_text(timeout=3000)[:3000])
        return any(marker in body for marker in ["sign in", "security verification", "captcha", "verify you are human"])

    @staticmethod
    def _click_easy_apply(page) -> bool:
        return bool(
            page.evaluate(
                """
                () => {
                  const buttons = Array.from(document.querySelectorAll('button'));
                  const button = buttons.find((el) => /easy apply/i.test(el.innerText || el.getAttribute('aria-label') || ''));
                  if (!button) return false;
                  button.click();
                  return true;
                }
                """
            )
        )

    @staticmethod
    def _upload_resume(page, resume_path: Path) -> bool:
        inputs = page.query_selector_all('input[type="file"]')
        uploaded = False
        for input_el in inputs[:3]:
            try:
                input_el.set_input_files(str(resume_path))
                uploaded = True
            except Exception:
                continue
        return uploaded

    @staticmethod
    def _answer_lookup(answers: list[ApplicationAnswer]) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for answer in answers:
            if answer.approved and answer.answer_text.strip() and answer.answer_text != "[NEEDS HUMAN REVIEW]":
                lookup[normalize(answer.question_key)] = answer.answer_text
                lookup[normalize(answer.question_text)] = answer.answer_text
        return lookup

    @staticmethod
    def _fill_visible_fields(page, user: User, answer_lookup: dict[str, str]) -> dict:
        return page.evaluate(
            """
            ({profile, answers}) => {
              function labelFor(el) {
                const id = el.getAttribute('id');
                const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                const parentLabel = el.closest('label');
                const wrapper = el.closest('.jobs-easy-apply-form-section__grouping, .fb-dash-form-element, .artdeco-text-input--container, fieldset, .form-group, .field, .input, .application-question');
                return [
                  el.getAttribute('aria-label'),
                  el.getAttribute('placeholder'),
                  el.getAttribute('name'),
                  label && label.innerText,
                  parentLabel && parentLabel.innerText,
                  wrapper && wrapper.innerText,
                ].filter(Boolean).join(' ').trim();
              }
              function norm(value) {
                return (value || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
              }
              function profileValue(label) {
                const n = norm(label);
                if (n.includes('email')) return profile.email || '';
                if (n.includes('phone') || n.includes('mobile')) return profile.phone || '';
                if (n.includes('first name')) return (profile.name || '').split(/\\s+/)[0] || '';
                if (n.includes('last name')) return (profile.name || '').split(/\\s+/).slice(1).join(' ') || '';
                if (n === 'name' || n.includes('full name')) return profile.name || '';
                if (n.includes('city') || n.includes('location')) return profile.location || '';
                if (n.includes('linkedin')) return profile.linkedin_url || '';
                if (n.includes('github')) return profile.github_url || '';
                if (n.includes('website') || n.includes('portfolio')) return profile.portfolio_url || profile.github_url || '';
                return '';
              }
              function answerValue(label) {
                const n = norm(label);
                for (const [key, value] of Object.entries(answers)) {
                  const k = norm(key);
                  if (k && (n.includes(k) || k.includes(n))) return value;
                }
                return '';
              }
              let profileCount = 0;
              let answerCount = 0;
              const fields = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]), textarea'));
              for (const el of fields) {
                if (el.disabled || el.readOnly || (el.value || '').trim()) continue;
                const label = labelFor(el);
                const profileFill = profileValue(label);
                const answerFill = answerValue(label);
                const value = profileFill || answerFill;
                if (!value) continue;
                el.focus();
                el.value = value;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                if (profileFill) profileCount += 1;
                else answerCount += 1;
              }
              return {profile_fields_filled: profileCount, answers_filled: answerCount};
            }
            """,
            {
                "profile": {
                    "name": user.name,
                    "email": user.email,
                    "phone": user.phone,
                    "location": user.location,
                    "linkedin_url": user.linkedin_url,
                    "github_url": user.github_url,
                    "portfolio_url": getattr(user, "portfolio_url", None),
                },
                "answers": answer_lookup,
            },
        )

    @staticmethod
    def _missing_questions(page, answer_lookup: dict[str, str]) -> list[str]:
        questions = page.evaluate(
            """
            () => {
              function labelFor(el) {
                const id = el.getAttribute('id');
                const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                const wrapper = el.closest('.jobs-easy-apply-form-section__grouping, .fb-dash-form-element, fieldset, .form-group, .field, .input, .application-question');
                return [
                  el.getAttribute('aria-label'),
                  el.getAttribute('placeholder'),
                  label && label.innerText,
                  wrapper && wrapper.innerText,
                ].filter(Boolean).join(' ').trim();
              }
              const out = [];
              const controls = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="file"]), textarea, select'));
              for (const el of controls) {
                const required = el.required || el.getAttribute('aria-required') === 'true';
                const type = (el.getAttribute('type') || '').toLowerCase();
                const empty = type === 'radio' || type === 'checkbox'
                  ? !document.querySelector(`input[name="${CSS.escape(el.name || '')}"]:checked`)
                  : !(el.value || '').trim();
                if (!required || !empty) continue;
                const label = labelFor(el).replace(/\\s+/g, ' ').trim();
                if (label && !out.includes(label)) out.push(label);
              }
              return out.slice(0, 12);
            }
            """
        )
        approved_keys = set(answer_lookup.keys())
        missing = []
        for question in questions:
            normalized = normalize(question)
            if not any(key and (normalized in key or key in normalized) for key in approved_keys):
                missing.append(question[:500])
        return missing

    @staticmethod
    def _final_submit_visible(page) -> bool:
        return bool(
            page.evaluate(
                """
                () => Array.from(document.querySelectorAll('button, input[type="submit"]')).some((el) =>
                  /(submit application|submit|send application|finish application|review application)/i.test(
                    el.innerText || el.value || el.getAttribute('aria-label') || ''
                  )
                )
                """
            )
        )

    @staticmethod
    def _click_next_step(page) -> bool:
        return bool(
            page.evaluate(
                """
                () => {
                  const buttons = Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"]'));
                  const button = buttons.find((el) => {
                    const text = el.innerText || el.value || el.getAttribute('aria-label') || '';
                    return /^(next|review|continue|save and continue|next step)$/i.test(text.trim()) && !/submit/i.test(text);
                  });
                  if (!button) return false;
                  button.click();
                  return true;
                }
                """
            )
        )
