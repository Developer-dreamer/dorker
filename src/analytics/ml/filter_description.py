import gc
import time
from logging import Logger
from typing import Any

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from src.shared.models import JobForAnalytics


class DescriptionFilter:
    def __init__(
        self, logger: Logger, clf: Any, embedder_name: str = "nomic-ai/nomic-embed-text-v1.5"
    ) -> None:
        self.logger = logger
        self.embedder_name = embedder_name
        self.clf = clf
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self._init_embedder(device=self.device)

    def _init_embedder(self, device: str) -> None:
        """Initializes or resets the embedder on the designated device."""
        self.embedder = SentenceTransformer(
            model_name_or_path=self.embedder_name,
            device=device,
            trust_remote_code=True,
            local_files_only=True,
        )

    def filter(self, jobs: list[JobForAnalytics]) -> list[JobForAnalytics]:
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
                    self._init_embedder(device=self.device)
                    return fallback_embeddings

                # 3. Dynamic backoff: reduce batch size and wait for OS memory defragmentation
                batch_size = max(4, batch_size // 2)
                time.sleep(3 * attempt)

        raise RuntimeError("Encoding failed after exhausting all retry strategies.")
