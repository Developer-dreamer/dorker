from typing import List, Protocol

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
    async def get_matched_jobs(self) -> list[JobForAnalytics]: ...


class JobFactSheetRepository(Protocol):
    async def save_fact_sheet(self, sheet: JobFactSheet) -> None: ...


class MatchRepository(Protocol):
    async def save_match(self, match: MatchedJob) -> None: ...
    async def get_matches(self, tier: SuitabilityTier) -> list[JobForAnalytics]: ...


class ApplicationPacketRepository(Protocol):
    async def save_packet(self, packet: ApplicationPacket) -> None: ...
    async def get_pending_packets(self) -> List[JobForAnalytics]: ...
    async def update_packet(self, packet_id: int, resp: ApplicationGeneratedResponse) -> None: ...


class BatchRepository(Protocol):
    async def save_batch(self, batch: OpenAIBatchRecord) -> None: ...
