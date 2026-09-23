import asyncio
import time
from enum import Enum
from logging import Logger
from typing import Dict

from src.database import JobFactSheetRepository, JobRepository, MatchRepository
from src.shared.models import (
    DomainEntities,
    JobFactSheet,
    LocationEntities,
    MatchedJob,
    RedFlagsEntities,
    RuntimeVersion,
    SuitabilityTier,
)

from .classification import SLM, Classifier
from .ml.filter_description import DescriptionFilter


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
        jev: Classifier,
        slm: SLM | None = None,
        desc_filter: DescriptionFilter | None = None,
    ):
        self.logger = logger
        self.runtime_version = runtime_version
        self.job_repo = job_repo
        self.fact_sheet_repo = fact_sheet_repo
        self.match_repo = match_repo

        self.slm = slm
        self.jev = jev
        self.desc_filter = desc_filter

    async def run(self, run_type: MatchingType = MatchingType.REMOTE) -> None:
        if run_type == MatchingType.LOCAL:
            await self._run_slm()
        else:
            await self._run_jev()

    async def _run_slm(self) -> None:
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

    async def _run_jev(self) -> None:
        entries = await self.job_repo.get_matched_jobs()
        self.logger.info(f"Retrieved {len(entries)} candidate jobs from repository.")

        for idx, job in enumerate(entries, start=1):
            job_start = time.perf_counter()
            self.logger.info(
                f"--- [{idx}/{len(entries)}] Processing job {job.id} ({job.title}) ---"
            )

            try:
                res = await self.jev.classify(job)
                await self.match_repo.save_match(res)
            except Exception as e:
                self.logger.error(f"Error classifying job {job.id}: {str(e)}")
                return

            total_time = time.perf_counter() - job_start
            self.logger.info(
                f"[{job.id}] Successfully saved | Total time: {total_time:.2f}s. | Suitability tier: {res.suitability_tier}."
            )
