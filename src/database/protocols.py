from typing import Protocol

from src.shared.models import JobFactSheet, JobForAnalytics, MatchedJob


class JobRepository(Protocol):
    async def get_matched_jobs(self) -> list[JobForAnalytics]: ...


class JobFactSheetRepository(Protocol):
    async def save_fact_sheet(self, sheet: JobFactSheet) -> None: ...


class MatchRepository(Protocol):
    async def save_match(self, match: MatchedJob) -> None: ...
