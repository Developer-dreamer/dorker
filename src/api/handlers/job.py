from dataclasses import dataclass
from logging import Logger

from fastapi import HTTPException


@dataclass
class JobResponse:
    id: str
    title: str
    company: str
    location: str
    salary_min: float| None
    salary_max: float| None
    salary_currency: str | None
    description: str
    created_at: str


class JobHandler:
    def __init__(self, logger: Logger, service: JobService):
        self.logger = logger
        self.service = service

    async def get_job_by_id(self) -> JobResponse:
        return await self.service.get_job_by_id()
