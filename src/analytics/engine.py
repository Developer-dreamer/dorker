import asyncio
import time
from datetime import timedelta
from enum import Enum
from logging import Logger
from typing import Dict, List
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
    DomainEntities,
    JobFactSheet,
    LocationEntities,
    MatchedJob,
    RedFlagsEntities,
    RuntimeVersion,
    SuitabilityTier,
)

from ..shared import JobForAnalytics
from .classification import SLM, Classifier
from .ml.filter_description import DescriptionFilter
from .protocols import ApplicationGenerator


class MatchingType(str, Enum):
    LOCAL = "LOCAL"
    REMOTE = "REMOTE"


class MatchingEngine:
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
        application_generator: ApplicationGenerator[ApplicationGeneratedResponse] | None = None,
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

    async def run(self) -> None:
        await self._generate_summary()

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

    async def generate_application_for_match(
        self, job: JobForAnalytics
    ) -> ApplicationGeneratedResponse:
        assert self.application_generator is not None

        # 1. Classify profile for job
        packet = await self.profile_clf.classify(job)
        await self.application_repo.save_packet(packet)
        job.application = packet

        # 2. Generate Application (Cover Letter / Follow Up)
        resp = await self.application_generator.generate_sync(job)
        await self.application_repo.update_packet(job.application.id, resp)

        return resp

    async def _classify_jobs_slm(self) -> None:
        if self.slm is None or self.desc_filter is None:
            raise AttributeError(
                "You cannot select local pipeline if slm and filter is not configured."
            )

        entries = await self.job_repo.get_matched_jobs()
        entry_count = len(entries)
        self.logger.info(f"Retrieved {entry_count} candidate jobs from repository.")

        while entries:
            try:
                filtered_entries = self.desc_filter.filter(entries)
            except Exception as e:
                self.logger.exception(f"[{entry_count}] Pipeline extraction failed: {str(e)}")
                await asyncio.sleep(10)
                continue

            for idx, job in enumerate(filtered_entries, start=1):
                job_start = time.perf_counter()
                self.logger.info(
                    f"--- [{idx}/{len(filtered_entries)}] Processing job {job.id} ({job.title}) ---"
                )

                matched_job = MatchedJob(job_id=job.id)

                # Initialize default empty entities for partial state persistence
                location = LocationEntities()
                red_flags = RedFlagsEntities()
                global_slm_debug: Dict[str, str] = {}

                try:
                    domain = await asyncio.to_thread(self.slm.generate, job, DomainEntities)
                    should_proceed = domain.should_apply == "Apply"
                    global_slm_debug = domain.debug

                    if should_proceed:
                        location = await asyncio.to_thread(self.slm.generate, job, LocationEntities)
                        should_proceed = location.should_apply == "Apply"
                        global_slm_debug = global_slm_debug | location.debug
                    else:
                        matched_job.suitability_tier = SuitabilityTier.REJECTED
                        matched_job.rejection_reason = "Domain mismatch"

                    if should_proceed:
                        matched_job.suitability_tier = SuitabilityTier.SUITABLE
                        red_flags = await asyncio.to_thread(
                            self.slm.generate, job, RedFlagsEntities
                        )
                        global_slm_debug = global_slm_debug | red_flags.debug
                    else:
                        matched_job.suitability_tier = SuitabilityTier.REJECTED
                        matched_job.rejection_reason = "Location mismatch"

                    fact_sheet = JobFactSheet.from_llm_responses(
                        job.id, location, domain, red_flags
                    )
                    fact_sheet.debug = global_slm_debug

                    await self.fact_sheet_repo.save_fact_sheet(fact_sheet)
                    await self.match_repo.save_match(matched_job)

                    total_time = time.perf_counter() - job_start
                    self.logger.info(
                        f"[{job.id}] Successfully saved | Total time: {total_time:.2f}s | "
                        f"Scope: {location.geographic_scope} | Family: {domain.job_family} | "
                        f"Exp: {domain.min_years_experience} years"
                    )
                except Exception as e:
                    self.logger.exception(f"[{job.id}] Pipeline extraction failed: {str(e)}")

            entries = await self.job_repo.get_matched_jobs()
            self.logger.info(
                f"Finished {entry_count} entries. Proceeding with {len(entries)} more."
            )
            entry_count += len(entries)

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
            resp = await self.application_generator.generate_sync(job)

            await self.application_repo.update_packet(job.application.id, resp)

    async def _generate_summary(self) -> None:
        assert self.slm is not None

        matches = await self.match_repo.get_matches(SuitabilityTier.RUNWAY, limit=10000)
        self.logger.info(f"Retrieved {len(matches)} matches.")

        for idx, match in enumerate(matches, start=1):
            assert match.match is not None
            self.logger.info(f"--- [{idx}/{len(matches)}] Processing match {match.id} ---")

            summary = self.slm.generate(match, str)
            await self.match_repo.update_summary(match.match.id, summary)
