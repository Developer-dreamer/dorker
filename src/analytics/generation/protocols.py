from typing import List, Protocol, Tuple
from uuid import UUID

from src.shared.models import JobForAnalytics


class ApplicationGenerator[T](Protocol):
    async def generate_sync(self, job: JobForAnalytics) -> T: ...
    async def queue_generation(self, jobs: List[JobForAnalytics]) -> None: ...
    async def harvest_results(self) -> List[Tuple[UUID, T]]: ...
