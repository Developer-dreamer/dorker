"""Map repository domain objects to the public job contract."""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from openai import OpenAIError

from src.analytics.protocols import Classifier, ProfileRepository
from src.api.exceptions import JobScrapeException, NotFoundException
from src.api.models import JobResponse, JobsResponse, ScrapeJobResponse
from src.api.services.match import match_response
from src.api.services.protocols import JobScraper
from src.api.services.protocols import JobService as JobServiceProtocol
from src.database.protocols import JobRepository, MatchRepository
from src.shared.models import JobForAnalytics, MatchedJob, SuitabilityTier
from src.shared.scripts.bu import LoginWallException

logger = logging.getLogger(__name__)


def job_response(job: JobForAnalytics) -> JobResponse:
    # Production browsing queries always load the required URL and ATS identifier.
    if job.url is None or job.ats_type is None:
        raise ValueError("Repository returned incomplete job metadata.")
    return JobResponse(
        id=job.id,
        title=job.title,
        company=job.company.name if job.company else None,
        location=job.location,
        description=job.description,
        url=job.url,
        apply_url=job.apply_url,
        ats_type=job.ats_type,
        employment_type=job.employment_type,
        questions=job.questions,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        salary_currency=job.salary_currency,
        posted_at=job.posted_at,
        fetched_at=job.fetched_at,
    )


class JobService(JobServiceProtocol):
    def __init__(
        self,
        repository: JobRepository,
        *,
        match_repository: MatchRepository | None = None,
        classifier: Classifier[MatchedJob] | None = None,
        profile: ProfileRepository | None = None,
        scraper: JobScraper | None = None,
        summary_available: asyncio.Event | None = None,
    ) -> None:
        self.repository = repository
        self.match_repository = match_repository
        self.classifier = classifier
        self.profile = profile
        self.scraper = scraper
        self.summary_available = summary_available

    async def list_jobs(self, *, limit: int, offset: int) -> JobsResponse:
        jobs, total = await self.repository.list_jobs(limit, offset)
        return JobsResponse(
            jobs=[job_response(job) for job in jobs], total=total, limit=limit, offset=offset
        )

    async def get_job_by_id(self, job_id: str) -> JobResponse:
        job = await self.repository.get_job_by_id(job_id)
        if job is None:
            raise NotFoundException("Job not found.")
        return job_response(job)

    async def scrape_job(self, url: str) -> ScrapeJobResponse:
        if (
            self.scraper is None
            or self.match_repository is None
            or self.classifier is None
            or self.profile is None
        ):
            raise JobScrapeException("Job scraping service is not configured.")

        try:
            raw_job = await self.scraper.scrape(url)
        except LoginWallException as error:
            logger.warning("Login wall blocked job URL %s: %s", url, error)
            raise JobScrapeException(str(error)) from error
        except (httpx.HTTPError, OpenAIError, ValueError) as error:
            logger.error("Failed to scrape job URL %s", url, exc_info=error)
            raise JobScrapeException("Could not scrape job from the supplied URL.") from error

        saved_job = await self.repository.save_job(raw_job)

        try:
            matched_job = await self.classifier.classify(saved_job, self.profile)
        except (httpx.HTTPError, OpenAIError, ValueError) as error:
            logger.error("Failed to classify scraped job %s", saved_job.id, exc_info=error)
            raise JobScrapeException("Scraped job could not be classified.") from error

        await self.match_repository.save_match(matched_job)
        if (
            self.summary_available is not None
            and matched_job.suitability_tier != SuitabilityTier.REJECTED
        ):
            self.summary_available.set()

        persisted = await self.match_repository.get_job_by_match_id(matched_job.id)
        if persisted is None:
            matched_job.created_at = matched_job.created_at or datetime.now(timezone.utc)
            saved_job.match = matched_job
            persisted = saved_job

        return ScrapeJobResponse(
            job=job_response(saved_job),
            match=match_response(persisted),
        )
