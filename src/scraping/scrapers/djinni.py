"""Djinni.co tech jobs scraper."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from src.scraping.exceptions import ScraperError
from src.scraping.models import ATSType, Job
from src.scraping.scrapers.base import BaseScraper, ScraperRegistry

if TYPE_CHECKING:
    from src.scraping.fetch import Fetcher

logger = logging.getLogger(__name__)

API_ROOT = "https://djinni.co/jobs/"
MAX_CONCURRENCY = 2
DETAIL_CONCURRENCY = 2


@ScraperRegistry.register(ATSType.DJINNI)
class DjinniScraper(BaseScraper):
    """Djinni.co tech jobs scraper.

    Single-source scraper: ``company_slug`` is ignored. Pass anything
    (``"any"``, ``""``) to fetch the entire board.
    """

    ats = ATSType.DJINNI
    default_headers: ClassVar[dict[str, str]] = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Priority": "u=0, i",
    }

    def get_description(self, job: Job) -> str | None:
        if job.description:
            return job.description
        copy = job.model_copy()

        async def run() -> str | None:
            async with self.make_fetcher() as fetch:
                sem = asyncio.Semaphore(1)
                await self._enrich_detail(fetch, sem, copy)
            return copy.description

        return self._run_sync(run())

    async def afetch(self) -> list[Job]:
        seen_ids: set[str] = set()
        jobs: list[Job] = []
        lock = asyncio.Lock()

        async def absorb(items: list[Job]) -> None:
            async with lock:
                for j in items:
                    if j.ats_id and j.ats_id not in seen_ids:
                        seen_ids.add(j.ats_id)
                        jobs.append(j)

        async with self.make_fetcher() as fetch:
            try:
                first_page_html = await fetch.get_text(f"{API_ROOT}?page=1")
            except ScraperError as exc:
                logger.error("Djinni scraper failed to fetch initial listing: %s", exc)
                return []

            first_page_jobs = self._parse_listing(first_page_html)
            if not first_page_jobs:
                return jobs

            await absorb(first_page_jobs)
            last_page = self._extract_last_page(first_page_html)

            if last_page > 1:
                offsets = list(range(2, last_page + 1))
                sem = asyncio.Semaphore(MAX_CONCURRENCY)

                async def fetch_page(page_num: int) -> None:
                    async with sem:
                        try:
                            html_chunk = await fetch.get_text(f"{API_ROOT}?page={page_num}")
                            parsed = self._parse_listing(html_chunk)
                            await absorb(parsed)
                        except Exception as e:
                            logger.warning("Djinni pagination failed at page %d: %s", page_num, e)

                await asyncio.gather(*(fetch_page(o) for o in offsets))

            if self.include_descriptions and jobs:
                detail_sem = asyncio.Semaphore(DETAIL_CONCURRENCY)
                await asyncio.gather(*(self._enrich_detail(fetch, detail_sem, job) for job in jobs))

        return jobs

    async def _enrich_detail(
        self,
        fetch: Fetcher,
        sem: asyncio.Semaphore,
        job: Job,
    ) -> None:
        async with sem:
            try:
                response = await fetch.request(
                    "GET", str(job.url), handled=frozenset(range(400, 600))
                )
            except ScraperError:
                return

        if response.status_code != 200:
            return

        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return

        soup = BeautifulSoup(response.text, "html.parser")
        main_node = soup.find("main") or soup.find("body") or soup

        for element in main_node(["script", "style", "nav", "header", "footer"]):
            element.decompose()

        if job.company == "Unknown":
            comp_link = main_node.find("a", href=re.compile(r"^/company/"))
            if comp_link:
                job.company = comp_link.get_text(strip=True)

        desc_node = main_node.find(
            "div", class_=re.compile(r"col-sm-[789]|job-post__description|mb-4")
        )
        if not desc_node:
            divs = main_node.find_all("div")
            desc_node = max(divs, key=lambda d: len(d.get_text(strip=True))) if divs else main_node

        if desc_node and not job.description:
            text = desc_node.get_text(separator="\n", strip=True)
            if text:
                job.description = text[:25_000]

    def _parse_listing(self, html_text: str) -> list[Job]:
        try:
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise ScraperError(
                "Djinni scraper requires beautifulsoup4. Install with "
                "`pip install ats-scrapers[scrapers]` or `pip install beautifulsoup4`."
            ) from exc

        soup = BeautifulSoup(html_text, "html.parser")
        jobs: list[Job] = []

        for a_tag in soup.find_all("a", href=re.compile(r"^/jobs/(\d+)-")):
            url = a_tag.get("href")
            m = re.search(r"^/jobs/(\d+)-", str(url))
            if not m:
                continue
            ats_id = m.group(1)

            title = a_tag.get_text(strip=True)
            if not title:
                continue

            container = a_tag.find_parent("li") or a_tag.find_parent("div")
            company = "Unknown"
            is_remote = None

            sal_text = title
            if container:
                comp_link = container.find("a", href=re.compile(r"^/company/"))
                if comp_link:
                    company = comp_link.get_text(strip=True)

                text_content = container.get_text(separator=" ", strip=True).lower()
                if "remote" in text_content or "віддалено" in text_content:
                    is_remote = True

                sal_node = container.find(string=re.compile(r"\$|€|₴"))
                if sal_node:
                    sal_text += f" {sal_node}"

            salary_min, salary_max, salary_currency = _parse_salary(sal_text)

            jobs.append(
                Job(
                    url=f"https://djinni.co{url}",
                    title=title,
                    company=company,
                    ats_type=self.ats,
                    ats_id=ats_id,
                    is_remote=is_remote,
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency=salary_currency,
                    salary_period="MONTH" if salary_currency else None,
                    fetched_at=datetime.now(UTC),
                )
            )

        unique_jobs = {j.ats_id: j for j in jobs}
        return list(unique_jobs.values())

    def _extract_last_page(self, html_text: str) -> int:
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html_text, "html.parser")
            pagination = soup.find("ul", class_="pagination")
            if not pagination:
                return 1
            page_links = pagination.find_all("a", class_="page-link")
            pages = [
                int(a.get_text(strip=True)) for a in page_links if a.get_text(strip=True).isdigit()
            ]
            return max(pages) if pages else 1
        except Exception:
            return 1


def _parse_salary(raw: str | None) -> tuple[float | None, float | None, str | None]:
    if not raw:
        return None, None, None

    currency = "USD" if "$" in raw else ("EUR" if "€" in raw else ("UAH" if "₴" in raw else None))
    if not currency:
        return None, None, None

    cleaned = re.sub(r"\s+", "", raw)
    nums = [float(n) for n in re.findall(r"\d{3,}", cleaned)]

    if not nums:
        return None, None, currency

    if len(nums) == 1:
        return nums[0], nums[0], currency

    return nums[0], nums[1], currency
