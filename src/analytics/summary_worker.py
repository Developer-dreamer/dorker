import asyncio
from logging import Logger

from src.analytics.protocols import SummaryGenerator
from src.database.protocols import MatchRepository
from src.shared.models import SuitabilityTier


class SummaryWorker:
    """Consume missing summaries from PostgreSQL; the event only reduces polling latency."""

    def __init__(
        self,
        repository: MatchRepository,
        generator: SummaryGenerator,
        available: asyncio.Event,
        logger: Logger,
        poll_seconds: float = 60,
        batch_size: int = 100,
    ) -> None:
        if poll_seconds <= 0 or batch_size <= 0:
            raise ValueError("Summary polling interval and batch size must be positive.")
        self.repository = repository
        self.generator = generator
        self.available = available
        self.logger = logger
        self.poll_seconds = poll_seconds
        self.batch_size = batch_size

    async def run(self) -> None:
        while True:
            # Clear before reading: notifications during the query/API call remain visible.
            self.available.clear()
            try:
                if await self._process_pending():
                    continue  # Drain a successful full batch without waiting a minute.
            except Exception:
                self.logger.exception("Could not retrieve pending summaries; retrying later.")
            try:
                await asyncio.wait_for(self.available.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def _process_pending(self) -> bool:
        match_ids = await self.repository.get_pending_summary_match_ids(self.batch_size)
        failed = False
        for match_id in match_ids:
            try:
                job = await self.repository.get_job_by_match_id(match_id)
                if (
                    job is None
                    or job.match is None
                    or job.match.suitability_tier == SuitabilityTier.REJECTED
                    or (job.match.job_summary and job.match.job_summary.summary.strip())
                ):
                    continue
                summary = await self.generator.generate(job)
                await self.repository.update_summary(match_id, summary)
                self.logger.info("[%s] Summary saved.", match_id)
            except Exception:
                failed = True
                self.logger.exception("[%s] Summary failed; left pending for retry.", match_id)
        return len(match_ids) == self.batch_size and not failed
