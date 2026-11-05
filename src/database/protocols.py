from datetime import timedelta
from typing import List, Protocol
from uuid import UUID

from src.shared import OpenAIBatchRecord
from src.shared.models import (
    ApplicationGeneratedResponse,
    ApplicationPacket,
    Job,
    JobAnalytics,
    JobFactSheet,
    JobForAnalytics,
    MatchCounts,
    MatchedJob,
    SuitabilityTier,
)


class JobRepository(Protocol):
    """Production reads return jobs with company identity hydrated by the same query."""

    async def get_matched_jobs(
        self, time_interval: timedelta = timedelta(weeks=1), limit: int = 1
    ) -> list[JobForAnalytics]: ...

    async def list_jobs(self, limit: int, offset: int = 0) -> tuple[list[JobForAnalytics], int]: ...
    async def get_job_by_id(self, job_id: str) -> JobForAnalytics | None: ...
    async def save_job(self, job: Job) -> JobForAnalytics: ...


class JobFactSheetRepository(Protocol):
    async def save_fact_sheet(self, sheet: JobFactSheet) -> None: ...


class MatchRepository(Protocol):
    """Missing detail reads return None; missing debug/mutation targets raise LookupError."""

    async def report_match(self, match_id: UUID, message: str) -> None: ...
    async def save_match(self, match: MatchedJob) -> None: ...
    async def get_matches(
        self,
        tiers: list[SuitabilityTier] | None,
        statuses: list[str] | None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[JobForAnalytics], int]:
        """Return a page and filtered total; None filters allow all latest eligible matches."""
        ...

    async def update_summary(self, match_id: UUID, summary: JobAnalytics) -> None: ...
    async def get_pending_summary_match_ids(self, limit: int = 100) -> list[UUID]: ...
    async def update_pipeline_status(self, match_id: UUID, status: str) -> None: ...
    async def get_matches_stats(self, pipeline_statuses: list[str] | None) -> MatchCounts: ...
    async def get_matched_job(
        self, tiers: List[SuitabilityTier], offset: int = 0
    ) -> JobForAnalytics | None: ...
    async def get_job_by_match_id(self, match_id: UUID) -> JobForAnalytics | None: ...
    async def get_latest_match_by_job_id(self, job_id: str) -> JobForAnalytics | None: ...
    async def get_match_debug(self, match_id: UUID) -> str | None: ...
    async def append_question(self, match_id: UUID, question: str) -> None: ...


class ApplicationPacketRepository(Protocol):
    """Return persisted models; missing mutation targets or parent matches raise LookupError."""

    async def save_packet(self, packet: ApplicationPacket) -> ApplicationPacket:
        """Insert completed content and also assign the supplied packet its database ID."""
        ...

    async def get_pending_packets(self, limit: int = 1) -> List[JobForAnalytics]: ...
    async def update_packet(
        self, packet_id: int, resp: ApplicationGeneratedResponse
    ) -> ApplicationPacket:
        """Preserve omitted fields; explicit null clears the letter, questions replace the list."""
        ...

    async def get_packet_by_id(self, packet_id: int) -> ApplicationPacket | None: ...
    async def list_packets(
        self, match_id: UUID, limit: int, offset: int = 0
    ) -> tuple[list[ApplicationPacket], int]: ...


class BatchRepository(Protocol):
    async def save_batch(self, batch: OpenAIBatchRecord) -> None: ...
