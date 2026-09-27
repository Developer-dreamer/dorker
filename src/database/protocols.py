from datetime import timedelta
from typing import List, Protocol
from uuid import UUID

from src.shared import OpenAIBatchRecord
from src.shared.models import (
    ApplicationGeneratedResponse,
    ApplicationPacket,
    JobFactSheet,
    JobForAnalytics,
    MatchedJob,
    SuitabilityTier,
)


class JobRepository(Protocol):
    async def get_matched_jobs(
        self, time_interval: timedelta = timedelta(weeks=1), limit: int = 1
    ) -> list[JobForAnalytics]: ...


class JobFactSheetRepository(Protocol):
    async def save_fact_sheet(self, sheet: JobFactSheet) -> None: ...


class MatchRepository(Protocol):
    async def save_match(self, match: MatchedJob) -> None: ...
    async def get_matches(
        self, tier: SuitabilityTier, limit: int = 1000
    ) -> list[JobForAnalytics]: ...
    async def update_summary(self, match_id: UUID, summary: str) -> None: ...
    async def update_pipeline_status(self, match_id: UUID, status: str) -> None: ...
    async def get_matches_stats(self) -> dict[str, int]: ...
    async def get_matched_job(
        self, tiers: List[SuitabilityTier], offset: int = 0
    ) -> JobForAnalytics | None: ...
    async def get_job_by_match_id(self, match_id: UUID) -> JobForAnalytics | None: ...


class ApplicationPacketRepository(Protocol):
    async def save_packet(self, packet: ApplicationPacket) -> None: ...
    async def get_pending_packets(self, limit: int = 1) -> List[JobForAnalytics]: ...
    async def update_packet(self, packet_id: int, resp: ApplicationGeneratedResponse) -> None: ...


class BatchRepository(Protocol):
    async def save_batch(self, batch: OpenAIBatchRecord) -> None: ...
