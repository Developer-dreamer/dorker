from typing import Annotated

from litestar import Controller, get, post
from litestar.di import NamedDependency
from litestar.exceptions import HTTPException
from litestar.openapi.datastructures import ResponseSpec
from litestar.params import FromPath, JSONBody, QueryParameter, SkipValidation

from src.api.dependencies import require_service
from src.api.exceptions import NotFoundException
from src.api.models import JobResponse, JobsResponse, ScrapeJobInput, ScrapeJobResponse
from src.api.services import JobService


class JobHandler(Controller):
    path = "/api/jobs"
    tags = ["Jobs"]
    responses = {
        422: ResponseSpec(None, description="Invalid request parameters or body."),
        503: ResponseSpec(None, description="The requested service is not configured."),
        404: ResponseSpec(None, description="Job not found."),
    }

    @get(responses=responses)
    async def list_jobs(
        self,
        job_service: NamedDependency[SkipValidation[JobService | None]],
        limit: Annotated[int, QueryParameter(ge=1, le=250)] = 50,
        offset: Annotated[int, QueryParameter(ge=0)] = 0,
    ) -> JobsResponse:
        job_service = require_service(job_service, "Job")
        return await job_service.list_jobs(limit=limit, offset=offset)

    @post(
        "/scrape",
        status_code=201,
        responses={
            **responses,
            502: ResponseSpec(None, description="Scraping or classification provider failed."),
        },
    )
    async def scrape_job(
        self,
        data: JSONBody[ScrapeJobInput],
        job_service: NamedDependency[SkipValidation[JobService | None]],
    ) -> ScrapeJobResponse:
        job_service = require_service(job_service, "Job")
        return await job_service.scrape_job(str(data.url))

    @get("/{id:str}", responses=responses)
    async def get_job_by_id(
        self, id: FromPath[str], job_service: NamedDependency[SkipValidation[JobService | None]]
    ) -> JobResponse:
        job_service = require_service(job_service, "Job")
        try:
            return await job_service.get_job_by_id(id)
        except NotFoundException as error:
            raise HTTPException(status_code=404, detail="Job not found.") from error
