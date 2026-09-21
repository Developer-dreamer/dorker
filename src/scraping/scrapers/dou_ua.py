"""DOU.ua tech jobs scraper."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from src.scraping.exceptions import ScraperError
from src.scraping.models import ATSType, Job
from src.scraping.scrapers.base import BaseScraper, ScraperRegistry

if TYPE_CHECKING:
    from src.scraping.fetch import Fetcher

logger = logging.getLogger(__name__)

API_ROOT = "https://jobs.dou.ua/vacancies/"
MAX_CONCURRENCY = 1
DETAIL_CONCURRENCY = 1

_UKR_MONTHS = {
    "січня": 1,
    "лютого": 2,
    "березня": 3,
    "квітня": 4,
    "травня": 5,
    "червня": 6,
    "липня": 7,
    "серпня": 8,
    "вересня": 9,
    "жовтня": 10,
    "листопада": 11,
    "грудня": 12,
}


@ScraperRegistry.register(ATSType.DOU)
class DOUScraper(BaseScraper):
    """DOU.ua tech jobs scraper.

    Single-source scraper: ``company_slug`` is ignored. Pass anything
    (``"any"``, ``""``) to fetch the entire board.
    """

    ats = ATSType.DOU
    default_headers: ClassVar[dict[str, str]] = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/json,application/xhtml+xml",
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
                html_text = await fetch.get_text(API_ROOT)
            except ScraperError as exc:
                logger.error("DOU scraper failed to fetch initial listing: %s", exc)
                return []

            csrf_match = re.search(
                r'name=["\']csrfmiddlewaretoken["\'][^>]*value=["\']([^"\']+)["\']', html_text
            )
            if not csrf_match:
                csrf_match = re.search(
                    r'value=["\']([^"\']+)["\'][^>]*name=["\']csrfmiddlewaretoken["\']', html_text
                )
            csrf_token = csrf_match.group(1) if csrf_match else ""

            first_page_jobs = self._parse_listing(html_text)
            await absorb(first_page_jobs)
            total = self._extract_total(html_text)

            if total and total > len(first_page_jobs):
                offsets = list(range(len(first_page_jobs), total, 40))
                sem = asyncio.Semaphore(MAX_CONCURRENCY)

                async def fetch_page(offset: int) -> None:
                    async with sem:
                        try:
                            resp = await self._post_xhr(
                                fetch,
                                f"{API_ROOT}xhr-load/",
                                data={"csrfmiddlewaretoken": csrf_token, "count": offset},
                                headers={"X-Requested-With": "XMLHttpRequest", "Referer": API_ROOT},
                            )
                            if resp.status_code == 200:
                                data = resp.json()
                                html_chunk = data.get("html", "")
                                if html_chunk:
                                    parsed = self._parse_listing(html_chunk)
                                    await absorb(parsed)
                        except Exception as e:
                            logger.warning("DOU pagination failed at offset %d: %s", offset, e)

                await asyncio.gather(*(fetch_page(o) for o in offsets))
            else:
                offset = len(first_page_jobs)
                while True:
                    try:
                        resp = await self._post_xhr(
                            fetch,
                            f"{API_ROOT}xhr-load/",
                            data={"csrfmiddlewaretoken": csrf_token, "count": offset},
                            headers={"X-Requested-With": "XMLHttpRequest", "Referer": API_ROOT},
                        )
                        if resp.status_code != 200:
                            break
                        data = resp.json()
                        html_chunk = data.get("html", "")
                        parsed = self._parse_listing(html_chunk)
                        if not parsed:
                            break
                        await absorb(parsed)
                        offset += len(parsed)
                        if data.get("last"):
                            break
                    except Exception as e:
                        logger.warning("DOU sequential pagination failed: %s", e)
                        break

            if self.include_descriptions and jobs:
                detail_sem = asyncio.Semaphore(DETAIL_CONCURRENCY)
                await asyncio.gather(*(self._enrich_detail(fetch, detail_sem, job) for job in jobs))

        return jobs

    async def _post_xhr(
        self, fetch: Fetcher, url: str, data: dict[str, Any], headers: dict[str, str]
    ) -> Any:
        """
        Handle URL-encoded form data separately since
        `Fetcher.request` only supports JSON payloads.
        """
        client = fetch._httpx_client()
        for attempt in range(1, 4):
            try:
                resp = await client.post(url, data=data, headers=headers)
                if resp.status_code in {200, 404, 403, 410}:
                    return resp
                if resp.status_code in {429, 500, 502, 503, 504}:
                    await asyncio.sleep(1.5 * (2 ** (attempt - 1)))
                    continue
            except Exception:
                if attempt == 3:
                    raise
                await asyncio.sleep(1.5 * (2 ** (attempt - 1)))
        return resp

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

        desc_node = soup.find("div", class_="b-typo vacancy-section")
        if desc_node and not job.description:
            text = desc_node.get_text(separator="\n", strip=True)
            if text:
                job.description = text[:25_000]

        date_node = soup.find("div", class_="date")
        if date_node and not job.posted_at:
            job.posted_at = _parse_ukr_date(date_node.get_text(strip=True))

    def _parse_listing(self, html_text: str) -> list[Job]:
        try:
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise ScraperError(
                "DOU scraper requires beautifulsoup4. Install with "
                "`pip install ats-scrapers[scrapers]` or `pip install beautifulsoup4`."
            ) from exc

        soup = BeautifulSoup(html_text, "html.parser")
        jobs: list[Job] = []

        for li in soup.find_all("li", class_="l-vacancy"):
            vt = li.find("a", class_="vt")
            if not vt:
                continue

            url = vt.get("href")
            if not isinstance(url, str):
                continue

            title = vt.get_text(strip=True)
            m = re.search(r"/vacancies/(\d+)/", url)
            ats_id = m.group(1) if m else None
            if not ats_id:
                continue

            company_node = li.find("a", class_="company")
            company = company_node.get_text(strip=True) if company_node else "Unknown"

            cities_node = li.find("span", class_="cities")
            location = cities_node.get_text(strip=True) if cities_node else None

            salary_node = li.find("span", class_="salary")
            salary_raw = salary_node.get_text(strip=True) if salary_node else None
            salary_min, salary_max, salary_currency = _parse_salary(salary_raw)

            is_remote = None
            if location:
                loc_lower = location.lower()
                if "віддалено" in loc_lower or "remote" in loc_lower:
                    is_remote = True

            jobs.append(
                Job(
                    url=url,
                    title=title,
                    company=company,
                    ats_type=self.ats,
                    ats_id=ats_id,
                    location=location,
                    is_remote=is_remote,
                    salary_currency=salary_currency,
                    salary_period="MONTH" if salary_currency else None,
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_summary=salary_raw,
                    fetched_at=datetime.now(UTC),
                )
            )

        return jobs

    def _extract_total(self, html_text: str) -> int | None:
        m = re.search(r"<h1[^>]*>([\d\s]+)\s*(?:ваканс|vacanc)", html_text, re.IGNORECASE)
        if m:
            clean_str = re.sub(r"\s+", "", m.group(1))
            try:
                return int(clean_str)
            except ValueError:
                return None
        return None


def _parse_salary(raw: str | None) -> tuple[float | None, float | None, str | None]:
    if not raw:
        return None, None, None

    raw_lower = raw.lower()
    currency = (
        "USD"
        if "$" in raw
        else ("EUR" if "€" in raw else ("UAH" if "₴" in raw or "грн" in raw_lower else None))
    )

    cleaned = re.sub(r"\s+", "", raw)
    nums = [float(n) for n in re.findall(r"\d+", cleaned)]

    if not nums:
        return None, None, currency

    if len(nums) == 1:
        if "до" in raw_lower or "up to" in raw_lower:
            return None, nums[0], currency
        if "від" in raw_lower or "from" in raw_lower:
            return nums[0], None, currency
        return nums[0], nums[0], currency

    return nums[0], nums[1], currency


def _parse_ukr_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None

    clean_str = date_str.lower().strip()

    if "сьогодні" in clean_str:
        return datetime.now(UTC)
    if "вчора" in clean_str:
        return datetime.now(UTC) - timedelta(days=1)

    parts = clean_str.split()
    if len(parts) >= 2:
        day_str = parts[0]
        month_str = parts[1]
        year_str = parts[2] if len(parts) >= 3 else str(datetime.now(UTC).year)

        if day_str.isdigit() and year_str.isdigit() and month_str in _UKR_MONTHS:
            try:
                return datetime(
                    year=int(year_str), month=_UKR_MONTHS[month_str], day=int(day_str), tzinfo=UTC
                )
            except ValueError:
                return None

    return None
