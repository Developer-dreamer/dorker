from dataclasses import dataclass
from logging import Logger
from typing import Annotated
from uuid import UUID

from fastapi import Body, HTTPException, Path, Query

from src.api.exceptions import NotFoundException


class Analytics:
    summary: str
    pros: str
    cons: str

@dataclass
class MatchResponse:
    id: str
    job_id: str
    tier: str
    pipeline_status: str
    technical_capability_score: float
    strategic_value_score: float
    analytics: Analytics
    created_at: str

@dataclass
class Report:
    text: str

class MatchHandler:
    def __init__(self, logger: Logger, service: MatchService):
        self.logger = logger
        self.service = service

    async def get_match_by_tier(self, tier: Annotated[str | None, Query()], offset: Annotated[int, Query()]) -> MatchResponse:
        return await self.service.get_match(tier, offset)

    async def post_report(self, match_id: Annotated[UUID, Path()], body: Annotated[Report, Body]) -> None:
        try:
            await self.service.report(match_id, body)
        except NotFoundException as error:
            raise HTTPException(
                status_code=404,
                detail=f"Match with id: {match_id} was not found."
            ) from error

    async def get_debug(self, match_id: Annotated[UUID, Path()]) -> str:
        try:
            return await self.service.get_debug(match_id)
        except NotFoundException as error:
            raise HTTPException(
                status_code=404,
                detail=f"Match with id: {match_id} was not found."
            ) from error
