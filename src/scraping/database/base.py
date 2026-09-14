from typing import List, Protocol

from models import JobDB
from pydantic import BaseModel


class JobRepository(Protocol):
    async def save_job_batch(self, jobs: List[JobDB]) -> None: ...


class ATSCompany(BaseModel):
    id: int
    name: str
    slug: str
    url: str

class ATS(BaseModel):
    name: str
    tier: int

    companies: List[ATSCompany]


class CompanyRepository(Protocol):
    async def get_tenants(self) -> List[ATS]: ...
    async def update_company_stats(self, is_success: bool,
                                   duration_ms: int,
                                   err: Exception | None,
                                   jobs_count: int,
                                   company_id) -> None:...
