import asyncio
import time
from datetime import timedelta
from enum import Enum
from logging import Logger
from typing import List, cast
from uuid import UUID

from src.database import (
    ApplicationPacketRepository,
    JobFactSheetRepository,
    JobRepository,
    MatchRepository,
)
from src.shared.models import (
    ApplicationGeneratedResponse,
    ApplicationPacket,
    JobForAnalytics,
    MatchedJob,
    OpenAIBaseResponse,
    RequestedCoverletter,
    RequestedFullResponse,
    RequestedGeneration,
    RequestedQuestions,
    RuntimeVersion,
    SuitabilityTier,
)

from .classification import SLM, Classifier
from .ml.filter_description import DescriptionFilter
from .protocols import ApplicationGenerator, MatchingProvider


class MatchingType(str, Enum):
    LOCAL = "LOCAL"
    REMOTE = "REMOTE"


class MatchingEngine(MatchingProvider):
    def __init__(
        self,
        logger: Logger,
        runtime_version: RuntimeVersion,
        job_repo: JobRepository,
        fact_sheet_repo: JobFactSheetRepository,
        match_repo: MatchRepository,
        application_repo: ApplicationPacketRepository,
        job_clf: Classifier[MatchedJob],
        profile_clf: Classifier[ApplicationPacket],
        application_generator: ApplicationGenerator[OpenAIBaseResponse] | None = None,
        slm: SLM | None = None,
        desc_filter: DescriptionFilter | None = None,
    ):
        self.logger = logger
        self.runtime_version = runtime_version
        self.job_repo = job_repo
        self.fact_sheet_repo = fact_sheet_repo
        self.match_repo = match_repo
        self.application_repo = application_repo

        self.application_generator = application_generator
        self.slm = slm
        self.job_clf = job_clf
        self.profile_clf = profile_clf
        self.desc_filter = desc_filter

    async def classify_background(self) -> None:
        await self._classify_jobs_jev()

    async def get_matching_count(self) -> dict[str, int]:
        return await self.match_repo.get_matches_stats()

    async def get_matched_job(
        self, tiers: List[SuitabilityTier], offset: int = 0
    ) -> JobForAnalytics | None:
        return await self.match_repo.get_matched_job(tiers, offset)

    async def get_job_by_match_id(self, match_id: UUID) -> JobForAnalytics | None:
        return await self.match_repo.get_job_by_match_id(match_id)

    async def update_pipeline_status(self, match_id: UUID, status: str) -> None:
        return await self.match_repo.update_pipeline_status(match_id, status)

    async def append_question(self, match_id: UUID, question: str) -> None:
        return await self.match_repo.append_question(match_id, question)

    def _to_application_response(
        self, request: RequestedGeneration, resp: OpenAIBaseResponse
    ) -> ApplicationGeneratedResponse:
        application = ApplicationGeneratedResponse()
        application.debug = resp.debug
        if request.cover_letter and request.questions:
            resp_full = cast(RequestedFullResponse, resp)
            application.cover_letter = resp_full.cover_letter
            application.questions = resp_full.questions
        elif request.questions:
            resp_q = cast(RequestedQuestions, resp)
            application.questions = resp_q.questions
        elif request.cover_letter:
            resp_c = cast(RequestedCoverletter, resp)
            application.cover_letter = resp_c.cover_letter

        return application

    async def generate_application_for_match(
        self, request: RequestedGeneration, job: JobForAnalytics
    ) -> ApplicationGeneratedResponse:
        assert self.application_generator is not None

        # 1. Classify profile for job
        packet = await self.profile_clf.classify(job)
        await self.application_repo.save_packet(packet)
        job.application = packet

        # 2. Generate Application (Cover Letter / Follow Up)
        resp = await self.application_generator.generate_sync(request, job)
        application = self._to_application_response(request, resp)

        await self.application_repo.update_packet(job.application.id, application)
        return application

    async def _classify_jobs_jev(self) -> None:
        entries = await self.job_repo.get_matched_jobs(time_interval=timedelta(weeks=5), limit=1000)
        self.logger.info(f"Retrieved {len(entries)} candidate jobs from repository.")

        for idx, job in enumerate(entries, start=1):
            job_start = time.perf_counter()
            self.logger.info(
                f"--- [{idx}/{len(entries)}] Processing job {job.id} ({job.title}) ---"
            )

            try:
                res = await self.job_clf.classify(job)
                if self.slm is not None and res.suitability_tier != SuitabilityTier.REJECTED:
                    summary = await asyncio.to_thread(self.slm.generate, job, str)
                    res.job_summary = summary

                await self.match_repo.save_match(res)
            except Exception as e:
                self.logger.error(f"Error classifying job {job.id}: {str(e)}")
                return

            total_time = time.perf_counter() - job_start
            self.logger.info(
                f"[{job.id}] Successfully saved | Total time: {total_time:.2f}s. | Suitability tier: {res.suitability_tier}."
            )

    async def _classify_profile_jev(self) -> None:
        jobs = await self.match_repo.get_matches(SuitabilityTier.RUNWAY)
        self.logger.info(f"Retrieved {len(jobs)} jobs for profile classification.")

        for idx, job in enumerate(jobs, start=1):
            self.logger.info(f"--- [{idx}/{len(jobs)}] Processing job {job.id} ({job.title}) ---")
            try:
                packet = await self.profile_clf.classify(job)
                await self.application_repo.save_packet(packet)
            except Exception as e:
                self.logger.error(f"Error classifying job {job.id}: {str(e)}")
                continue

    async def _generate_applications(self) -> None:
        assert self.application_generator is not None

        jobs = await self.application_repo.get_pending_packets(limit=100)
        self.logger.info(f"Retrieved {len(jobs)} jobs for cover letter generation.")

        for idx, job in enumerate(jobs, start=1):
            assert job.application is not None

            self.logger.info(f"--- [{idx}/{len(jobs)}] Processing job {job.id} ({job.title}) ---")
            request = RequestedGeneration(cover_letter=True, questions=bool(job.questions))
            resp = await self.application_generator.generate_sync(request, job)
            application = self._to_application_response(request, resp)

            await self.application_repo.update_packet(job.application.id, application)
