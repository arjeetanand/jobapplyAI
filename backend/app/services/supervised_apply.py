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
            resumed_active_browser = bool(self.__class__._active and self.__class__._active.get("task_id") == task_id)
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
            if resumed_active_browser and (page.url or "").lower().startswith(("http://", "https://")):
                steps.append(f"Resuming visible browser at {page.url}.")
            else:
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

            current_is_linkedin = "linkedin.com" in (page.url or "").lower()
            if resumed_active_browser and self._application_form_present(page):
                fill_report["mode"] = "linkedin_easy_apply" if current_is_linkedin else "external_from_linkedin"
                steps.append("Continuing the currently open application form.")
                return self._fill_supervised_form(
                    page=page,
                    user=user,
                    resume_path=resume_path,
                    answer_lookup=answer_lookup,
                    steps=steps,
                    fill_report=fill_report,
                    final_action_label=(
                        "Review the visible LinkedIn form, submit manually, then mark submitted in SeekApply."
                        if current_is_linkedin
                        else "Review the visible external application form, submit manually, then mark submitted in SeekApply."
                    ),
                    unsupported_message=(
                        "Could not reach LinkedIn's final review/submit step."
                        if current_is_linkedin
                        else "Opened the external application site, but some steps need manual review."
                    ),
                    unsupported_error=(
                        "The LinkedIn form may have changed or contains unsupported controls."
                        if current_is_linkedin
                        else "External portal controls vary; finish any unsupported fields in the visible browser."
                    ),
                    next_step_label=(
                        "Advanced to the next Easy Apply step."
                        if current_is_linkedin
                        else "Advanced to the next external application step."
                    ),
                    context=context,
                    allow_manual_finish=not current_is_linkedin,
                )

            if is_linkedin and not current_is_linkedin:
                fill_report["mode"] = "external_from_linkedin"
                steps.append("Continuing the current external application page.")
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

            if is_linkedin:
                easy_apply = self._click_easy_apply(page, wait_seconds=min(wait_seconds, 30))
                fill_report["easy_apply_detection"] = easy_apply
            else:
                easy_apply = {"clicked": False, "reason": "not_linkedin_job"}

            if is_linkedin and easy_apply.get("clicked"):
                clicked_text = easy_apply.get("clicked_text") or "LinkedIn apply button"
                steps.append(f"Opened the LinkedIn apply flow from: {clicked_text}.")
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
                reason = easy_apply.get("reason") or "linkedin_apply_not_found"
                visible_apply_buttons = easy_apply.get("visible_apply_buttons") or []
                if visible_apply_buttons:
                    steps.append(
                        "LinkedIn in-page Apply was not opened "
                        f"({reason}); visible apply actions were: {', '.join(visible_apply_buttons[:6])}."
                    )
                else:
                    steps.append(f"LinkedIn in-page Apply was not opened ({reason}); no visible apply action was detected.")
                steps.append("Switching to the external apply/site link.")
                fill_report["mode"] = "external_from_linkedin"
                external_page = self._open_external_apply(page, job, steps, context=context)
                if not external_page:
                    self._save_state(context)
                    return SupervisedApplyResult(
                        status="needs_user_action",
                        message="LinkedIn in-page Apply was not found, and no external Apply link could be opened automatically.",
                        steps=steps,
                        fill_report=fill_report,
                        action_required=(
                            "Use the visible browser to open the company apply link, then click Resume or Mark Submitted in SeekApply. "
                            "If the page actually has the blue LinkedIn Apply button, click Debug and check the detected apply-button text."
                        ),
                    )
                page = external_page
                self.__class__._active["page"] = page
                page.wait_for_timeout(1500)
            else:
                external_page = self._open_external_apply(page, job, steps, context=context)
                if external_page:
                    page = external_page
                    self.__class__._active["page"] = page
                page.wait_for_timeout(1200)

            if self._needs_user_login(page):
                logged_in = self._wait_for_login(page, page.url or start_url, wait_seconds, steps)
                if not logged_in:
                    self._save_state(context)
                    return SupervisedApplyResult(
                        status="needs_login",
                        message="The external application site needs login, verification, or CAPTCHA before SeekApply can continue.",
                        steps=steps,
                        fill_report=fill_report,
                        action_required="Complete login or verification in the visible browser, then click Resume in SeekApply.",
                    )

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

    @staticmethod
    def _page_snapshot(page) -> dict:
        try:
            snapshot = page.evaluate(
                """
                () => {
                  function textFor(el) {
                    return [
                      el.getAttribute('aria-label'),
                      el.getAttribute('title'),
                      el.getAttribute('name'),
                      el.getAttribute('placeholder'),
                      el.value,
                      el.innerText,
                      el.textContent,
                    ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
                  }
                  function visible(el) {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                      && style.display !== 'none'
                      && rect.width > 0
                      && rect.height > 0
                      && !el.closest('[aria-hidden="true"]');
                  }
                  function labelFor(el) {
                    const id = el.getAttribute('id');
                    const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                    const wrapper = el.closest('label, fieldset, .form-group, .field, .input, .application-question, .fb-dash-form-element, .jobs-easy-apply-form-section__grouping');
                    return [
                      el.getAttribute('aria-label'),
                      el.getAttribute('placeholder'),
                      el.getAttribute('name'),
                      label && label.innerText,
                      wrapper && wrapper.innerText,
                    ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
                  }
                  const buttons = Array.from(document.querySelectorAll('button, a[href], [role="button"], input[type="button"], input[type="submit"]'))
                    .filter(visible)
                    .map(textFor)
                    .filter(Boolean);
                  const fields = Array.from(document.querySelectorAll('input:not([type="hidden"]), textarea, select, [contenteditable="true"], [role="combobox"]'))
                    .filter(visible)
                    .map(labelFor)
                    .filter(Boolean);
                  return {
                    page_title: document.title || '',
                    buttons_seen: Array.from(new Set(buttons)).slice(0, 30),
                    fields_seen: Array.from(new Set(fields)).slice(0, 30),
                    visible_file_inputs: Array.from(document.querySelectorAll('input[type="file"]')).filter(visible).length,
                    form_count: document.querySelectorAll('form').length,
                  };
                }
                """
            )
            return snapshot if isinstance(snapshot, dict) else {}
        except Exception:
            return {}

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
        for _ in range(10):
            fill_report["current_url"] = page.url
            fill_report.update(self._page_snapshot(page))
            if self._needs_user_login(page):
                logged_in = self._wait_for_login(page, page.url or "", 90, steps)
                if not logged_in:
                    self._save_state(context)
                    return SupervisedApplyResult(
                        status="needs_login",
                        message="The application site needs login, verification, or CAPTCHA before SeekApply can continue.",
                        steps=steps,
                        fill_report=fill_report,
                        action_required="Complete login or verification in the visible browser, then click Resume in SeekApply.",
                    )
            uploaded = self._upload_resume(page, resume_path)
            fill_report["resume_uploaded"] = fill_report["resume_uploaded"] or uploaded
            filled = self._fill_visible_fields(page, user, answer_lookup)
            custom_choice_filled = self._fill_custom_choice_controls(page, user, answer_lookup)
            fill_report["profile_fields_filled"] += filled["profile_fields_filled"]
            fill_report["answers_filled"] += filled["answers_filled"] + custom_choice_filled["answers_filled"]
            fill_report["profile_fields_filled"] += custom_choice_filled["profile_fields_filled"]
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
            if allow_manual_finish:
                start_result = self._click_start_application(page, context=context)
                if start_result.get("clicked"):
                    page = start_result.get("page") or page
                    if self.__class__._active:
                        self.__class__._active["page"] = page
                    steps.append(
                        f"Clicked the visible external Apply/Start application button"
                        f"{': ' + start_result.get('text') if start_result.get('text') else ''}."
                    )
                    page.wait_for_timeout(1500)
                    continue
            next_result = self._click_next_step(page, context=context)
            if not next_result.get("clicked"):
                fill_report["last_click_blocker"] = next_result.get("reason") or "no_next_or_continue_button"
                if next_result.get("buttons_seen"):
                    fill_report["buttons_seen"] = next_result["buttons_seen"]
                break
            page = next_result.get("page") or page
            if self.__class__._active:
                self.__class__._active["page"] = page
            steps.append(
                f"{next_step_label}"
                f"{' Button: ' + next_result.get('text') if next_result.get('text') else ''}"
            )
            page.wait_for_timeout(1500)

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
            total_filled = int(fill_report.get("profile_fields_filled") or 0) + int(fill_report.get("answers_filled") or 0)
            resume_uploaded = bool(fill_report.get("resume_uploaded"))
            if total_filled or resume_uploaded:
                completed = []
                if resume_uploaded:
                    completed.append("uploaded the resume")
                if total_filled:
                    completed.append(f"filled {total_filled} field(s)")
                message = f"SeekApply {' and '.join(completed)}, then paused for external-site review."
            else:
                message = "Opened the external application site, but no supported application form was detected yet."
                unsupported_error = (
                    "No supported form or resume upload control was detected on the current page. "
                    "The portal may require a manual click, login, or a custom widget that needs review."
                )
            fill_report["manual_review_reason"] = unsupported_error
            return SupervisedApplyResult(
                status="needs_user_action",
                message=message or unsupported_message,
                steps=steps,
                fill_report=fill_report,
                errors=[unsupported_error],
                action_required="Continue in the visible browser. SeekApply will keep the task open so you can mark it submitted after manual review.",
            )
        total_filled = int(fill_report.get("profile_fields_filled") or 0) + int(fill_report.get("answers_filled") or 0)
        resume_uploaded = bool(fill_report.get("resume_uploaded"))
        if total_filled or resume_uploaded or fill_report.get("mode") == "linkedin_easy_apply":
            self._save_state(context)
            fill_report["manual_review_reason"] = unsupported_error
            return SupervisedApplyResult(
                status="needs_user_action",
                message=unsupported_message,
                steps=steps,
                fill_report=fill_report,
                errors=[unsupported_error],
                action_required="Continue in the visible browser or click Resume after handling the unsupported field.",
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
    def _open_external_apply(page, job: Job, steps: list[str], context=None):
        apply_url = (getattr(job, "apply_url", None) or "").strip()
        current_url = (page.url or job.job_url or "").strip()
        if apply_url and apply_url != current_url:
            try:
                page.goto(apply_url, wait_until="domcontentloaded", timeout=45_000)
                steps.append(f"Opened external apply URL: {apply_url}.")
                return page
            except Exception:
                steps.append("External apply URL could not be opened directly; trying the visible Apply button.")

        try:
            candidate = page.evaluate(
                """
                () => {
                  function textFor(el) {
                    return [
                      el.getAttribute('aria-label'),
                      el.getAttribute('title'),
                      el.getAttribute('data-control-name'),
                      el.innerText,
                      el.textContent,
                    ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
                  }
                  function visible(el) {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                      && style.display !== 'none'
                      && rect.width > 0
                      && rect.height > 0
                      && !el.closest('[aria-hidden="true"]');
                  }
                  function enabled(el) {
                    return !(el.disabled || el.getAttribute('disabled') !== null || el.getAttribute('aria-disabled') === 'true');
                  }
                  function primaryArea(el) {
                    const rect = el.getBoundingClientRect();
                    const topCard = el.closest('.jobs-unified-top-card, .job-details-jobs-unified-top-card, .jobs-details-top-card, .top-card-layout');
                    if (topCard) return true;
                    return rect.top >= 0 && rect.top < Math.max(760, window.innerHeight + 80)
                      && rect.left < window.innerWidth * 0.75
                      && !el.closest('aside, .jobs-details__right-rail, .job-card-container, .scaffold-layout__list');
                  }
                  const candidates = Array.from(document.querySelectorAll('a[href], button, [role="button"], input[type="button"], input[type="submit"]'))
                    .filter((el) => visible(el) && enabled(el));
                  const matches = candidates.filter((el) => {
                    const text = textFor(el);
                    return primaryArea(el)
                      && /(apply\\s+on\\s+company\\s+website|company\\s+website|external\\s+apply|apply\\s+now|apply\\s+for\\s+this\\s+job|^apply$)/i.test(text)
                      && !/(easy\\s+apply|submit|send|finish|save)/i.test(text);
                  });
                  const target = matches[0];
                  const visibleApplyButtons = candidates
                    .map(textFor)
                    .filter((text) => /\\bapply\\b|company website|continue application/i.test(text))
                    .map((text) => text.slice(0, 120));
                  if (!target) return {found: false, visible_apply_buttons: Array.from(new Set(visibleApplyButtons)).slice(0, 10)};
                  document.querySelectorAll('[data-seekapply-external-target="1"]').forEach((el) => el.removeAttribute('data-seekapply-external-target'));
                  target.setAttribute('data-seekapply-external-target', '1');
                  return {
                    found: true,
                    text: textFor(target).slice(0, 160),
                    href: target.href || target.getAttribute('href') || '',
                    visible_apply_buttons: Array.from(new Set(visibleApplyButtons)).slice(0, 10),
                  };
                }
                """
            )
            if not isinstance(candidate, dict) or not candidate.get("found"):
                if isinstance(candidate, dict) and candidate.get("visible_apply_buttons"):
                    steps.append(f"No primary external Apply button was clickable. Visible apply actions: {', '.join(candidate['visible_apply_buttons'][:6])}.")
                return None

            href = str(candidate.get("href") or "")
            if href and not href.lower().startswith(("javascript:", "#")):
                page.goto(href, wait_until="domcontentloaded", timeout=45_000)
                steps.append(f"Opened external apply link: {href}.")
                continued_page = SupervisedLinkedInApplyAgent._click_external_continue(page, steps, context=context)
                return continued_page or page

            before_pages = set(context.pages) if context else set()
            clicked_page = page
            try:
                if context:
                    try:
                        with context.expect_page(timeout=8000) as page_info:
                            page.locator('[data-seekapply-external-target="1"]').first.click(timeout=6000)
                        clicked_page = page_info.value
                        clicked_page.wait_for_load_state("domcontentloaded", timeout=45_000)
                    except Exception:
                        page.locator('[data-seekapply-external-target="1"]').first.click(timeout=6000)
                        page.wait_for_timeout(2500)
                        new_pages = [item for item in context.pages if item not in before_pages]
                        if new_pages:
                            clicked_page = new_pages[-1]
                            clicked_page.wait_for_load_state("domcontentloaded", timeout=45_000)
                else:
                    page.locator('[data-seekapply-external-target="1"]').first.click(timeout=6000)
                    page.wait_for_timeout(2500)
                steps.append(f"Clicked external Apply button: {candidate.get('text') or 'Apply'}.")
                continued_page = SupervisedLinkedInApplyAgent._click_external_continue(clicked_page, steps, context=context)
                return continued_page or clicked_page
            except Exception as exc:
                steps.append(f"External Apply button click failed: {exc}")
                return None
        except Exception as exc:
            steps.append(f"External Apply detection failed: {exc}")
            return None

    @staticmethod
    def _click_external_continue(page, steps: list[str], context=None):
        try:
            body = normalize(page.locator("body").inner_text(timeout=3000)[:2500])
        except Exception:
            body = ""
        url = (page.url or "").lower()
        if "linkedin.com" not in url and "leaving linkedin" not in body and "company website" not in body:
            return page
        try:
            result = page.evaluate(
                """
                () => {
                  function textFor(el) {
                    return [el.getAttribute('aria-label'), el.getAttribute('title'), el.innerText, el.textContent]
                      .filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
                  }
                  function visible(el) {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                  }
                  const candidates = Array.from(document.querySelectorAll('a[href], button, [role="button"]')).filter(visible);
                  const target = candidates.find((el) => {
                    const text = textFor(el);
                    return /(continue|visit site|company website|apply on company website|open application)/i.test(text)
                      && !/(cancel|back|submit|finish|send)/i.test(text);
                  });
                  if (!target) return {clicked: false};
                  target.setAttribute('data-seekapply-continue-target', '1');
                  return {clicked: true, text: textFor(target).slice(0, 160), href: target.href || target.getAttribute('href') || ''};
                }
                """
            )
            if not isinstance(result, dict) or not result.get("clicked"):
                return page
            href = str(result.get("href") or "")
            if href and not href.lower().startswith(("javascript:", "#")):
                page.goto(href, wait_until="domcontentloaded", timeout=45_000)
                steps.append(f"Opened LinkedIn external handoff link: {href}.")
                return page
            before_pages = set(context.pages) if context else set()
            if context:
                try:
                    with context.expect_page(timeout=6000) as page_info:
                        page.locator('[data-seekapply-continue-target="1"]').first.click(timeout=5000)
                    new_page = page_info.value
                    new_page.wait_for_load_state("domcontentloaded", timeout=45_000)
                    steps.append(f"Clicked LinkedIn external handoff: {result.get('text') or 'Continue'}.")
                    return new_page
                except Exception:
                    page.locator('[data-seekapply-continue-target="1"]').first.click(timeout=5000)
                    page.wait_for_timeout(1800)
                    new_pages = [item for item in context.pages if item not in before_pages]
                    if new_pages:
                        return new_pages[-1]
            else:
                page.locator('[data-seekapply-continue-target="1"]').first.click(timeout=5000)
            steps.append(f"Clicked LinkedIn external handoff: {result.get('text') or 'Continue'}.")
            return page
        except Exception:
            return page

    @staticmethod
    def _needs_user_login(page) -> bool:
        url = (page.url or "").lower()
        if any(marker in url for marker in ["login", "signin", "sign-in", "checkpoint", "challenge", "/auth", "authorize"]):
            return True
        try:
            has_visible_password = bool(
                page.evaluate(
                    """
                    () => {
                      function visible(el) {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                      }
                      return Array.from(document.querySelectorAll('input[type="password"]')).some(visible);
                    }
                    """
                )
            )
            if has_visible_password:
                return True
        except Exception:
            pass
        body = normalize(page.locator("body").inner_text(timeout=3000)[:3000])
        return any(
            marker in body
            for marker in [
                "sign in to continue",
                "log in to continue",
                "login to continue",
                "sign in to apply",
                "log in to apply",
                "login to apply",
                "please sign in",
                "please log in",
                "create an account or sign in",
                "security verification",
                "captcha",
                "verify you are human",
                "two factor authentication",
            ]
        )

    @staticmethod
    def _application_form_present(page) -> bool:
        try:
            return bool(
                page.evaluate(
                    """
                    () => {
                      function visible(el) {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                      }
                      const easyApplyModal = document.querySelector('.jobs-easy-apply-modal, .artdeco-modal, [role="dialog"]');
                      if (easyApplyModal && visible(easyApplyModal)) return true;
                      const controls = Array.from(document.querySelectorAll('input:not([type="hidden"]), textarea, select, [contenteditable="true"]')).filter(visible);
                      const meaningful = controls.filter((el) => {
                        const type = (el.getAttribute('type') || '').toLowerCase();
                        const label = [
                          el.getAttribute('aria-label'),
                          el.getAttribute('placeholder'),
                          el.getAttribute('name'),
                          el.closest('label') && el.closest('label').innerText,
                          el.closest('fieldset,.form-group,.field,.application-question,.fb-dash-form-element') && el.closest('fieldset,.form-group,.field,.application-question,.fb-dash-form-element').innerText,
                        ].filter(Boolean).join(' ').toLowerCase();
                        return type === 'file'
                          || /email|phone|mobile|name|resume|cv|linkedin|github|portfolio|experience|notice|salary|location|question|answer|cover/i.test(label);
                      });
                      return meaningful.length >= 1;
                    }
                    """
                )
            )
        except Exception:
            return False

    @staticmethod
    def _click_easy_apply(page, wait_seconds: int = 15) -> dict:
        deadline = time.monotonic() + max(3, min(wait_seconds, 30))
        last_result: dict = {"clicked": False, "reason": "linkedin_apply_not_found", "visible_apply_buttons": []}
        while time.monotonic() < deadline:
            result = page.evaluate(
                """
                () => {
                  // easy apply detector: keep this literal for lightweight tests and trace readability.
                  // LinkedIn sometimes labels Easy Apply as a blue "in Apply" button with no "Easy" text.
                  const PRIMARY_TOP_LIMIT = Math.max(760, Math.min(window.innerHeight + 80, 980));
                  function textFor(el) {
                    const direct = [
                      el.getAttribute('aria-label'),
                      el.getAttribute('title'),
                      el.getAttribute('data-control-name'),
                      el.getAttribute('value'),
                      el.innerText,
                      el.textContent,
                    ].filter(Boolean).join(' ');
                    return direct.replace(/\\s+/g, ' ').trim();
                  }
                  function compactText(el) {
                    return textFor(el).slice(0, 180);
                  }
                  function norm(value) {
                    return (value || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
                  }
                  function visible(el) {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                      && style.display !== 'none'
                      && rect.width > 0
                      && rect.height > 0
                      && rect.bottom >= -10
                      && rect.top <= window.innerHeight + 120
                      && !el.closest('[aria-hidden="true"]');
                  }
                  function enabled(el) {
                    return !(el.disabled
                      || el.getAttribute('disabled') !== null
                      || el.getAttribute('aria-disabled') === 'true'
                      || /disabled/i.test(el.className || ''));
                  }
                  function isPrimaryJobArea(el) {
                    const rect = el.getBoundingClientRect();
                    const topCard = el.closest(
                      '.jobs-unified-top-card, .job-details-jobs-unified-top-card, .jobs-details-top-card, .top-card-layout'
                    );
                    if (topCard) return true;
                    return rect.top >= 0
                      && rect.top <= PRIMARY_TOP_LIMIT
                      && rect.left < window.innerWidth * 0.72
                      && !el.closest('aside, .jobs-details__right-rail, .job-card-container, .scaffold-layout__list');
                  }
                  function isLinkedInApplyButton(el) {
                    const text = textFor(el);
                    const n = norm(text);
                    if (/company\\s+website|external\\s+apply|apply\\s+on\\s+company/i.test(text)) return false;
                    const className = String(el.className || '');
                    const control = String(el.getAttribute('data-control-name') || '');
                    const looksLinkedInApply =
                      /easy\\s*apply/i.test(text)
                      || /apply\\s+to\\s+(this\\s+job|.+\\s+at\\s+)/i.test(text)
                      || /linkedin\\s+apply|apply\\s+linkedin/i.test(text)
                      || /jobs-apply-button/i.test(className)
                      || /inapply|easyapply|jobdetails.*apply/i.test(control);
                    const primaryApplyText = n === 'apply' || n === 'in apply' || n.startsWith('apply to ');
                    return enabled(el) && isPrimaryJobArea(el) && (looksLinkedInApply || primaryApplyText);
                  }
                  const candidates = Array.from(document.querySelectorAll(
                    'button, a[href], [role="button"], div[role="button"], span[role="button"], input[type="button"], input[type="submit"]'
                  )).filter(visible);
                  const visibleApplyButtons = candidates
                    .map(compactText)
                    .filter((text) => /(easy\\s*apply|\\bapply\\b|continue application|company website)/i.test(text))
                    .filter((text) => text.length <= 180);
                  const easyApplyButtons = candidates.filter((el) => /easy\\s*apply/i.test(textFor(el)) && enabled(el));
                  const linkedinApplyButtons = candidates.filter(isLinkedInApplyButton);
                  const target = easyApplyButtons[0] || linkedinApplyButtons[0];
                  if (!target) {
                    return {
                      clicked: false,
                      reason: candidates.some((el) => /easy\\s*apply/i.test(textFor(el))) ? 'linkedin_apply_disabled' : 'linkedin_apply_not_found',
                      visible_apply_buttons: Array.from(new Set(visibleApplyButtons)).slice(0, 10),
                    };
                  }
                  target.scrollIntoView({ block: 'center', inline: 'center' });
                  target.click();
                  return {
                    clicked: true,
                    reason: /easy\\s*apply/i.test(textFor(target)) ? 'clicked_easy_apply' : 'clicked_linkedin_apply_button',
                    clicked_text: compactText(target),
                    visible_apply_buttons: Array.from(new Set(visibleApplyButtons)).slice(0, 10),
                  };
                }
                """
            )
            if isinstance(result, bool):
                return {"clicked": result, "reason": "legacy_detector", "visible_apply_buttons": []}
            if isinstance(result, dict):
                last_result = result
                if result.get("clicked"):
                    return result
            page.wait_for_timeout(1000)
        return last_result

    @staticmethod
    def _upload_resume(page, resume_path: Path) -> bool:
        uploaded = False
        if hasattr(page, "main_frame") and hasattr(page, "frames"):
            frames = [page.main_frame, *[frame for frame in page.frames if frame != page.main_frame]]
        else:
            frames = [page]
        for frame in frames:
            try:
                inputs = frame.query_selector_all('input[type="file"]')
            except Exception:
                inputs = []
            for input_el in inputs[:4]:
                try:
                    input_el.set_input_files(str(resume_path))
                    uploaded = True
                except Exception:
                    continue
        if uploaded:
            return True
        try:
            result = page.evaluate(
                """
                () => {
                  function textFor(el) {
                    return [
                      el.getAttribute('aria-label'),
                      el.getAttribute('title'),
                      el.getAttribute('name'),
                      el.innerText,
                      el.textContent,
                    ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
                  }
                  function visible(el) {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                  }
                  const target = Array.from(document.querySelectorAll('button, a[href], [role="button"], label, input[type="button"]'))
                    .filter((el) => visible(el))
                    .find((el) => /(upload|attach|choose|select).*(resume|cv|file)|resume|cv/i.test(textFor(el))
                      && !/(delete|remove|download|preview)/i.test(textFor(el)));
                  if (!target) return {found: false};
                  document.querySelectorAll('[data-seekapply-upload-target="1"]').forEach((el) => el.removeAttribute('data-seekapply-upload-target'));
                  target.setAttribute('data-seekapply-upload-target', '1');
                  return {found: true, text: textFor(target).slice(0, 160)};
                }
                """
            )
            if isinstance(result, dict) and result.get("found"):
                try:
                    with page.expect_file_chooser(timeout=5000) as chooser_info:
                        page.locator('[data-seekapply-upload-target="1"]').first.click(timeout=5000)
                    chooser_info.value.set_files(str(resume_path))
                    return True
                except Exception:
                    return False
        except Exception:
            return False
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
              function tokens(value) {
                return norm(value).split(' ').filter((token) => token.length > 2);
              }
              function tokenMatch(a, b) {
                const left = new Set(tokens(a));
                const right = new Set(tokens(b));
                if (!left.size || !right.size) return false;
                let overlap = 0;
                for (const token of left) if (right.has(token)) overlap += 1;
                const smaller = Math.min(left.size, right.size);
                return overlap >= 2 && overlap / smaller >= 0.55;
              }
              function profileValue(label) {
                const n = norm(label);
                if (n.includes('email')) return profile.email || '';
                if (n.includes('country code') || n.includes('phone code')) {
                  if ((profile.location || '').toLowerCase().includes('india') || String(profile.phone || '').startsWith('+91')) return 'India (+91)';
                  return '';
                }
                if (n.includes('phone') || n.includes('mobile')) {
                  const raw = String(profile.phone || '').trim();
                  const digits = raw.replace(/\\D/g, '');
                  if ((profile.location || '').toLowerCase().includes('india') && digits.length > 10 && digits.startsWith('91')) return digits.slice(-10);
                  return digits || raw;
                }
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
                  if (k && (n.includes(k) || k.includes(n) || tokenMatch(n, k))) return value;
                }
                return '';
              }
              let profileCount = 0;
              let answerCount = 0;
              const fields = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]), textarea, [contenteditable="true"]'));
              for (const el of fields) {
                const currentValue = el.isContentEditable ? (el.innerText || '') : (el.value || '');
                if (el.disabled || el.readOnly || currentValue.trim()) continue;
                const label = labelFor(el);
                const profileFill = profileValue(label);
                const answerFill = answerValue(label);
                const value = profileFill || answerFill;
                if (!value) continue;
                el.focus();
                if (el.isContentEditable) el.innerText = value;
                else el.value = value;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                if (profileFill) profileCount += 1;
                else answerCount += 1;
              }
              const selects = Array.from(document.querySelectorAll('select'));
              for (const el of selects) {
                const selected = el.options[el.selectedIndex];
                const selectedText = norm(selected ? (selected.innerText || selected.label || selected.value) : '');
                const hasRealSelection = (el.value || '').trim()
                  && el.selectedIndex > 0
                  && !/(select|choose|please select|--)/i.test(selectedText);
                if (el.disabled || hasRealSelection) continue;
                const label = labelFor(el);
                const value = answerValue(label) || profileValue(label);
                if (!value) continue;
                const wanted = norm(value);
                const option = Array.from(el.options).find((opt) => {
                  const text = norm(opt.innerText || opt.label || opt.value);
                  return text && (text === wanted || text.includes(wanted) || wanted.includes(text) || tokenMatch(text, wanted));
                });
                if (!option) continue;
                el.value = option.value;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                if (answerValue(label)) answerCount += 1;
                else profileCount += 1;
              }
              const radioNames = Array.from(new Set(Array.from(document.querySelectorAll('input[type="radio"]')).map((el) => el.name).filter(Boolean)));
              for (const name of radioNames) {
                if (document.querySelector(`input[name="${CSS.escape(name)}"]:checked`)) continue;
                const radios = Array.from(document.querySelectorAll(`input[name="${CSS.escape(name)}"]`));
                const label = labelFor(radios[0]);
                const value = answerValue(label);
                if (!value) continue;
                const wanted = norm(value);
                const radio = radios.find((el) => {
                  const optionLabel = labelFor(el);
                  const optionText = norm([optionLabel, el.value].filter(Boolean).join(' '));
                  return optionText && (optionText.includes(wanted) || wanted.includes(optionText) || optionText === wanted);
                });
                if (!radio) continue;
                radio.click();
                answerCount += 1;
              }
              const checkboxes = Array.from(document.querySelectorAll('input[type="checkbox"]'));
              for (const el of checkboxes) {
                if (el.disabled || el.checked) continue;
                const label = labelFor(el);
                const value = answerValue(label);
                const n = norm(value);
                if (!value || !/(yes|true|agree|available|willing|authorized|i agree)/i.test(n)) continue;
                el.click();
                answerCount += 1;
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
    def _fill_custom_choice_controls(page, user: User, answer_lookup: dict[str, str]) -> dict:
        counts = {"profile_fields_filled": 0, "answers_filled": 0}
        for _ in range(3):
            try:
                target = page.evaluate(
                    """
                    ({profile, answers}) => {
                      function textFor(el) {
                        return [
                          el.getAttribute('aria-label'),
                          el.getAttribute('title'),
                          el.getAttribute('name'),
                          el.getAttribute('placeholder'),
                          el.innerText,
                          el.textContent,
                        ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
                      }
                      function labelFor(el) {
                        const id = el.getAttribute('id');
                        const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                        const wrapper = el.closest('label, fieldset, .form-group, .field, .input, .application-question, .fb-dash-form-element, .jobs-easy-apply-form-section__grouping');
                        return [
                          el.getAttribute('aria-label'),
                          el.getAttribute('placeholder'),
                          el.getAttribute('name'),
                          label && label.innerText,
                          wrapper && wrapper.innerText,
                          textFor(el),
                        ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
                      }
                      function visible(el) {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                          && style.display !== 'none'
                          && rect.width > 0
                          && rect.height > 0
                          && !el.closest('[aria-hidden="true"]');
                      }
                      function norm(value) {
                        return (value || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
                      }
                      function tokens(value) {
                        return norm(value).split(' ').filter((token) => token.length > 2);
                      }
                      function tokenMatch(a, b) {
                        const left = new Set(tokens(a));
                        const right = new Set(tokens(b));
                        if (!left.size || !right.size) return false;
                        let overlap = 0;
                        for (const token of left) if (right.has(token)) overlap += 1;
                        const smaller = Math.min(left.size, right.size);
                        return overlap >= 2 && overlap / smaller >= 0.55;
                      }
                      function profileValue(label) {
                        const n = norm(label);
                        if (n.includes('country code') || n.includes('phone code')) {
                          if ((profile.location || '').toLowerCase().includes('india') || String(profile.phone || '').startsWith('+91')) return 'India (+91)';
                        }
                        if (n.includes('email')) return profile.email || '';
                        if (n.includes('location') || n.includes('city')) return profile.location || '';
                        return '';
                      }
                      function answerValue(label) {
                        const n = norm(label);
                        for (const [key, value] of Object.entries(answers)) {
                          const k = norm(key);
                          if (k && (n.includes(k) || k.includes(n) || tokenMatch(n, k))) return value;
                        }
                        return '';
                      }
                      const controls = Array.from(document.querySelectorAll(
                        '[role="combobox"], button[aria-haspopup="listbox"], button[aria-expanded], .artdeco-dropdown__trigger, input[aria-autocomplete="list"]'
                      )).filter(visible);
                      for (const el of controls) {
                        const label = labelFor(el);
                        const value = profileValue(label) || answerValue(label);
                        if (!value) continue;
                        const current = norm(textFor(el));
                        const wanted = norm(value);
                        if (current && (current.includes(wanted) || wanted.includes(current)) && !/(select|choose)/i.test(current)) continue;
                        document.querySelectorAll('[data-seekapply-choice-target="1"]').forEach((item) => item.removeAttribute('data-seekapply-choice-target'));
                        el.setAttribute('data-seekapply-choice-target', '1');
                        return {
                          found: true,
                          value,
                          source: profileValue(label) ? 'profile' : 'answer',
                          label: label.slice(0, 240),
                        };
                      }
                      return {found: false};
                    }
                    """,
                    {
                        "profile": {
                            "email": user.email,
                            "phone": user.phone,
                            "location": user.location,
                        },
                        "answers": answer_lookup,
                    },
                )
                if not isinstance(target, dict) or not target.get("found"):
                    break
                page.locator('[data-seekapply-choice-target="1"]').first.click(timeout=4000)
                page.wait_for_timeout(500)
                option = page.evaluate(
                    """
                    ({value}) => {
                      function textFor(el) {
                        return [
                          el.getAttribute('aria-label'),
                          el.getAttribute('title'),
                          el.innerText,
                          el.textContent,
                        ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
                      }
                      function visible(el) {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                          && style.display !== 'none'
                          && rect.width > 0
                          && rect.height > 0
                          && !el.closest('[aria-hidden="true"]');
                      }
                      function norm(item) {
                        return (item || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
                      }
                      const wanted = norm(value);
                      const options = Array.from(document.querySelectorAll('[role="option"], .artdeco-dropdown__item, li, button, [role="menuitem"]'))
                        .filter(visible);
                      const target = options.find((el) => {
                        const text = norm(textFor(el));
                        return text && (text === wanted || text.includes(wanted) || wanted.includes(text));
                      });
                      if (!target) return {found: false};
                      document.querySelectorAll('[data-seekapply-choice-option="1"]').forEach((item) => item.removeAttribute('data-seekapply-choice-option'));
                      target.setAttribute('data-seekapply-choice-option', '1');
                      return {found: true, text: textFor(target).slice(0, 160)};
                    }
                    """,
                    {"value": target.get("value") or ""},
                )
                if not isinstance(option, dict) or not option.get("found"):
                    break
                page.locator('[data-seekapply-choice-option="1"]').first.click(timeout=4000)
                if target.get("source") == "profile":
                    counts["profile_fields_filled"] += 1
                else:
                    counts["answers_filled"] += 1
                page.wait_for_timeout(300)
            except Exception:
                break
        return counts

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
              function norm(value) {
                return (value || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
              }
              function visible(el) {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
              }
              const controls = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="file"]), textarea, select, [contenteditable="true"]'));
              for (const el of controls) {
                if (!visible(el)) continue;
                const required = el.required || el.getAttribute('aria-required') === 'true';
                const type = (el.getAttribute('type') || '').toLowerCase();
                const currentValue = el.isContentEditable ? (el.innerText || '') : (el.value || '');
                const selected = el.tagName === 'SELECT' ? el.options[el.selectedIndex] : null;
                const selectedText = selected ? norm(selected.innerText || selected.label || selected.value) : '';
                const empty = type === 'radio' || type === 'checkbox'
                  ? !document.querySelector(`input[name="${CSS.escape(el.name || '')}"]:checked`)
                  : el.tagName === 'SELECT'
                    ? !(currentValue || '').trim() || el.selectedIndex <= 0 || /(select|choose|please select|--)/i.test(selectedText)
                    : !currentValue.trim();
                if (!required || !empty) continue;
                const label = labelFor(el).replace(/\\s+/g, ' ').trim();
                if (label && !out.includes(label)) out.push(label);
              }
              const customRequired = Array.from(document.querySelectorAll('[aria-required="true"], .fb-dash-form-element, .jobs-easy-apply-form-section__grouping'))
                .filter(visible);
              for (const wrapper of customRequired) {
                const text = (wrapper.innerText || '').replace(/\\s+/g, ' ').trim();
                if (!text || !/\\*/.test(text)) continue;
                const hasInput = wrapper.querySelector('input, textarea, select, [contenteditable="true"]');
                if (hasInput) continue;
                const hasChecked = wrapper.querySelector('[aria-checked="true"], input:checked');
                const hasSelectedText = /(yes|no|india|\\+91|selected|uploaded)/i.test(text);
                if (!hasChecked && !hasSelectedText && !out.includes(text)) out.push(text);
              }
              return out.slice(0, 12);
            }
            """
        )
        approved_keys = set(answer_lookup.keys())
        missing = []
        for question in questions:
            normalized = normalize(question)
            question_tokens = {token for token in normalized.split() if len(token) > 2}
            def matches(key: str) -> bool:
                if not key:
                    return False
                if normalized in key or key in normalized:
                    return True
                key_tokens = {token for token in key.split() if len(token) > 2}
                if not question_tokens or not key_tokens:
                    return False
                overlap = question_tokens & key_tokens
                return len(overlap) >= 2 and len(overlap) / min(len(question_tokens), len(key_tokens)) >= 0.55
            if not any(matches(key) for key in approved_keys):
                missing.append(question[:500])
        return missing

    @staticmethod
    def _final_submit_visible(page) -> bool:
        return bool(
            page.evaluate(
                """
                () => Array.from(document.querySelectorAll('button, input[type="submit"]')).some((el) =>
                  /(submit application|submit|send application|finish application)/i.test(
                    el.innerText || el.value || el.getAttribute('aria-label') || ''
                  )
                )
                """
            )
        )

    @staticmethod
    def _click_marked_target(page, selector: str, *, context=None, timeout: int = 5000) -> dict:
        clicked_page = page
        before_pages = set(context.pages) if context else set()
        try:
            if context:
                try:
                    with context.expect_page(timeout=4000) as page_info:
                        page.locator(selector).first.click(timeout=timeout)
                    clicked_page = page_info.value
                    clicked_page.wait_for_load_state("domcontentloaded", timeout=45_000)
                except Exception:
                    page.locator(selector).first.click(timeout=timeout)
                    page.wait_for_timeout(1200)
                    new_pages = [item for item in context.pages if item not in before_pages]
                    if new_pages:
                        clicked_page = new_pages[-1]
                        try:
                            clicked_page.wait_for_load_state("domcontentloaded", timeout=45_000)
                        except Exception:
                            pass
            else:
                page.locator(selector).first.click(timeout=timeout)
                page.wait_for_timeout(1200)
            return {"clicked": True, "page": clicked_page}
        except Exception as exc:
            return {"clicked": False, "page": page, "error": str(exc)}

    @staticmethod
    def _click_next_step(page, context=None) -> dict:
        try:
            result = page.evaluate(
                """
                () => {
                  function textFor(el) {
                    return [el.getAttribute('aria-label'), el.getAttribute('title'), el.value, el.innerText, el.textContent]
                      .filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
                  }
                  function visible(el) {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                      && style.display !== 'none'
                      && rect.width > 0
                      && rect.height > 0
                      && !el.closest('[aria-hidden="true"]');
                  }
                  function enabled(el) {
                    return !(el.disabled || el.getAttribute('disabled') !== null || el.getAttribute('aria-disabled') === 'true');
                  }
                  const modal = document.querySelector('.jobs-easy-apply-modal, .artdeco-modal, [role="dialog"]');
                  const root = modal || document;
                  const buttons = Array.from(root.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"]'))
                    .filter((el) => visible(el) && enabled(el));
                  const button = buttons.find((el) => {
                    const text = textFor(el).toLowerCase();
                    return /^(next|review|continue|save and continue|next step|continue to next step|review your application)$/i.test(text)
                      && !/(submit|send|finish|withdraw|delete|discard)/i.test(text);
                  }) || buttons.find((el) => {
                    const text = textFor(el).toLowerCase();
                    return /(next|review|continue|save and continue)/i.test(text)
                      && !/(submit|send|finish|withdraw|delete|discard|cancel|close)/i.test(text);
                  });
                  const buttonsSeen = buttons.map(textFor).filter(Boolean).slice(0, 30);
                  if (!button) return {clicked: false, reason: 'no_next_or_continue_button', buttons_seen: buttonsSeen};
                  document.querySelectorAll('[data-seekapply-next-target="1"]').forEach((el) => el.removeAttribute('data-seekapply-next-target'));
                  button.setAttribute('data-seekapply-next-target', '1');
                  button.scrollIntoView({ block: 'center', inline: 'center' });
                  return {clicked: true, text: textFor(button).slice(0, 160), buttons_seen: buttonsSeen};
                }
                """
            )
        except Exception as exc:
            return {"clicked": False, "reason": str(exc), "page": page}
        if isinstance(result, bool):
            return {"clicked": result, "page": page}
        if not isinstance(result, dict) or not result.get("clicked"):
            return {**(result if isinstance(result, dict) else {}), "clicked": False, "page": page}
        click_result = SupervisedLinkedInApplyAgent._click_marked_target(
            page, '[data-seekapply-next-target="1"]', context=context, timeout=6000
        )
        return {**result, **click_result, "clicked": bool(click_result.get("clicked"))}

    @staticmethod
    def _click_start_application(page, context=None) -> dict:
        try:
            result = page.evaluate(
                """
                () => {
                  function textFor(el) {
                    return [el.getAttribute('aria-label'), el.getAttribute('title'), el.value, el.innerText, el.textContent]
                      .filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
                  }
                  function visible(el) {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                  }
                  function enabled(el) {
                    return !(el.disabled || el.getAttribute('disabled') !== null || el.getAttribute('aria-disabled') === 'true');
                  }
                  const candidates = Array.from(document.querySelectorAll('a[href], button, [role="button"], input[type="button"], input[type="submit"]'))
                    .filter((el) => visible(el) && enabled(el));
                  const target = candidates.find((el) => {
                    const text = textFor(el);
                    return /(^apply$|apply\\s+now|apply\\s+to\\s+job|apply\\s+for\\s+this\\s+job|start\\s+application|begin\\s+application|continue\\s+application|complete\\s+application)/i.test(text)
                      && !/(submit|send|finish|delete|withdraw|cancel)/i.test(text);
                  });
                  const buttonsSeen = candidates.map(textFor).filter(Boolean).slice(0, 30);
                  if (!target) return {clicked: false, reason: 'no_apply_or_start_button', buttons_seen: buttonsSeen};
                  document.querySelectorAll('[data-seekapply-start-target="1"]').forEach((el) => el.removeAttribute('data-seekapply-start-target'));
                  target.setAttribute('data-seekapply-start-target', '1');
                  target.scrollIntoView({ block: 'center', inline: 'center' });
                  return {clicked: true, text: textFor(target).slice(0, 160), buttons_seen: buttonsSeen};
                }
                """
            )
        except Exception as exc:
            return {"clicked": False, "reason": str(exc), "page": page}
        if isinstance(result, bool):
            return {"clicked": result, "page": page}
        if not isinstance(result, dict) or not result.get("clicked"):
            return {**(result if isinstance(result, dict) else {}), "clicked": False, "page": page}
        click_result = SupervisedLinkedInApplyAgent._click_marked_target(
            page, '[data-seekapply-start-target="1"]', context=context, timeout=6000
        )
        return {**result, **click_result, "clicked": bool(click_result.get("clicked"))}
