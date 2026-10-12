"""Djinni public listing scraper; shared crawler lives outside upstream sync scope."""

from __future__ import annotations

import httpx

from src.scraping.djinni_snapshot import USER_AGENT, Snapshot, collect
from src.scraping.exceptions import ScraperError
from src.scraping.models import ATSType, Job
from src.scraping.scrapers.base import BaseScraper, ScraperRegistry


@ScraperRegistry.register(ATSType.DJINNI)
class DjinniScraper(BaseScraper):
    """Traverse /jobs/ once, with descriptions embedded in each listing page.

    company_slug is ignored. Incomplete or blocked traversals raise rather than
    returning a partial list that the pipeline might use for deletion reconciliation.
    Use python -m orchestration.djinni for a resumable local export.
    """

    ats = ATSType.DJINNI

    async def afetch(self) -> list[Job]:
        state = Snapshot()
        async with httpx.AsyncClient(
            timeout=self.timeout,
            proxy=self.proxy,
            trust_env=False,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html", "Accept-Language": "en"},
        ) as client:
            await collect(client, state, None)
        if state.status != "complete":
            raise ScraperError(f"Djinni {state.status}: {state.reason}")
        jobs = list(state.jobs.values())
        if not self.include_descriptions:
            for job in jobs:
                job.description = None
        return jobs
