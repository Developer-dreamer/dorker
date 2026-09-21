from typing import List, Protocol

from src.scraping.models import Job
from src.shared.models.company import ATS
from src.shared.models.job import Job as JobDomain


class JobRepository(Protocol):
    async def save_job_batch(self, jobs: List[JobDomain]) -> None: ...


class CompanyRepository(Protocol):
    async def get_tenants(self) -> List[ATS]: ...
    async def update_company_stats(
        self,
        is_success: bool,
        duration_ms: int,
        err: str | None,
        jobs_count: int,
        company_id: int,
    ) -> None: ...


class DescriptionCache(Protocol):
    async def close(self) -> None: ...
    async def get(self, job: Job) -> str | None: ...
    async def set(self, job: Job, description: str) -> None: ...


def description_keys(job: Job) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    url = str(job.url).strip()
    if job.ats_type.value == "icims":
        return [("url", url)] if url else []
    company = (job.company or "").strip()
    ats_id = (job.ats_id or "").strip()
    if company and ats_id:
        keys.append(("company_ats_id", f"{company}\0{ats_id}"))
    if url:
        keys.append(("url", url))
    return keys
