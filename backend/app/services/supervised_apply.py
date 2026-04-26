from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.models.entities import ApplicationAnswer, Job, User
from app.services.browser_config import resolve_apply_browser
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

        if self.__class__._active and not self.__class__._active_is_live():
            self.__class__._close_active_on_worker()

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
            browser_config = resolve_apply_browser()
            resumed_active_browser = bool(self.__class__._active and self.__class__._active.get("task_id") == task_id)
            if not self.__class__._active:
                playwright = sync_playwright().start()
                launch_options = browser_config.kwargs()
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
                    **launch_options,
                )
                page = context.pages[0] if context.pages else context.new_page()
                self.__class__._active = {
                    "task_id": task_id,
                    "playwright": playwright,
                    "context": context,
                    "page": page,
                    "state_path": self.state_path,
                    "browser": browser_config.display_name,
                }
                steps.append(f"Launched {browser_config.display_name} with SeekApply's saved apply profile.")
            else:
                context = self.__class__._active["context"]
                page = self.__class__._active["page"]
                steps.append(f"Reusing active {self.__class__._active.get('browser') or 'browser'} session.")

            start_url = (job.job_url or job.apply_url) if is_linkedin else (job.apply_url or job.job_url)
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
                "resume_selected": False,
                "resume_actions": [],
                "profile_fields_filled": 0,
                "answers_filled": 0,
                "easy_apply_steps_completed": 0,
                "questions_seen": [],
                "kb_matched_questions": [],
                "unanswered_questions": [],
                "final_submit_detected": False,
                "session_profile": str(self.profile_dir),
                "browser": self.__class__._active.get("browser") if self.__class__._active else browser_config.display_name,
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
                easy_apply = self._click_easy_apply(page, wait_seconds=min(wait_seconds, 30), context=context)
                easy_apply_page = easy_apply.pop("_page", None) if isinstance(easy_apply, dict) else None
                if easy_apply_page:
                    page = easy_apply_page
                    self.__class__._active["page"] = page
                fill_report["easy_apply_detection"] = easy_apply
            else:
                easy_apply = {"clicked": False, "reason": "not_linkedin_job"}

            if is_linkedin and easy_apply.get("clicked"):
                clicked_text = easy_apply.get("clicked_text") or "LinkedIn apply button"
                steps.append(f"Opened the LinkedIn apply flow from: {clicked_text}.")
                page.wait_for_timeout(2200)
                if not self._application_form_present(page) and not self._final_submit_visible(page):
                    continued_page = self._click_external_continue(page, steps, context=context)
                    if continued_page:
                        page = continued_page
                        self.__class__._active["page"] = page
                    if "linkedin.com" not in (page.url or "").lower():
                        fill_report["mode"] = "external_from_linkedin"
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
                    fill_report.update(self._page_snapshot(page))
                    fill_report["manual_review_reason"] = (
                        "SeekApply clicked the visible LinkedIn Apply button, but no Easy Apply form appeared. "
                        "LinkedIn may have opened a custom prompt, blocked the modal, or changed the button behavior."
                    )
                    self._save_state(context)
                    return SupervisedApplyResult(
                        status="needs_user_action",
                        message="LinkedIn Apply was clicked, but the application form did not open.",
                        steps=steps,
                        fill_report=fill_report,
                        errors=[fill_report["manual_review_reason"]],
                        action_required=(
                            "In the visible browser, click the real Easy Apply/Application button manually. "
                            "When the form is open, click Resume Agent in SeekApply."
                        ),
                    )
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
            active = self.__class__._active
            if active and active.get("task_id") == task_id:
                self._save_state(active["context"])
            return SupervisedApplyResult(
                status="needs_user_action",
                message="Supervised apply timed out, but the visible browser was kept open.",
                steps=steps,
                errors=[str(exc)],
                action_required="Continue in the visible browser, then click Resume Agent. The browser is intentionally left open for inspection.",
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
            active = self.__class__._active
            if active and active.get("task_id") == task_id:
                self._save_state(active["context"])
            return SupervisedApplyResult(
                status="needs_user_action",
                message="Supervised apply hit a browser automation error, but the visible browser was kept open.",
                steps=steps,
                errors=[message],
                action_required="Inspect the visible browser, handle any prompt manually, then click Resume Agent.",
            )
        except Exception as exc:
            logger.exception("Supervised apply automation failed")
            active = self.__class__._active
            if active and active.get("task_id") == task_id:
                self._save_state(active["context"])
            return SupervisedApplyResult(
                status="needs_user_action",
                message="Supervised apply hit an automation error, but the visible browser was kept open.",
                steps=steps,
                errors=[str(exc)],
                action_required="Inspect the visible browser, handle any prompt manually, then click Resume Agent.",
            )

    @classmethod
    def close(cls, task_id: int | None = None) -> None:
        cls._executor.submit(cls._close_on_worker, task_id).result()

    @classmethod
    def active_summary(cls) -> dict:
        return cls._executor.submit(cls._active_summary_on_worker).result()

    @classmethod
    def _active_summary_on_worker(cls) -> dict:
        active = cls._active
        if not active:
            return {"live": False}
        if not cls._active_is_live():
            cls._close_active_on_worker()
            return {"live": False, "released": True, "reason": "stale_browser"}

        page = active.get("page")
        summary = {
            "live": True,
            "task_id": active.get("task_id"),
            "browser": active.get("browser"),
            "url": getattr(page, "url", "") if page else "",
            "submission_success": False,
            "final_submit_visible": False,
            "application_form_present": False,
        }
        if not page:
            return summary

        try:
            title = page.title()
            if title:
                summary["title"] = title
        except Exception:
            pass
        try:
            summary["submission_success"] = cls._submission_success_visible(page)
        except Exception:
            summary["submission_success"] = False
        try:
            summary["final_submit_visible"] = cls._final_submit_visible(page)
        except Exception:
            summary["final_submit_visible"] = False
        try:
            summary["application_form_present"] = cls._application_form_present(page)
        except Exception:
            summary["application_form_present"] = False
        return summary

    @classmethod
    def _close_on_worker(cls, task_id: int | None = None) -> None:
        if task_id is not None and cls._active and cls._active.get("task_id") != task_id:
            return
        cls._close_active_on_worker()

    @classmethod
    def _close_active_on_worker(cls) -> None:
        active = cls._active
        if not active:
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

    @classmethod
    def _active_is_live(cls) -> bool:
        active = cls._active
        if not active:
            return False
        page = active.get("page")
        context = active.get("context")
        try:
            if page is not None and hasattr(page, "is_closed") and page.is_closed():
                return False
            pages = getattr(context, "pages", None)
            if pages is not None:
                live_pages = [item for item in pages if not (hasattr(item, "is_closed") and item.is_closed())]
                if not live_pages:
                    return False
                if page not in live_pages:
                    active["page"] = live_pages[-1]
        except Exception:
            return False
        return True

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

    @staticmethod
    def _debug_value(value, *, depth: int = 0):
        if depth > 4:
            return str(value)[:500]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, list):
            return [SupervisedLinkedInApplyAgent._debug_value(item, depth=depth + 1) for item in value[:30]]
        if isinstance(value, tuple):
            return [SupervisedLinkedInApplyAgent._debug_value(item, depth=depth + 1) for item in value[:30]]
        if isinstance(value, dict):
            clean = {}
            for key, item in list(value.items())[:60]:
                if key in {"page", "_page", "context", "playwright"}:
                    continue
                clean[str(key)] = SupervisedLinkedInApplyAgent._debug_value(item, depth=depth + 1)
            return clean
        return str(value)[:500]

    @staticmethod
    def _record_automation_debug(fill_report: dict, event: str, data: dict | None = None) -> None:
        entries = fill_report.setdefault("automation_debug", [])
        entry = {
            "event": event,
            "data": SupervisedLinkedInApplyAgent._debug_value(data or {}),
        }
        entries.append(entry)
        if len(entries) > 80:
            del entries[:-80]
        logger.info("Apply automation debug: %s %s", event, entry["data"])

    @staticmethod
    def _automation_dom_snapshot(page) -> dict:
        try:
            snapshot = page.evaluate(
                """
                () => {
                  function textFor(el) {
                    const id = el.getAttribute('id');
                    const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                    const wrapper = el.closest('label, fieldset, .jobs-easy-apply-form-section__grouping, .fb-dash-form-element, .jobs-document-upload, .jobs-resume-picker, .form-group, .field, footer, .artdeco-modal__actionbar');
                    return [
                      el.getAttribute('aria-label'),
                      el.getAttribute('title'),
                      el.getAttribute('data-control-name'),
                      el.getAttribute('placeholder'),
                      el.getAttribute('name'),
                      el.getAttribute('accept'),
                      el.value,
                      label && label.innerText,
                      el.innerText,
                      wrapper && wrapper.innerText,
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
                    return !(el.disabled
                      || el.getAttribute('disabled') !== null
                      || el.getAttribute('aria-disabled') === 'true'
                      || /disabled/i.test(String(el.className || '')));
                  }
                  function buttonPayload(el) {
                    const rect = el.getBoundingClientRect();
                    const text = textFor(el).slice(0, 180);
                    return {
                      text,
                      enabled: enabled(el),
                      tag: el.tagName.toLowerCase(),
                      type: (el.getAttribute('type') || '').toLowerCase(),
                      role: el.getAttribute('role') || '',
                      aria_disabled: el.getAttribute('aria-disabled') || '',
                      data_control_name: el.getAttribute('data-control-name') || '',
                      top: Math.round(rect.top),
                      left: Math.round(rect.left),
                    };
                  }
                  function inputPayload(el) {
                    const rect = el.getBoundingClientRect();
                    const type = (el.getAttribute('type') || '').toLowerCase();
                    return {
                      label: textFor(el).slice(0, 180),
                      type,
                      tag: el.tagName.toLowerCase(),
                      required: Boolean(el.required || el.getAttribute('aria-required') === 'true'),
                      disabled: !enabled(el),
                      value_present: type === 'file' ? false : Boolean((el.value || '').trim()),
                      accept: el.getAttribute('accept') || '',
                      top: Math.round(rect.top),
                    };
                  }
                  const modal = document.querySelector('.jobs-easy-apply-modal, .artdeco-modal, [role="dialog"]');
                  const root = modal || document;
                  const buttons = Array.from(root.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"], a[href], label'))
                    .filter(visible)
                    .map(buttonPayload)
                    .filter((item) => item.text)
                    .slice(0, 50);
                  const next_candidates = buttons.filter((item) => /(next|continue|review|save and continue)/i.test(item.text)
                    && !/(submit|send|finish|cancel|close|discard|withdraw)/i.test(item.text));
                  const submit_candidates = buttons.filter((item) => /(submit application|submit|send application|finish application)/i.test(item.text));
                  const resume_buttons = buttons.filter((item) => /(resume|cv|upload|attach|choose|select)/i.test(item.text)
                    && !/(delete|remove|preview|download)/i.test(item.text));
                  const inputs = Array.from(root.querySelectorAll('input:not([type="hidden"]), textarea, select, [contenteditable="true"], [role="combobox"]'))
                    .filter(visible)
                    .map(inputPayload)
                    .slice(0, 40);
                  const file_inputs = Array.from(root.querySelectorAll('input[type="file"]'))
                    .filter(visible)
                    .map(inputPayload)
                    .slice(0, 10);
                  return {
                    title: document.title || '',
                    url: location.href,
                    modal_present: Boolean(modal),
                    modal_text_head: modal && modal.innerText ? modal.innerText.replace(/\\s+/g, ' ').slice(0, 700) : '',
                    buttons,
                    next_candidates,
                    submit_candidates,
                    resume_buttons,
                    inputs,
                    file_inputs,
                    visible_file_inputs: file_inputs.length,
                    form_count: root.querySelectorAll('form').length,
                  };
                }
                """
            )
            return snapshot if isinstance(snapshot, dict) else {}
        except Exception as exc:
            return {"error": str(exc)}

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
        fill_report.setdefault("automation_debug", [])
        for iteration in range(10):
            fill_report["current_url"] = page.url
            fill_report.update(self._page_snapshot(page))
            self._record_automation_debug(
                fill_report,
                "form_iteration_start",
                {
                    "iteration": iteration + 1,
                    "url": page.url,
                    "page_snapshot": self._page_snapshot(page),
                    "dom_snapshot": self._automation_dom_snapshot(page),
                },
            )
            if self._needs_user_login(page):
                logged_in = self._wait_for_login(page, page.url or "", 90, steps)
                if not logged_in:
                    self._record_automation_debug(
                        fill_report,
                        "login_required",
                        {
                            "iteration": iteration + 1,
                            "url": page.url,
                            "dom_snapshot": self._automation_dom_snapshot(page),
                        },
                    )
                    self._save_state(context)
                    return SupervisedApplyResult(
                        status="needs_login",
                        message="The application site needs login, verification, or CAPTCHA before SeekApply can continue.",
                        steps=steps,
                        fill_report=fill_report,
                        action_required="Complete login or verification in the visible browser, then click Resume in SeekApply.",
                    )
            resume_state = self._ensure_resume_selected(page, resume_path)
            self._record_automation_debug(
                fill_report,
                "resume_check",
                {
                    "iteration": iteration + 1,
                    "resume_state": resume_state,
                    "dom_snapshot": self._automation_dom_snapshot(page),
                },
            )
            fill_report["resume_uploaded"] = fill_report["resume_uploaded"] or bool(resume_state.get("uploaded"))
            fill_report["resume_selected"] = fill_report["resume_selected"] or bool(resume_state.get("selected"))
            resume_message = resume_state.get("message")
            if resume_message and resume_message not in fill_report["resume_actions"]:
                fill_report["resume_actions"].append(resume_message)
                steps.append(resume_message)
            filled = self._fill_visible_fields(page, user, answer_lookup)
            custom_choice_filled = self._fill_custom_choice_controls(page, user, answer_lookup)
            fill_report["profile_fields_filled"] += filled["profile_fields_filled"]
            fill_report["answers_filled"] += filled["answers_filled"] + custom_choice_filled["answers_filled"]
            fill_report["profile_fields_filled"] += custom_choice_filled["profile_fields_filled"]
            self._record_automation_debug(
                fill_report,
                "field_fill",
                {
                    "iteration": iteration + 1,
                    "profile_fields_filled_now": filled["profile_fields_filled"] + custom_choice_filled["profile_fields_filled"],
                    "answers_filled_now": filled["answers_filled"] + custom_choice_filled["answers_filled"],
                    "profile_fields_filled_total": fill_report["profile_fields_filled"],
                    "answers_filled_total": fill_report["answers_filled"],
                    "dom_snapshot": self._automation_dom_snapshot(page),
                },
            )
            previous_matched = set(fill_report.get("kb_matched_questions") or [])
            question_inventory = self._question_inventory(page, user, answer_lookup)
            for key in ["questions_seen", "kb_matched_questions", "unanswered_questions"]:
                merged = list(dict.fromkeys([*(fill_report.get(key) or []), *question_inventory.get(key, [])]))
                fill_report[key] = merged[:40]
            new_matched = [item for item in question_inventory.get("kb_matched_questions", []) if item not in previous_matched]
            if new_matched:
                steps.append(
                    f"Matched {len(new_matched)} visible question(s) from profile/KB."
                )
            missing_questions = self._missing_questions(page, answer_lookup, user=user)
            if missing_questions:
                self._record_automation_debug(
                    fill_report,
                    "missing_questions",
                    {
                        "iteration": iteration + 1,
                        "missing_questions": missing_questions,
                        "question_inventory": question_inventory,
                        "dom_snapshot": self._automation_dom_snapshot(page),
                    },
                )
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
                self._record_automation_debug(
                    fill_report,
                    "final_submit_detected",
                    {
                        "iteration": iteration + 1,
                        "dom_snapshot": self._automation_dom_snapshot(page),
                    },
                )
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
            self._record_automation_debug(
                fill_report,
                "next_click_attempt",
                {
                    "iteration": iteration + 1,
                    "next_result": next_result,
                    "dom_snapshot": self._automation_dom_snapshot(next_result.get("page") or page),
                },
            )

            if not next_result.get("clicked"):
                fill_report["last_click_blocker"] = next_result.get("reason") or "no_next_or_continue_button"

                if next_result.get("buttons_seen"):
                    fill_report["buttons_seen"] = next_result["buttons_seen"]

                if next_result.get("disabled_buttons_seen"):
                    fill_report["disabled_buttons_seen"] = next_result["disabled_buttons_seen"]

                if next_result.get("error"):
                    fill_report["last_click_error"] = next_result["error"]

                self._record_automation_debug(
                    fill_report,
                    "next_click_blocked",
                    {
                        "iteration": iteration + 1,
                        "reason": fill_report["last_click_blocker"],
                        "buttons_seen": next_result.get("buttons_seen") or [],
                        "disabled_buttons_seen": next_result.get("disabled_buttons_seen") or [],
                        "error": next_result.get("error"),
                    },
                )
                break
            page = next_result.get("page") or page
            if self.__class__._active:
                self.__class__._active["page"] = page
            fill_report["easy_apply_steps_completed"] += 1
            self._record_automation_debug(
                fill_report,
                "next_clicked",
                {
                    "iteration": iteration + 1,
                    "button_text": next_result.get("text"),
                    "easy_apply_steps_completed": fill_report["easy_apply_steps_completed"],
                    "url_after_click": page.url,
                    "dom_snapshot": self._automation_dom_snapshot(page),
                },
            )
            steps.append(
                f"{next_step_label}"
                f"{' Button: ' + next_result.get('text') if next_result.get('text') else ''}"
            )
            page.wait_for_timeout(1500)

        if self._final_submit_visible(page):
            fill_report["final_submit_detected"] = True
            self._record_automation_debug(
                fill_report,
                "final_submit_detected_after_loop",
                {"dom_snapshot": self._automation_dom_snapshot(page)},
            )
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
            self._record_automation_debug(
                fill_report,
                "manual_review_required",
                {
                    "reason": unsupported_error,
                    "message": message,
                    "dom_snapshot": self._automation_dom_snapshot(page),
                },
            )
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
            self._record_automation_debug(
                fill_report,
                "unsupported_form_state",
                {
                    "reason": unsupported_error,
                    "total_filled": total_filled,
                    "resume_uploaded": resume_uploaded,
                    "dom_snapshot": self._automation_dom_snapshot(page),
                },
            )
            return SupervisedApplyResult(
                status="needs_user_action",
                message=unsupported_message,
                steps=steps,
                fill_report=fill_report,
                errors=[unsupported_error],
                action_required="Continue in the visible browser or click Resume after handling the unsupported field.",
            )
        self._record_automation_debug(
            fill_report,
            "automation_failed",
            {
                "reason": unsupported_error,
                "dom_snapshot": self._automation_dom_snapshot(page),
            },
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
        direct_apply_url_first = SupervisedLinkedInApplyAgent._should_open_apply_url_directly(
            apply_url,
            current_url=current_url,
            job_url=getattr(job, "job_url", None),
        )
        if direct_apply_url_first and "linkedin." not in current_url.lower():
            try:
                page.goto(apply_url, wait_until="domcontentloaded", timeout=45_000)
                steps.append(f"Opened external apply URL: {apply_url}.")
                return page
            except Exception:
                steps.append("External apply URL could not be opened directly; trying the visible Apply button.")
        elif apply_url and not direct_apply_url_first and SupervisedLinkedInApplyAgent._is_linkedin_job_url(apply_url):
            steps.append("Captured apply_url is still a LinkedIn job page, so using the visible Apply button instead.")

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
                  if (target.tagName === 'A' && target.href && !/^javascript:|^#/i.test(target.href)) {
                    target.setAttribute('target', '_blank');
                    target.setAttribute('rel', 'noopener noreferrer');
                  }
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

            before_url = page.url or ""
            href = str(candidate.get("href") or "")
            click_result = SupervisedLinkedInApplyAgent._click_marked_target(
                page,
                '[data-seekapply-external-target="1"]',
                context=context,
                timeout=8000,
            )
            if click_result.get("clicked"):
                clicked_page = click_result.get("page") or page
                steps.append(f"Clicked external Apply button: {candidate.get('text') or 'Apply'}.")
                continued_page = SupervisedLinkedInApplyAgent._click_external_continue(clicked_page, steps, context=context)
                clicked_page = continued_page or clicked_page
                current_after_click = clicked_page.url or ""
                form_present = False
                final_submit_visible = False
                try:
                    form_present = SupervisedLinkedInApplyAgent._application_form_present(clicked_page)
                except Exception:
                    form_present = False
                try:
                    final_submit_visible = SupervisedLinkedInApplyAgent._final_submit_visible(clicked_page)
                except Exception:
                    final_submit_visible = False
                if (
                    current_after_click
                    and current_after_click != before_url
                ) or form_present or final_submit_visible:
                    return clicked_page

            if direct_apply_url_first:
                try:
                    page.goto(apply_url, wait_until="domcontentloaded", timeout=45_000)
                    steps.append(f"Opened captured external apply URL directly: {apply_url}.")
                    continued_page = SupervisedLinkedInApplyAgent._click_external_continue(page, steps, context=context)
                    return continued_page or page
                except Exception as exc:
                    steps.append(f"Captured external apply URL navigation failed: {exc}")

            if href and not href.lower().startswith(("javascript:", "#")) and not SupervisedLinkedInApplyAgent._is_linkedin_job_url(href):
                try:
                    page.goto(href, wait_until="domcontentloaded", timeout=45_000)
                    steps.append(f"Opened external apply link directly: {href}.")
                    continued_page = SupervisedLinkedInApplyAgent._click_external_continue(page, steps, context=context)
                    return continued_page or page
                except Exception as exc:
                    steps.append(f"External apply link navigation failed: {exc}")

            if click_result.get("clicked"):
                return click_result.get("page") or page
            if click_result.get("error"):
                steps.append(f"External Apply button click failed: {click_result.get('error')}")
            return None
        except Exception as exc:
            steps.append(f"External Apply detection failed: {exc}")
            return None

    @staticmethod
    def _is_linkedin_job_url(url: str | None) -> bool:
        value = str(url or "").lower()
        return "linkedin." in value and "/jobs/view/" in value

    @staticmethod
    def _linkedin_job_id_from_url(url: str | None) -> str | None:
        match = re.search(r"/jobs/view/(\d+)", str(url or ""))
        return match.group(1) if match else None

    @staticmethod
    def _same_linkedin_job_url(left: str | None, right: str | None) -> bool:
        if not (SupervisedLinkedInApplyAgent._is_linkedin_job_url(left) and SupervisedLinkedInApplyAgent._is_linkedin_job_url(right)):
            return False
        left_id = SupervisedLinkedInApplyAgent._linkedin_job_id_from_url(left)
        right_id = SupervisedLinkedInApplyAgent._linkedin_job_id_from_url(right)
        return bool(left_id and right_id and left_id == right_id)

    @staticmethod
    def _should_open_apply_url_directly(apply_url: str | None, *, current_url: str | None, job_url: str | None) -> bool:
        if not apply_url:
            return False
        apply_clean = str(apply_url).strip()
        current_clean = str(current_url or "").strip()
        job_clean = str(job_url or "").strip()
        if apply_clean == current_clean:
            return False
        if SupervisedLinkedInApplyAgent._is_linkedin_job_url(apply_clean):
            if (
                SupervisedLinkedInApplyAgent._is_linkedin_job_url(current_clean)
                or SupervisedLinkedInApplyAgent._is_linkedin_job_url(job_clean)
                or SupervisedLinkedInApplyAgent._same_linkedin_job_url(apply_clean, current_clean)
                or SupervisedLinkedInApplyAgent._same_linkedin_job_url(apply_clean, job_clean)
            ):
                return False
        return True

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
            before_pages = list(context.pages) if context else []
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
                    new_pages = [
                        item
                        for item in context.pages
                        if not any(item is existing for existing in before_pages)
                    ]
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

    # @staticmethod
    # def _click_easy_apply(page, wait_seconds: int = 15, context=None) -> dict:
    #     deadline = time.monotonic() + max(3, min(wait_seconds, 30))
    #     last_result: dict = {"clicked": False, "reason": "linkedin_apply_not_found", "visible_apply_buttons": []}
    #     while time.monotonic() < deadline:
    #         before_pages = list(context.pages) if context else []
    #         result = page.evaluate(
    #             """
    #             () => {
    #               // easy apply detector: keep this literal for lightweight tests and trace readability.
    #               // LinkedIn sometimes labels Easy Apply as a blue "in Apply" button with no "Easy" text.
    #               const PRIMARY_TOP_LIMIT = Math.max(760, Math.min(window.innerHeight + 80, 980));
    #               function textFor(el) {
    #                 const direct = [
    #                   el.getAttribute('aria-label'),
    #                   el.getAttribute('title'),
    #                   el.getAttribute('data-control-name'),
    #                   el.getAttribute('value'),
    #                   el.innerText,
    #                   el.textContent,
    #                 ].filter(Boolean).join(' ');
    #                 return direct.replace(/\\s+/g, ' ').trim();
    #               }
    #               function compactText(el) {
    #                 return textFor(el).slice(0, 180);
    #               }
    #               function norm(value) {
    #                 return (value || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
    #               }
    #               function visible(el) {
    #                 const style = window.getComputedStyle(el);
    #                 const rect = el.getBoundingClientRect();
    #                 return style.visibility !== 'hidden'
    #                   && style.display !== 'none'
    #                   && rect.width > 0
    #                   && rect.height > 0
    #                   && rect.bottom >= -10
    #                   && rect.top <= window.innerHeight + 120
    #                   && !el.closest('[aria-hidden="true"]');
    #               }
    #               function enabled(el) {
    #                 return !(el.disabled
    #                   || el.getAttribute('disabled') !== null
    #                   || el.getAttribute('aria-disabled') === 'true'
    #                   || /disabled/i.test(el.className || ''));
    #               }
    #               function isPrimaryJobArea(el) {
    #                 const rect = el.getBoundingClientRect();
    #                 const topCard = el.closest(
    #                   '.jobs-unified-top-card, .job-details-jobs-unified-top-card, .jobs-details-top-card, .top-card-layout'
    #                 );
    #                 if (topCard) return true;
    #                 return rect.top >= 0
    #                   && rect.top <= PRIMARY_TOP_LIMIT
    #                   && rect.left < window.innerWidth * 0.72
    #                   && !el.closest('aside, .jobs-details__right-rail, .job-card-container, .scaffold-layout__list');
    #               }
    #               function isLinkedInApplyButton(el) {
    #                 const text = textFor(el);
    #                 const n = norm(text);
    #                 if (/company\\s+website|external\\s+apply|apply\\s+on\\s+company/i.test(text)) return false;
    #                 const className = String(el.className || '');
    #                 const control = String(el.getAttribute('data-control-name') || '');
    #                 const looksLinkedInApply =
    #                   /easy\\s*apply/i.test(text)
    #                   || /apply\\s+to\\s+(this\\s+job|.+\\s+at\\s+)/i.test(text)
    #                   || /linkedin\\s+apply|apply\\s+linkedin/i.test(text)
    #                   || /jobs-apply-button/i.test(className)
    #                   || /inapply|easyapply|jobdetails.*apply/i.test(control);
    #                 const primaryApplyText = n === 'apply' || n === 'in apply' || n.startsWith('apply to ');
    #                 return enabled(el) && isPrimaryJobArea(el) && (looksLinkedInApply || primaryApplyText);
    #               }
    #               const candidates = Array.from(document.querySelectorAll(
    #                 'button, a[href], [role="button"], div[role="button"], span[role="button"], input[type="button"], input[type="submit"]'
    #               )).filter(visible);
    #               const visibleApplyButtons = candidates
    #                 .map(compactText)
    #                 .filter((text) => /(easy\\s*apply|\\bapply\\b|continue application|company website)/i.test(text))
    #                 .filter((text) => text.length <= 180);
    #               const easyApplyButtons = candidates.filter((el) => /easy\\s*apply/i.test(textFor(el)) && enabled(el));
    #               const linkedinApplyButtons = candidates.filter(isLinkedInApplyButton);
    #               const target = easyApplyButtons[0] || linkedinApplyButtons[0];
    #               if (!target) {
    #                 return {
    #                   clicked: false,
    #                   reason: candidates.some((el) => /easy\\s*apply/i.test(textFor(el))) ? 'linkedin_apply_disabled' : 'linkedin_apply_not_found',
    #                   visible_apply_buttons: Array.from(new Set(visibleApplyButtons)).slice(0, 10),
    #                 };
    #               }
    #               target.scrollIntoView({ block: 'center', inline: 'center' });
    #               target.click();
    #               return {
    #                 clicked: true,
    #                 reason: /easy\\s*apply/i.test(textFor(target)) ? 'clicked_easy_apply' : 'clicked_linkedin_apply_button',
    #                 clicked_text: compactText(target),
    #                 visible_apply_buttons: Array.from(new Set(visibleApplyButtons)).slice(0, 10),
    #               };
    #             }
    #             """
    #         )
    #         if isinstance(result, bool):
    #             return {"clicked": result, "reason": "legacy_detector", "visible_apply_buttons": []}
    #         if isinstance(result, dict):
    #             last_result = result
    #             if result.get("clicked"):
    #                 page.wait_for_timeout(1000)
    #                 if context:
    #                     new_pages = [
    #                         item
    #                         for item in context.pages
    #                         if not any(item is existing for existing in before_pages)
    #                     ]
    #                     if new_pages:
    #                         new_page = new_pages[-1]
    #                         try:
    #                             new_page.wait_for_load_state("domcontentloaded", timeout=45_000)
    #                         except Exception:
    #                             pass
    #                         result["_page"] = new_page
    #                         result["opened_new_page"] = True
    #                         result["new_page_url"] = getattr(new_page, "url", "")
    #                 return result
    #         page.wait_for_timeout(1000)
    #     return last_result
    @staticmethod
    def _click_easy_apply(page, wait_seconds: int = 15, context=None) -> dict:
        deadline = time.monotonic() + max(3, min(wait_seconds, 30))
        last_result: dict = {
            "clicked": False,
            "reason": "linkedin_apply_not_found",
            "visible_apply_buttons": [],
        }

        while time.monotonic() < deadline:
            before_pages = list(context.pages) if context else []

            try:
                result = page.evaluate(
                    """
                    () => {
                    // easy apply detector: keep this literal for lightweight tests and trace readability.
                    // clicked_linkedin_apply_button
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
                        return String(value || '')
                        .toLowerCase()
                        .replace(/[^a-z0-9]+/g, ' ')
                        .trim();
                    }

                    function visible(el) {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();

                        return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0
                        && rect.height > 0
                        && rect.bottom >= -10
                        && rect.top <= window.innerHeight + 160
                        && !el.closest('[aria-hidden="true"]');
                    }

                    function enabled(el) {
                        return !(el.disabled
                        || el.getAttribute('disabled') !== null
                        || el.getAttribute('aria-disabled') === 'true'
                        || /disabled/i.test(String(el.className || '')));
                    }

                    function isPrimaryJobArea(el) {
                        const rect = el.getBoundingClientRect();

                        const topCard = el.closest(
                        [
                            '.jobs-unified-top-card',
                            '.job-details-jobs-unified-top-card',
                            '.jobs-details-top-card',
                            '.top-card-layout',
                            '.jobs-search__job-details--container',
                            '.jobs-details',
                        ].join(',')
                        );

                        if (topCard) return true;

                        return rect.top >= 0
                        && rect.top <= PRIMARY_TOP_LIMIT
                        && rect.left < window.innerWidth * 0.75
                        && !el.closest(
                            [
                            'aside',
                            '.jobs-details__right-rail',
                            '.job-card-container',
                            '.jobs-search-results-list',
                            '.scaffold-layout__list',
                            ].join(',')
                        );
                    }

                    function isExternalApply(el) {
                        const text = textFor(el);
                        const href = el.getAttribute('href') || '';

                        return /company\\s+website|external\\s+apply|apply\\s+on\\s+company|apply\\s+on\\s+.*website/i.test(text)
                        || /externalApply|offsite|company/i.test(href);
                    }

                    function isEasyApply(el) {
                        const text = textFor(el);
                        const n = norm(text);
                        const className = String(el.className || '');
                        const control = String(el.getAttribute('data-control-name') || '');

                        if (!visible(el) || !enabled(el)) return false;
                        if (!isPrimaryJobArea(el)) return false;
                        if (isExternalApply(el)) return false;

                        const explicitEasyApply =
                        /easy\\s*apply/i.test(text)
                        || /jobs-apply-button/i.test(className)
                        || /easyapply|easy_apply|inapply/i.test(control);

                        const linkedinApplyText =
                        /apply\\s+to\\s+(this\\s+job|.+\\s+at\\s+)/i.test(text)
                        || /linkedin\\s+apply|apply\\s+linkedin/i.test(text);

                        const shortPrimaryApply =
                        n === 'apply'
                        || n === 'in apply'
                        || n === 'continue application';

                        return explicitEasyApply || linkedinApplyText || shortPrimaryApply;
                    }

                    const candidates = Array.from(document.querySelectorAll(
                        [
                        'button',
                        'a[href]',
                        '[role="button"]',
                        'div[role="button"]',
                        'span[role="button"]',
                        'input[type="button"]',
                        'input[type="submit"]'
                        ].join(',')
                    )).filter(visible);

                    const visibleApplyButtons = candidates
                        .map(compactText)
                        .filter((text) => /(easy\\s*apply|\\bapply\\b|continue application|company website)/i.test(text))
                        .filter((text) => text.length <= 180);

                    const targets = candidates.filter(isEasyApply);

                    const target =
                        targets.find((el) => /easy\\s*apply/i.test(textFor(el)))
                        || targets[0];

                    document
                        .querySelectorAll('[data-seekapply-easy-apply-target="1"]')
                        .forEach((el) => el.removeAttribute('data-seekapply-easy-apply-target'));

                    if (!target) {
                        return {
                        clicked: false,
                        reason: candidates.some((el) => /easy\\s*apply/i.test(textFor(el)))
                            ? 'linkedin_apply_disabled'
                            : 'linkedin_apply_not_found',
                        visible_apply_buttons: Array.from(new Set(visibleApplyButtons)).slice(0, 10),
                        };
                    }

                    target.setAttribute('data-seekapply-easy-apply-target', '1');
                    target.scrollIntoView({ block: 'center', inline: 'center' });

                    return {
                        clicked: true,
                        reason: /easy\\s*apply/i.test(textFor(target))
                        ? 'found_easy_apply'
                        : 'found_linkedin_apply_button',
                        clicked_text: compactText(target),
                        visible_apply_buttons: Array.from(new Set(visibleApplyButtons)).slice(0, 10),
                    };
                    }
                    """
                )
            except Exception as exc:
                last_result = {
                    "clicked": False,
                    "reason": f"easy_apply_detection_error: {exc}",
                    "visible_apply_buttons": [],
                }
                page.wait_for_timeout(1000)
                continue

            if isinstance(result, bool):
                return {
                    "clicked": result,
                    "reason": "legacy_detector",
                    "visible_apply_buttons": [],
                }

            if not isinstance(result, dict):
                last_result = {
                    "clicked": False,
                    "reason": "invalid_easy_apply_detector_result",
                    "visible_apply_buttons": [],
                }
                page.wait_for_timeout(1000)
                continue

            last_result = result

            if not result.get("clicked"):
                page.wait_for_timeout(1000)
                continue

            clicked_result = {
                **result,
                "clicked": True,
                "reason": result.get("reason", "clicked_easy_apply").replace("found_", "clicked_"),
            }

            if context:
                new_pages = [
                    item
                    for item in context.pages
                    if not any(item is existing for existing in before_pages)
                ]

                if new_pages and not hasattr(page, "locator"):
                    new_page = new_pages[-1]
                    try:
                        new_page.wait_for_load_state("domcontentloaded", timeout=45_000)
                    except Exception:
                        pass

                    clicked_result["_page"] = new_page
                    clicked_result["opened_new_page"] = True
                    clicked_result["new_page_url"] = getattr(new_page, "url", "")

                    return clicked_result

            if not hasattr(page, "locator"):
                return clicked_result

            try:
                target = page.locator('[data-seekapply-easy-apply-target="1"]').first

                target.wait_for(state="visible", timeout=5000)
                target.scroll_into_view_if_needed(timeout=5000)

                # Real Playwright click. This is more reliable than element.click() inside page.evaluate().
                target.click(timeout=8000)

                page.wait_for_timeout(1500)

            except Exception as exc:
                last_result = {
                    **result,
                    "clicked": False,
                    "reason": f"easy_apply_click_failed: {exc}",
                    "visible_apply_buttons": result.get("visible_apply_buttons", []),
                }
                page.wait_for_timeout(1000)
                continue

            if context:
                new_pages = [
                    item
                    for item in context.pages
                    if not any(item is existing for existing in before_pages)
                ]

                if new_pages:
                    new_page = new_pages[-1]
                    try:
                        new_page.wait_for_load_state("domcontentloaded", timeout=45_000)
                    except Exception:
                        pass

                    clicked_result["_page"] = new_page
                    clicked_result["opened_new_page"] = True
                    clicked_result["new_page_url"] = getattr(new_page, "url", "")

                    return clicked_result

            # LinkedIn Easy Apply usually opens a modal on the same page.
            try:
                modal = page.locator(
                    '.jobs-easy-apply-modal, '
                    '.artdeco-modal, '
                    '[role="dialog"]'
                ).first

                modal.wait_for(state="visible", timeout=8000)
                clicked_result["modal_opened"] = True

            except Exception:
                clicked_result["modal_opened"] = False

            return clicked_result

        return last_result
    
    @staticmethod
    def _ensure_resume_selected(page, resume_path: Path) -> dict:
        uploaded = SupervisedLinkedInApplyAgent._upload_resume(page, resume_path)
        if uploaded:
            return {"uploaded": True, "selected": True, "message": f"Uploaded selected resume file: {resume_path.name}."}
        selected = SupervisedLinkedInApplyAgent._select_existing_resume(page)
        if selected.get("selected"):
            return {
                "uploaded": False,
                "selected": True,
                "message": f"Selected resume option: {selected.get('text') or 'existing resume'}.",
            }
        return {"uploaded": False, "selected": False, "message": None, **selected}

    @staticmethod
    def _select_existing_resume(page) -> dict:
        try:
            result = page.evaluate(
                """
                () => {
                  function textFor(el) {
                    const id = el.getAttribute('id');
                    const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                    const wrapper = el.closest('label, .jobs-document-upload, .jobs-resume-picker, .jobs-easy-apply-form-section__grouping, .fb-dash-form-element, fieldset, li, div');
                    return [
                      el.getAttribute('aria-label'),
                      el.getAttribute('title'),
                      el.getAttribute('name'),
                      el.value,
                      label && label.innerText,
                      wrapper && wrapper.innerText,
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
                  function looksResume(text) {
                    return /(resume|cv|curriculum|\\.pdf|\\.docx|document)/i.test(text)
                      && !/(cover\\s+letter|portfolio|delete|remove|trash|preview|download)/i.test(text);
                  }
                  const root = document.querySelector('.jobs-easy-apply-modal, .artdeco-modal, [role="dialog"]') || document;
                  const radios = Array.from(root.querySelectorAll('input[type="radio"], input[type="checkbox"]'))
                    .filter((el) => visible(el) && enabled(el) && looksResume(textFor(el)));
                  const checked = radios.find((el) => el.checked);
                  if (checked) return {selected: true, already_selected: true, text: textFor(checked).slice(0, 160)};
                  const target = radios[0];
                  if (target) {
                    target.scrollIntoView({block: 'center', inline: 'center'});
                    target.click();
                    return {selected: true, already_selected: false, text: textFor(target).slice(0, 160)};
                  }
                  const buttons = Array.from(root.querySelectorAll('button, [role="button"], label'))
                    .filter((el) => visible(el) && enabled(el) && looksResume(textFor(el)) && /(select|choose|use|resume|cv)/i.test(textFor(el)));
                  const button = buttons[0];
                  if (!button) return {selected: false, reason: 'no_resume_option'};
                  button.scrollIntoView({block: 'center', inline: 'center'});
                  button.click();
                  return {selected: true, already_selected: false, text: textFor(button).slice(0, 160)};
                }
                """
            )
            return result if isinstance(result, dict) else {"selected": bool(result)}
        except Exception as exc:
            return {"selected": False, "reason": str(exc)}

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
                    label = input_el.evaluate(
                        """
                        (el) => {
                          const id = el.getAttribute('id');
                          const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                          const wrapper = el.closest('label, .jobs-easy-apply-form-section__grouping, .fb-dash-form-element, fieldset, .form-group, .field, .input, .application-question, div');
                          return [
                            el.getAttribute('aria-label'),
                            el.getAttribute('title'),
                            el.getAttribute('name'),
                            el.getAttribute('accept'),
                            label && label.innerText,
                            wrapper && wrapper.innerText,
                          ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
                        }
                        """
                    )
                    lowered = normalize(str(label or ""))
                    if len(inputs) > 1 and not any(marker in lowered for marker in ["resume", "cv", "curriculum", "document"]):
                        continue
                    if any(marker in lowered for marker in ["cover letter", "portfolio", "transcript"]):
                        continue
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
        aliases = {
            "notice_period": [
                "when can you join",
                "available to start",
                "availability to start",
                "start date",
                "joining date",
                "how soon can you join",
            ],
            "expected_ctc": [
                "expected ctc",
                "expected salary",
                "expected compensation",
                "salary expectation",
                "desired salary",
            ],
            "current_ctc": [
                "current ctc",
                "current salary",
                "current compensation",
            ],
            "work_authorization": [
                "work authorization",
                "authorized to work",
                "legally authorized",
                "visa status",
                "require sponsorship",
                "need sponsorship",
            ],
            "relocation": [
                "willing to relocate",
                "relocation",
                "can you relocate",
            ],
            "preferred_locations": [
                "preferred location",
                "location preference",
                "work location",
                "remote preference",
            ],
            "linkedin_url": ["linkedin profile", "linkedin url"],
            "github_url": ["github profile", "github url"],
            "portfolio_url": ["portfolio", "personal website", "website"],
        }
        for answer in answers:
            if answer.approved and answer.answer_text.strip() and answer.answer_text != "[NEEDS HUMAN REVIEW]":
                key = normalize(answer.question_key)
                text = normalize(answer.question_text)
                lookup[key] = answer.answer_text
                lookup[text] = answer.answer_text
                combined = f"{key} {text}"
                for canonical_key, canonical_aliases in aliases.items():
                    if canonical_key in combined or any(normalize(alias) in combined for alias in canonical_aliases):
                        for alias in [canonical_key, *canonical_aliases]:
                            lookup[normalize(alias)] = answer.answer_text
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
              function optionLabelFor(el) {
                const id = el.getAttribute('id');
                const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                const parentLabel = el.closest('label');
                return [
                  el.getAttribute('aria-label'),
                  label && label.innerText,
                  parentLabel && parentLabel.innerText,
                  el.value,
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
                if (n.includes('notice')) return profile.notice_period || '';
                if (n.includes('authorization') || n.includes('visa') || n.includes('sponsor')) return profile.work_authorization || '';
                if (n.includes('year') && n.includes('experience')) return profile.experience_years !== null && profile.experience_years !== undefined ? String(profile.experience_years) : '';
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
                  const optionLabel = optionLabelFor(el);
                  const optionText = norm(optionLabel);
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
                    "notice_period": getattr(user, "notice_period", None),
                    "work_authorization": getattr(user, "work_authorization", None),
                    "experience_years": getattr(user, "experience_years", None),
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
    def _question_inventory(page, user: User, answer_lookup: dict[str, str]) -> dict:
        try:
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
                      el.getAttribute('name'),
                      label && label.innerText,
                      wrapper && wrapper.innerText,
                    ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
                  }
                  function visible(el) {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                  }
                  const controls = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="file"]), textarea, select, [contenteditable="true"], [role="combobox"]'));
                  const out = [];
                  for (const el of controls) {
                    if (!visible(el)) continue;
                    const label = labelFor(el);
                    if (label && !out.includes(label)) out.push(label.slice(0, 260));
                  }
                  return out.slice(0, 30);
                }
                """
            )
        except Exception:
            questions = []
        seen = []
        matched = []
        unanswered = []
        for question in questions or []:
            if not question or question in seen:
                continue
            seen.append(question)
            if SupervisedLinkedInApplyAgent._answer_for_label(question, answer_lookup) or SupervisedLinkedInApplyAgent._profile_value_for_label(question, user):
                matched.append(question)
            else:
                unanswered.append(question)
        return {"questions_seen": seen, "kb_matched_questions": matched, "unanswered_questions": unanswered}

    @staticmethod
    def _answer_for_label(label: str, answer_lookup: dict[str, str]) -> str:
        normalized = normalize(label)
        label_tokens = {token for token in normalized.split() if len(token) > 2}
        for key, value in answer_lookup.items():
            if not key or not value:
                continue
            if normalized in key or key in normalized:
                return value
            key_tokens = {token for token in key.split() if len(token) > 2}
            if label_tokens and key_tokens:
                overlap = label_tokens & key_tokens
                if len(overlap) >= 2 and len(overlap) / min(len(label_tokens), len(key_tokens)) >= 0.55:
                    return value
        return ""

    @staticmethod
    def _profile_value_for_label(label: str, user: User) -> str:
        n = normalize(label)
        if "email" in n:
            return getattr(user, "email", None) or ""
        if "phone" in n or "mobile" in n:
            return getattr(user, "phone", None) or ""
        if "first name" in n:
            return (getattr(user, "name", "") or "").split(" ")[0]
        if "last name" in n:
            return " ".join((getattr(user, "name", "") or "").split(" ")[1:])
        if n == "name" or "full name" in n:
            return getattr(user, "name", None) or ""
        if "linkedin" in n:
            return getattr(user, "linkedin_url", None) or ""
        if "github" in n:
            return getattr(user, "github_url", None) or ""
        if "portfolio" in n or "website" in n:
            return getattr(user, "portfolio_url", None) or getattr(user, "github_url", None) or ""
        if "notice" in n:
            return getattr(user, "notice_period", None) or ""
        if ("join" in n or "start" in n or "available" in n) and any(marker in n for marker in ["when", "date", "soon", "available"]):
            return getattr(user, "notice_period", None) or ""
        if "authorization" in n or "visa" in n or "sponsor" in n:
            return getattr(user, "work_authorization", None) or ""
        if "year" in n and "experience" in n:
            years = getattr(user, "experience_years", None)
            return str(years) if years is not None else ""
        if "city" in n or "location" in n:
            return getattr(user, "location", None) or ""
        return ""

    @staticmethod
    def _missing_questions(page, answer_lookup: dict[str, str], user: User | None = None) -> list[str]:
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
            if not any(matches(key) for key in approved_keys) and not (
                user and SupervisedLinkedInApplyAgent._profile_value_for_label(question, user)
            ):
                missing.append(question[:500])
        return missing

    @staticmethod
    def _final_submit_visible(page) -> bool:
        return bool(
            page.evaluate(
                """
                () => {
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
                  return Array.from(document.querySelectorAll('button, input[type="submit"], [role="button"]')).some((el) =>
                    visible(el) && enabled(el) && /(submit application|submit|send application|finish application)/i.test(
                      el.innerText || el.value || el.getAttribute('aria-label') || ''
                    )
                  );
                }
                """
            )
        )

    @staticmethod
    def _submission_success_visible(page) -> bool:
        return bool(
            page.evaluate(
                """
                () => {
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
                  const hasFinalSubmit = Array.from(document.querySelectorAll('button, input[type="submit"], [role="button"]')).some((el) =>
                    visible(el) && enabled(el) && /(submit application|submit|send application|finish application)/i.test(
                      el.innerText || el.value || el.getAttribute('aria-label') || ''
                    )
                  );
                  if (hasFinalSubmit) return false;
                  const text = [
                    document.title || '',
                    document.body && document.body.innerText ? document.body.innerText : '',
                  ].join('\\n').replace(/\\s+/g, ' ').slice(0, 20000);
                  return /(?:application (?:submitted|sent|received)|your application (?:was sent|has been submitted|has been received)|you(?:'|\\u2019)ve applied|you have applied|successfully applied|thank you for applying|thanks for applying|application complete|we(?:'|\\u2019)ve received your application|we have received your application)/i.test(text);
                }
                """
            )
        )

    # @staticmethod
    # def _click_marked_target(page, selector: str, *, context=None, timeout: int = 5000) -> dict:
    #     clicked_page = page
    #     before_pages = list(context.pages) if context else []
    #     try:
    #         if context:
    #             try:
    #                 with context.expect_page(timeout=4000) as page_info:
    #                     page.locator(selector).first.click(timeout=timeout)
    #                 clicked_page = page_info.value
    #                 clicked_page.wait_for_load_state("domcontentloaded", timeout=45_000)
    #             except Exception:
    #                 page.locator(selector).first.click(timeout=timeout)
    #                 page.wait_for_timeout(1200)
    #                 new_pages = [
    #                     item
    #                     for item in context.pages
    #                     if not any(item is existing for existing in before_pages)
    #                 ]
    #                 if new_pages:
    #                     clicked_page = new_pages[-1]
    #                     try:
    #                         clicked_page.wait_for_load_state("domcontentloaded", timeout=45_000)
    #                     except Exception:
    #                         pass
    #         else:
    #             page.locator(selector).first.click(timeout=timeout)
    #             page.wait_for_timeout(1200)
    #         return {"clicked": True, "page": clicked_page}
    #     except Exception as exc:
    #         return {"clicked": False, "page": page, "error": str(exc)}

    @staticmethod
    def _click_marked_target(page, selector: str, *, context=None, timeout: int = 8000) -> dict:
        clicked_page = page
        before_pages = list(context.pages) if context else []

        try:
            target = page.locator(selector).first
            if hasattr(target, "wait_for"):
                target.wait_for(state="visible", timeout=timeout)
            if hasattr(target, "scroll_into_view_if_needed"):
                target.scroll_into_view_if_needed(timeout=timeout)

            # Click exactly once. Do not use expect_page here.
            try:
                target.click(timeout=timeout)
            except TypeError:
                target.click()

            if hasattr(page, "wait_for_timeout"):
                page.wait_for_timeout(1800)

            if context:
                new_pages = [
                    item
                    for item in context.pages
                    if not any(item is existing for existing in before_pages)
                ]

                if new_pages:
                    clicked_page = new_pages[-1]
                    try:
                        clicked_page.wait_for_load_state("domcontentloaded", timeout=45_000)
                    except Exception:
                        pass

                    return {
                        "clicked": True,
                        "page": clicked_page,
                        "opened_new_page": True,
                        "new_page_url": getattr(clicked_page, "url", ""),
                    }

            return {
                "clicked": True,
                "page": clicked_page,
            }

        except Exception as exc:
            return {
                "clicked": False,
                "page": page,
                "error": str(exc),
                "reason": f"marked_target_click_failed: {exc}",
            }

    # @staticmethod
    # def _click_next_step(page, context=None) -> dict:
    #     try:
    #         result = page.evaluate(
    #             """
    #             () => {
    #               function textFor(el) {
    #                 return [el.getAttribute('aria-label'), el.getAttribute('title'), el.value, el.innerText, el.textContent]
    #                   .filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
    #               }
    #               function visible(el) {
    #                 const style = window.getComputedStyle(el);
    #                 const rect = el.getBoundingClientRect();
    #                 return style.visibility !== 'hidden'
    #                   && style.display !== 'none'
    #                   && rect.width > 0
    #                   && rect.height > 0
    #                   && !el.closest('[aria-hidden="true"]');
    #               }
    #               function enabled(el) {
    #                 return !(el.disabled || el.getAttribute('disabled') !== null || el.getAttribute('aria-disabled') === 'true');
    #               }
    #               const modal = document.querySelector('.jobs-easy-apply-modal, .artdeco-modal, [role="dialog"]');
    #               const root = modal || document;
    #               const buttons = Array.from(root.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"]'))
    #                 .filter((el) => visible(el) && enabled(el));
    #               const button = buttons.find((el) => {
    #                 const text = textFor(el).toLowerCase();
    #                 return /^(next|review|continue|save and continue|next step|continue to next step|review your application)$/i.test(text)
    #                   && !/(submit|send|finish|withdraw|delete|discard)/i.test(text);
    #               }) || buttons.find((el) => {
    #                 const text = textFor(el).toLowerCase();
    #                 return /(next|review|continue|save and continue)/i.test(text)
    #                   && !/(submit|send|finish|withdraw|delete|discard|cancel|close)/i.test(text);
    #               });
    #               const buttonsSeen = buttons.map(textFor).filter(Boolean).slice(0, 30);
    #               if (!button) return {clicked: false, reason: 'no_next_or_continue_button', buttons_seen: buttonsSeen};
    #               document.querySelectorAll('[data-seekapply-next-target="1"]').forEach((el) => el.removeAttribute('data-seekapply-next-target'));
    #               button.setAttribute('data-seekapply-next-target', '1');
    #               button.scrollIntoView({ block: 'center', inline: 'center' });
    #               return {clicked: true, text: textFor(button).slice(0, 160), buttons_seen: buttonsSeen};
    #             }
    #             """
    #         )
    #     except Exception as exc:
    #         return {"clicked": False, "reason": str(exc), "page": page}
    #     if isinstance(result, bool):
    #         return {"clicked": result, "page": page}
    #     if not isinstance(result, dict) or not result.get("clicked"):
    #         return {**(result if isinstance(result, dict) else {}), "clicked": False, "page": page}
    #     click_result = SupervisedLinkedInApplyAgent._click_marked_target(
    #         page, '[data-seekapply-next-target="1"]', context=context, timeout=6000
    #     )
    #     return {**result, **click_result, "clicked": bool(click_result.get("clicked"))}


    @staticmethod
    def _click_next_step(page, context=None) -> dict:
        try:
            result = page.evaluate(
                """
                () => {
                function textFor(el) {
                    return [
                    el.getAttribute('aria-label'),
                    el.getAttribute('title'),
                    el.getAttribute('data-control-name'),
                    el.value,
                    el.innerText,
                    el.textContent
                    ]
                    .filter(Boolean)
                    .join(' ')
                    .replace(/\\s+/g, ' ')
                    .trim();
                }

                function norm(value) {
                    return String(value || '')
                    .toLowerCase()
                    .replace(/[^a-z0-9]+/g, ' ')
                    .trim();
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
                    return !(
                    el.disabled ||
                    el.getAttribute('disabled') !== null ||
                    el.getAttribute('aria-disabled') === 'true' ||
                    /disabled/i.test(String(el.className || ''))
                    );
                }

                function dangerous(text) {
                    return /\\b(submit|submit application|send application|send|finish|withdraw|delete|discard|cancel|close)\\b/i.test(text);
                }

                function nextLike(text) {
                    const n = norm(text);

                    return (
                    n === 'next' ||
                    n === 'review' ||
                    n === 'continue' ||
                    n === 'save and continue' ||
                    n === 'next step' ||
                    n === 'continue to next step' ||
                    n === 'review your application' ||
                    n.includes('next') ||
                    n.includes('review') ||
                    n.includes('continue')
                    );
                }

                const modal =
                    document.querySelector('.jobs-easy-apply-modal') ||
                    document.querySelector('.artdeco-modal') ||
                    document.querySelector('[role="dialog"]');

                const root = modal || document;

                const footer =
                    root.querySelector?.('.artdeco-modal__actionbar') ||
                    root.querySelector?.('.jobs-easy-apply-modal__footer') ||
                    root.querySelector?.('footer') ||
                    root;

                const selector = [
                    'button',
                    '[role="button"]',
                    'input[type="button"]',
                    'input[type="submit"]'
                ].join(',');

                const footerButtons = Array.from(footer.querySelectorAll(selector)).filter(visible);
                const rootButtons = Array.from(root.querySelectorAll(selector)).filter(visible);

                const buttons = [];
                const seen = new Set();

                for (const button of [...footerButtons, ...rootButtons]) {
                    if (seen.has(button)) continue;
                    seen.add(button);
                    buttons.push(button);
                }

                const buttonsSeen = buttons
                    .map(textFor)
                    .filter(Boolean)
                    .slice(0, 40);

                const disabledButtonsSeen = buttons
                    .filter((el) => !enabled(el))
                    .map(textFor)
                    .filter(Boolean)
                    .slice(0, 20);

                const candidates = buttons.filter((el) => {
                    const text = textFor(el);
                    if (!text) return false;
                    if (!enabled(el)) return false;
                    if (dangerous(text)) return false;
                    return nextLike(text);
                });

                const target =
                    candidates.find((el) => norm(textFor(el)) === 'next') ||
                    candidates.find((el) => norm(textFor(el)) === 'review') ||
                    candidates.find((el) => norm(textFor(el)) === 'continue') ||
                    candidates.find((el) => norm(textFor(el)) === 'save and continue') ||
                    candidates[0];

                document
                    .querySelectorAll('[data-seekapply-next-target="1"]')
                    .forEach((el) => el.removeAttribute('data-seekapply-next-target'));

                if (!target) {
                    return {
                    clicked: false,
                    reason: disabledButtonsSeen.length
                        ? 'next_button_disabled_or_required_fields_missing'
                        : 'no_next_or_continue_button',
                    buttons_seen: buttonsSeen,
                    disabled_buttons_seen: disabledButtonsSeen
                    };
                }

                target.setAttribute('data-seekapply-next-target', '1');
                target.scrollIntoView({ block: 'center', inline: 'center' });

                return {
                    clicked: true,
                    text: textFor(target).slice(0, 160),
                    buttons_seen: buttonsSeen,
                    disabled_buttons_seen: disabledButtonsSeen
                };
                }
                """
            )

        except Exception as exc:
            return {
                "clicked": False,
                "reason": f"next_button_detection_error: {exc}",
                "page": page,
            }

        if isinstance(result, bool):
            return {
                "clicked": result,
                "page": page,
            }

        if not isinstance(result, dict) or not result.get("clicked"):
            return {
                **(result if isinstance(result, dict) else {}),
                "clicked": False,
                "page": page,
            }

        click_result = SupervisedLinkedInApplyAgent._click_marked_target(
            page,
            '[data-seekapply-next-target="1"]',
            context=context,
            timeout=8000,
        )

        return {
            **result,
            **click_result,
            "clicked": bool(click_result.get("clicked")),
            "reason": "clicked_next_step" if click_result.get("clicked") else click_result.get("reason"),
        }

        
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
