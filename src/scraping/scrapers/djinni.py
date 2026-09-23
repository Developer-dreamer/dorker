"""Djinni.co tech jobs scraper."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import ClassVar
from xml.etree import ElementTree as ET

from src.scraping.exceptions import ScraperError
from src.scraping.models import ATSType, Job
from src.scraping.scrapers.base import BaseScraper, ScraperRegistry

API_URL = "https://djinni.co/jobs/rss/"
_TAG_RE = re.compile(r"<[^>]+>")


@ScraperRegistry.register(ATSType.DJINNI)
class DjinniScraper(BaseScraper):
    """Djinni.co tech jobs scraper.

    Single-source scraper: ``company_slug`` is ignored.
    Uses the public RSS feed to bypass HTML scraping and Cloudflare limits.
    """

    ats = ATSType.DJINNI
    default_headers: ClassVar[dict[str, str]] = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/rss+xml, text/xml, application/xml",
    }

    async def afetch(self) -> list[Job]:
        async with self.make_fetcher() as fetch:
            xml_text = await fetch.get_text(API_URL)

        return self._parse_rss(xml_text)

    def _parse_rss(self, xml_text: str) -> list[Job]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise ScraperError(f"Djinni returned malformed RSS: {exc}") from exc

        if root.tag.lower() != "rss" and root.find(".//channel") is None:
            raise ScraperError("Djinni returned malformed RSS: root element is not <rss>")

        jobs: list[Job] = []
        seen: set[str] = set()

        for item in root.iter("item"):
            job = self._parse_item(item)
            if job is None or job.ats_id in seen:
                continue
            seen.add(job.ats_id)
            jobs.append(job)

        return jobs

    def _parse_item(self, item: ET.Element) -> Job | None:
        link = (item.findtext("link") or "").strip()
        if not link:
            return None

        # Extract ID from URL (e.g. https://djinni.co/jobs/12345-title/)
        ats_id = ""
        m = re.search(r"/jobs/(\d+)-", link)
        if m:
            ats_id = m.group(1)
        else:
            # Fallback to guid
            guid = (item.findtext("guid") or "").strip()
            ats_id = guid.split("/")[-1] if guid else ""

        if not ats_id:
            return None

        title = (item.findtext("title") or "").strip() or "Untitled"
        description_raw = item.findtext("description")
        description = self._strip_description(description_raw) if self.include_descriptions else None

        category = (item.findtext("category") or "").strip()

        # Djinni typically puts the salary directly in the title
        salary_min, salary_max, salary_currency = _parse_salary(title)

        return Job(
            url=link,
            title=title,
            company="Unknown",  # Djinni RSS omits company names to encourage candidate interaction
            ats_type=ATSType.DJINNI,
            ats_id=ats_id,
            department=category or None,
            is_remote=self._check_remote(title, description_raw),
            salary_currency=salary_currency,
            salary_period="MONTH" if salary_currency else None,
            salary_min=salary_min,
            salary_max=salary_max,
            description=description,
            posted_at=_parse_pubdate(item.findtext("pubDate")),
            fetched_at=datetime.now(UTC),
        )

    def _strip_description(self, raw: str | None) -> str | None:
        if not raw:
            return None
        text = html.unescape(raw)
        text = _TAG_RE.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return None
        return text[:25_000]

    def _check_remote(self, title: str, description: str | None) -> bool | None:
        text = f"{title} {description or ''}".lower()
        if "remote" in text or "віддалено" in text:
            return True
        return None


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


def _parse_pubdate(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
