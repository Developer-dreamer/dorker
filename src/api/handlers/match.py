from typing import Annotated
from uuid import UUID

from litestar import Controller, get, patch, post
from litestar.di import NamedDependency
from litestar.exceptions import HTTPException
from litestar.openapi.datastructures import ResponseSpec
from litestar.params import FromPath, FromQuery, JSONBody, QueryParameter, SkipValidation

from src.api.dependencies import require_service
from src.api.exceptions import NotFoundException
from src.api.models import (
    MatchDebugResponse,
    MatchesResponse,
    MatchResponse,
    PipelineStatus,
    ReportInput,
    StatusInput,
)
from src.api.services import MatchService
from src.shared.models.match import SuitabilityTier


class MatchHandler(Controller):
    path = "/api/matches"
    tags = ["Matches"]
    responses = {
        422: ResponseSpec(None, description="Invalid request parameters or body."),
        503: ResponseSpec(None, description="The requested service is not configured."),
        404: ResponseSpec(None, description="Match not found."),
    }

    @get(responses=responses)
    async def list_matches(
        self,
        match_service: NamedDependency[SkipValidation[MatchService | None]],
        limit: Annotated[int, QueryParameter(ge=1, le=250)] = 50,
        offset: Annotated[int, QueryParameter(ge=0)] = 0,
        tiers: FromQuery[list[SuitabilityTier] | None] = None,
        pipeline_status: FromQuery[list[PipelineStatus] | None] = None,
    ) -> MatchesResponse:
        match_service = require_service(match_service, "Match")
        return await match_service.list_matches(
            limit=limit, offset=offset, tiers=tiers, pipeline_status=pipeline_status
        )

    @get("/applications", responses=responses)
    async def list_application_matches(
        self,
        match_service: NamedDependency[SkipValidation[MatchService | None]],
        limit: Annotated[int, QueryParameter(ge=1, le=250)] = 50,
        offset: Annotated[int, QueryParameter(ge=0)] = 0,
        tiers: FromQuery[list[SuitabilityTier] | None] = None,
        pipeline_status: FromQuery[list[PipelineStatus] | None] = None,
    ) -> MatchesResponse:
        match_service = require_service(match_service, "Match")
        return await match_service.list_application_matches(
            limit=limit, offset=offset, tiers=tiers, pipeline_status=pipeline_status
        )

    @get("/counts", responses=responses)
    async def get_counts(
        self,
        match_service: NamedDependency[SkipValidation[MatchService | None]],
        pipeline_status: FromQuery[list[PipelineStatus] | None] = None,
    ) -> dict[SuitabilityTier, int]:
        match_service = require_service(match_service, "Match")
        counts = await match_service.get_counts(pipeline_status=pipeline_status)
        return {tier: counts.get(tier, 0) for tier in SuitabilityTier}

    @get("/by-job/{job_id:str}", responses=responses)
    async def get_match_by_job_id(
        self,
        job_id: FromPath[str],
        match_service: NamedDependency[SkipValidation[MatchService | None]],
    ) -> MatchResponse:
        match_service = require_service(match_service, "Match")
        try:
            return await match_service.get_match_by_job_id(job_id)
        except NotFoundException as error:
            raise HTTPException(status_code=404, detail="Match not found.") from error

    @get("/{id:str}", responses=responses)
    async def get_match_by_id(
        self,
        id: FromPath[UUID],
        match_service: NamedDependency[SkipValidation[MatchService | None]],
    ) -> MatchResponse:
        match_service = require_service(match_service, "Match")
        try:
            return await match_service.get_match_by_id(id)
        except NotFoundException as error:
            raise HTTPException(status_code=404, detail="Match not found.") from error

    @get("/{id:str}/debug", responses=responses)
    async def get_debug(
        self,
        id: FromPath[UUID],
        match_service: NamedDependency[SkipValidation[MatchService | None]],
    ) -> MatchDebugResponse:
        match_service = require_service(match_service, "Match")
        try:
            return MatchDebugResponse(debug=await match_service.get_debug(id))
        except NotFoundException as error:
            raise HTTPException(status_code=404, detail="Match not found.") from error

    @post("/{id:str}/reports", status_code=204, responses=responses)
    async def post_report(
        self,
        id: FromPath[UUID],
        data: JSONBody[ReportInput],
        match_service: NamedDependency[SkipValidation[MatchService | None]],
    ) -> None:
        match_service = require_service(match_service, "Match")
        try:
            await match_service.report(id, data.text)
        except NotFoundException as error:
            raise HTTPException(status_code=404, detail="Match not found.") from error

    @patch("/{id:str}/status", responses=responses)
    async def update_status(
        self,
        id: FromPath[UUID],
        data: JSONBody[StatusInput],
        match_service: NamedDependency[SkipValidation[MatchService | None]],
    ) -> StatusInput:
        match_service = require_service(match_service, "Match")
        try:
            await match_service.update_pipeline_status(id, data.status)
        except NotFoundException as error:
            raise HTTPException(status_code=404, detail="Match not found.") from error
        return data
