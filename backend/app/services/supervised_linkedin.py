from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.services.linkedin_assist import LinkedInSearchPlan
from app.services.text import extract_keywords

logger = logging.getLogger(__name__)


@dataclass
class SupervisedLinkedInJob:
    title: str
    company: str
    location: str | None
    description: str
    job_url: str
    apply_url: str | None
    source_site: str = "linkedin.com"
    skills: list[str] = field(default_factory=list)


@dataclass
class SupervisedLinkedInResult:
    status: str
    message: str
    jobs: list[SupervisedLinkedInJob] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    action_required: str | None = None


class SupervisedLinkedInImporter:
    def __init__(self, storage_root: Path) -> None:
        self.storage_root = storage_root
        self.session_dir = storage_root / "browser_sessions" / "linkedin"

    def import_jobs(
        self,
        plans: list[LinkedInSearchPlan],
        *,
        max_jobs: int = 20,
        include_descriptions: bool = True,
        wait_seconds: int = 90,
    ) -> SupervisedLinkedInResult:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError:
            return SupervisedLinkedInResult(
                status="setup_required",
                message="Playwright is not installed in the backend environment.",
                action_required="Install Playwright and its Chromium browser, then try again.",
            )

        if not plans:
            return SupervisedLinkedInResult(status="no_searches", message="No LinkedIn search plans were generated.")

        self.session_dir.mkdir(parents=True, exist_ok=True)
        steps = ["Opening a supervised browser window."]
        errors: list[str] = []
        jobs: list[SupervisedLinkedInJob] = []
        seen_urls: set[str] = set()

        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.session_dir),
                    headless=False,
                    viewport={"width": 1360, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
                for plan in plans:
                    if len(jobs) >= max_jobs:
                        break
                    steps.append(f"Searching LinkedIn for {plan.keyword} in {plan.location}.")
                    try:
                        page.goto(plan.url, wait_until="domcontentloaded", timeout=45_000)
                        self._wait_for_results_or_user_action(page, wait_seconds)
                        self._scroll_results(page)
                        found = self._extract_result_cards(page)
                        steps.append(f"Found {len(found)} visible card(s) for {plan.keyword}.")
                        for item in found:
                            if len(jobs) >= max_jobs:
                                break
                            url = item.get("job_url") or ""
                            if not url or url in seen_urls:
                                continue
                            seen_urls.add(url)
                            detail = {}
                            if include_descriptions:
                                detail = self._read_job_detail(context, url)
                            merged = {**item, **{key: value for key, value in detail.items() if value}}
                            jobs.append(self._job_from_payload(merged))
                    except PlaywrightTimeoutError as exc:
                        errors.append(f"Timed out reading {plan.keyword}: {exc}")
                    except PlaywrightError as exc:
                        errors.append(f"Could not read {plan.keyword}: {exc}")
                    except Exception as exc:
                        logger.exception("Supervised LinkedIn import failed for %s", plan.url)
                        errors.append(f"Could not read {plan.keyword}: {exc}")
                context.close()
        except Exception as exc:
            logger.exception("Could not start supervised LinkedIn browser")
            return SupervisedLinkedInResult(
                status="browser_error",
                message=f"Could not start or control the supervised browser: {exc}",
                steps=steps,
                errors=[*errors, str(exc)],
                action_required="Make sure Playwright Chromium is installed and no stale browser session is locked.",
            )

        if jobs:
            return SupervisedLinkedInResult(
                status="completed_with_warnings" if errors else "completed",
                message=f"Imported visible data for {len(jobs)} LinkedIn job(s).",
                jobs=jobs,
                steps=steps,
                errors=errors,
            )

        return SupervisedLinkedInResult(
            status="needs_user_action" if not errors else "failed",
            message=(
                "No visible LinkedIn jobs were extracted. If LinkedIn showed login, CAPTCHA, or an empty page, "
                "complete that in the browser and run the importer again."
            ),
            steps=steps,
            errors=errors,
            action_required="Open LinkedIn in the launched browser, sign in or resolve prompts manually, then retry.",
        )

    @staticmethod
    def _wait_for_results_or_user_action(page, wait_seconds: int) -> None:
        selector = "li.jobs-search-results__list-item,.job-card-container,.base-card,[data-job-id],.jobs-search-results-list"
        page.wait_for_selector(selector, timeout=max(wait_seconds, 5) * 1000)

    @staticmethod
    def _scroll_results(page) -> None:
        for _ in range(4):
            page.evaluate(
                """
                () => {
                  const list = document.querySelector('.jobs-search-results-list, .scaffold-layout__list, main');
                  if (list) list.scrollTo(0, list.scrollHeight);
                  window.scrollTo(0, document.body.scrollHeight);
                }
                """
            )
            page.wait_for_timeout(900)

    @staticmethod
    def _extract_result_cards(page) -> list[dict]:
        return page.evaluate(
            """
            () => {
              function text(root, selector) {
                const el = root.querySelector(selector);
                return el && el.innerText ? el.innerText.trim() : "";
              }
              function cleanUrl(value) {
                try {
                  const url = new URL(value, window.location.href);
                  url.search = "";
                  return url.href;
                } catch {
                  return value || "";
                }
              }
              const cards = Array.from(document.querySelectorAll(
                'li.jobs-search-results__list-item,.job-card-container,.base-card,[data-job-id]'
              ));
              const seen = new Set();
              const jobs = [];
              for (const card of cards) {
                const anchor = card.querySelector('a[href*="/jobs/view/"],a.base-card__full-link,a[href*="/jobs/"]');
                const job_url = cleanUrl(anchor ? anchor.getAttribute("href") : "");
                if (!job_url || seen.has(job_url)) continue;
                seen.add(job_url);
                const lines = (card.innerText || "").split("\\n").map((line) => line.trim()).filter(Boolean);
                const title = text(card, '.job-card-list__title,.base-search-card__title,.artdeco-entity-lockup__title,a[href*="/jobs/view/"]') || lines[0] || "LinkedIn Imported Role";
                const company = text(card, '.job-card-container__primary-description,.base-search-card__subtitle,.artdeco-entity-lockup__subtitle') || lines[1] || "Unknown Company";
                const location = text(card, '.job-card-container__metadata-item,.job-search-card__location,.artdeco-entity-lockup__caption') || lines[2] || "";
                jobs.push({
                  title,
                  company,
                  location,
                  job_url,
                  apply_url: job_url,
                  description: (card.innerText || "").slice(0, 4000),
                  visible_text: (card.innerText || "").slice(0, 4000),
                });
              }
              return jobs;
            }
            """
        )

    @staticmethod
    def _read_job_detail(context, job_url: str) -> dict:
        detail = context.new_page()
        try:
            detail.goto(job_url, wait_until="domcontentloaded", timeout=45_000)
            detail.wait_for_timeout(1600)
            payload = detail.evaluate(
                """
                () => {
                  function text(selector) {
                    const el = document.querySelector(selector);
                    return el && el.innerText ? el.innerText.trim() : "";
                  }
                  function cleanUrl(value) {
                    try {
                      const url = new URL(value, window.location.href);
                      url.search = "";
                      return url.href;
                    } catch {
                      return value || "";
                    }
                  }
                  const applyAnchors = Array.from(document.querySelectorAll('a[href]'))
                    .filter((anchor) => /apply/i.test(anchor.innerText || anchor.getAttribute('aria-label') || ""));
                  const applyUrl = applyAnchors.length ? cleanUrl(applyAnchors[0].href) : window.location.href;
                  const description = text('.jobs-description-content__text,.description__text,.jobs-box__html-content,[data-test-job-description],main');
                  return {
                    title: text('.job-details-jobs-unified-top-card__job-title,.topcard__title,[data-test-job-title],h1'),
                    company: text('.job-details-jobs-unified-top-card__company-name,.topcard__org-name-link,[data-test-job-company-name]'),
                    location: text('.job-details-jobs-unified-top-card__primary-description-container,.topcard__flavor--bullet,[data-test-job-location]'),
                    description: description || document.body.innerText.slice(0, 8000),
                    apply_url: applyUrl,
                  };
                }
                """
            )
            return payload
        finally:
            detail.close()

    @staticmethod
    def _job_from_payload(payload: dict) -> SupervisedLinkedInJob:
        description = str(payload.get("description") or payload.get("visible_text") or "")
        job_url = str(payload.get("job_url") or payload.get("page_url") or payload.get("apply_url") or "")
        job_url = re.sub(r"\?.*$", "", job_url)
        return SupervisedLinkedInJob(
            title=str(payload.get("title") or "LinkedIn Imported Role")[:300],
            company=str(payload.get("company") or "Unknown Company")[:250],
            location=str(payload.get("location") or "").strip() or None,
            description=description[:12000],
            job_url=job_url,
            apply_url=str(payload.get("apply_url") or job_url),
            skills=extract_keywords(description, limit=16),
        )
