import re
import logging
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from app.services.text import extract_keywords

logger = logging.getLogger(__name__)

@dataclass
class DiscoveredJob:
    title: str
    company: str
    location: str | None
    description: str
    job_url: str
    apply_url: str | None
    source: str
    skills: list[str]


class JobSearchAgent:
    allowed_sources = {"manual", "company_career_page", "greenhouse", "lever", "ashby", "smartrecruiters", "linkedin_automated_search"}

    def search_linkedin(self, query: str, location: str = "India", limit: int = 3) -> list[DiscoveredJob]:
        """Automated LinkedIn search using Playwright."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("Playwright not installed. Automated search disabled.")
            return []
            
        jobs: list[DiscoveredJob] = []
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
                
                # LinkedIn guest search URL
                search_url = f"https://www.linkedin.com/jobs/search/?keywords={query}&location={location}&f_TPR=r86400"
                page.goto(search_url, wait_until="networkidle", timeout=30000)
                
                # Wait for job cards to appear
                page.wait_for_selector(".base-card", timeout=15000)
                
                cards = page.query_selector_all(".base-card")[:limit]
                for card in cards:
                    try:
                        title_el = card.query_selector(".base-search-card__title")
                        company_el = card.query_selector(".base-search-card__subtitle")
                        location_el = card.query_selector(".job-search-card__location")
                        link_el = card.query_selector("a.base-card__full-link")
                        
                        if not title_el or not link_el:
                            continue
                            
                        title = title_el.inner_text().strip()
                        company = company_el.inner_text().strip() if company_el else "Unknown"
                        loc = location_el.inner_text().strip() if location_el else location
                        url = link_el.get_attribute("href").split("?")[0]
                        
                        jobs.append(DiscoveredJob(
                            title=title,
                            company=company,
                            location=loc,
                            description=f"Real-time automated discovery from LinkedIn for {title} at {company}.",
                            job_url=url,
                            apply_url=url,
                            source="linkedin_automated_search",
                            skills=extract_keywords(title)
                        ))
                    except Exception as e:
                        logger.warning(f"Failed to parse job card: {e}")
                        continue
                browser.close()
            except Exception as e:
                logger.error(f"Playwright search failed: {e}")
        return jobs

    def search_public_career_page(self, url: str, query: str, company: str | None = None) -> list[DiscoveredJob]:
        response = requests.get(url, timeout=12, headers={"User-Agent": "SeekApplyLocalMVP/1.0"})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        text = soup.get_text("\n", strip=True)
        links = soup.find_all("a", href=True)
        query_terms = [term.lower() for term in re.findall(r"[a-zA-Z0-9+#.]+", query)]
        jobs: list[DiscoveredJob] = []
        for link in links:
            label = link.get_text(" ", strip=True)
            href = link["href"]
            if not label:
                continue
            if query_terms and not any(term in label.lower() for term in query_terms):
                continue
            absolute = requests.compat.urljoin(url, href)
            jobs.append(
                DiscoveredJob(
                    title=label[:180],
                    company=company or self._company_from_url(url),
                    location=None,
                    description=text[:4000],
                    job_url=absolute,
                    apply_url=absolute,
                    source=self._source_from_url(url),
                    skills=extract_keywords(text),
                )
            )
        return self._dedupe(jobs)

    @staticmethod
    def _source_from_url(url: str) -> str:
        lowered = url.lower()
        if "greenhouse.io" in lowered:
            return "greenhouse"
        if "lever.co" in lowered:
            return "lever"
        if "ashbyhq.com" in lowered:
            return "ashby"
        if "smartrecruiters.com" in lowered:
            return "smartrecruiters"
        return "company_career_page"

    @staticmethod
    def _company_from_url(url: str) -> str:
        host = re.sub(r"^https?://", "", url).split("/")[0]
        return host.replace("www.", "").split(".")[0].title()

    @staticmethod
    def _dedupe(jobs: list[DiscoveredJob]) -> list[DiscoveredJob]:
        seen: set[str] = set()
        unique: list[DiscoveredJob] = []
        for job in jobs:
            if job.job_url not in seen:
                unique.append(job)
                seen.add(job.job_url)
        return unique
