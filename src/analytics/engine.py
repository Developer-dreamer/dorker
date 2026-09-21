import asyncio
import gc
import time
from logging import Logger
from typing import Any, Dict

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from .database import JobFactSheetRepository, JobRepository, MatchRepository
from .jev import Jev
from .models import (
    DomainEntities,
    JobFactSheet,
    JobForAnalytics,
    LocationEntities,
    MatchedJob,
    RedFlagsEntities,
    RuntimeVersion,
    SuitabilityTier,
)
from .slm import SLM


class MatchingEngine:
    def __init__(
        self,
        logger: Logger,
        runtime_version: RuntimeVersion,
        job_repo: JobRepository,
        fact_sheet_repo: JobFactSheetRepository,
        match_repo: MatchRepository,
        clf: Any | None = None,
        slm: SLM | None = None,
        jev: Jev | None = None,
    ):
        self.logger = logger
        self.runtime_version = runtime_version
        self.job_repo = job_repo
        self.fact_sheet_repo = fact_sheet_repo
        self.match_repo = match_repo

        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.clf = clf

        self.slm = slm

        self.jev = jev

    def _init_embedder(self, device: str) -> None:
        """Initializes or resets the embedder on the designated device."""
        self.embedder = SentenceTransformer(
            "nomic-ai/nomic-embed-text-v1.5",
            device=device,
            trust_remote_code=True,
            local_files_only=True,
        )

    async def run_slm(self) -> None:
        assert self.slm is not None

        entries = await self.job_repo.get_matched_jobs()
        entry_count = len(entries)
        self.logger.info(f"Retrieved {entry_count} candidate jobs from repository.")

        self.slm.load_model()

        while entries:
            try:
                filtered_entries = self._filter_descriptions(entries)
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
                    domain, debug_domain = await asyncio.to_thread(
                        self.slm.generate, job, DomainEntities
                    )
                    should_proceed = domain.should_apply == "Apply"
                    global_slm_debug = debug_domain

                    if should_proceed:
                        location, debug_location = await asyncio.to_thread(
                            self.slm.generate, job, LocationEntities
                        )
                        should_proceed = location.should_apply == "Apply"
                        global_slm_debug = global_slm_debug | debug_location
                    else:
                        matched_job.suitability_tier = SuitabilityTier.REJECTED
                        matched_job.rejection_reason = "Domain mismatch"

                    if should_proceed:
                        matched_job.suitability_tier = SuitabilityTier.SUITABLE
                        red_flags, debug_redflags = await asyncio.to_thread(
                            self.slm.generate, job, RedFlagsEntities
                        )
                        global_slm_debug = global_slm_debug | debug_redflags
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

    async def run_jev(self) -> None:
        if self.jev is None:
            return

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

    def _filter_descriptions(self, jobs: list[JobForAnalytics]) -> list[JobForAnalytics]:
        assert self.clf is not None

        valid_jobs: list[JobForAnalytics] = []
        for job in jobs:
            if not job.description or not job.description.strip():
                self.logger.warning(
                    f"Job {getattr(job, 'id', 'unknown')} has empty description; skipping classification."
                )
                job.description_blocks = []
                continue
            valid_jobs.append(job)

        if not valid_jobs:
            return []

        jobs = valid_jobs
        flat_blocks: list[str] = []
        job_slices: list[slice] = []

        start_idx = 0
        for job in jobs:
            blocks = [b.strip() for b in job.description.split("\n\n") if b.strip()]
            flat_blocks.extend(blocks)
            end_idx = start_idx + len(blocks)
            job_slices.append(slice(start_idx, end_idx))
            start_idx = end_idx

        if not flat_blocks:
            for job in jobs:
                job.description_blocks = []
            return jobs

        # 1. Safe batch encode across all aggregated blocks
        all_vectors = self._safe_encode(flat_blocks)

        # 2. Predict probabilities using the classifier
        all_probs = self.clf.predict_proba(all_vectors)
        classes = list(self.clf.classes_)

        # 3. Assign (top_class, top_proba, text) back to each job
        for job, s in zip(jobs, job_slices, strict=True):
            job_blocks = flat_blocks[s]
            probs = all_probs[s]

            if len(job_blocks) == 0:
                job.description_blocks = []
                continue

            top_indices = np.argmax(probs, axis=1)

            job.description_blocks = [
                (classes[class_idx], float(probs[i, class_idx]), block)
                for i, (class_idx, block) in enumerate(zip(top_indices, job_blocks, strict=True))
            ]

        return jobs

    def _safe_encode(self, texts: list[str], max_retries: int = 3) -> np.ndarray:
        """
        Encodes a list of strings with MPS OOM retry and CPU fallback.
        Returns a 2D numpy array ready for scikit-learn classifiers.
        """
        batch_size = 16
        for attempt in range(1, max_retries + 1):
            try:
                # convert_to_numpy=True ensures embeddings are copied to host RAM immediately
                embeddings = self.embedder.encode(
                    texts,
                    batch_size=batch_size,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                )

                # Promptly free temporary MPS allocations
                if torch.backends.mps.is_available():
                    torch.mps.empty_cache()
                gc.collect()

                return embeddings

            except RuntimeError as e:
                err_msg = str(e).lower()
                is_mps_oom = "mps" in err_msg and ("out of memory" in err_msg or "alloc" in err_msg)

                if not is_mps_oom:
                    raise e

                self.logger.warning(
                    f"MPS allocation failure on attempt {attempt}/{max_retries}. Purging cache..."
                )

                # 1. Purge corrupted PyTorch GPU state
                if torch.backends.mps.is_available():
                    torch.mps.empty_cache()
                gc.collect()

                # 2. Final attempt exhausted -> fallback to CPU
                if attempt == max_retries:
                    self.logger.error(
                        "Max MPS retries reached. Falling back to CPU for this batch."
                    )
                    self._init_embedder(device="cpu")
                    fallback_embeddings = self.embedder.encode(
                        texts,
                        batch_size=32,
                        show_progress_bar=False,
                        convert_to_numpy=True,
                    )
                    # Restore embedder to MPS for subsequent calls
                    self._init_embedder(device="mps")
                    return fallback_embeddings

                # 3. Dynamic backoff: reduce batch size and wait for OS memory defragmentation
                batch_size = max(4, batch_size // 2)
                time.sleep(3 * attempt)

        raise RuntimeError("Encoding failed after exhausting all retry strategies.")
