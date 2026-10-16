from dataclasses import dataclass
from logging import Logger
from typing import List, Optional, Annotated

from fastapi import Body, HTTPException

from src.api.exceptions import NotFoundException
from src.shared.models import Question


@dataclass
class GenerationRequest:
    match_id: str

    cover_letter_required: bool
    questions_required: bool

    questions: Optional[List[Question]]

class GenerationResult:
    pass


class ApplicationHandler:
    def __init__(self, logger: Logger, service: ApplicationService):
        self.logger = logger
        self.service = service

    async def post_application(self, request: Annotated[GenerationRequest, Body()]) -> GenerationResult:
        try:
            await self.service.generate_application(request)
        except NotFoundException as e:
            raise HTTPException(
                status_code=404,
                detail=f"Can't generate an application for unexisting match. Unresolved id: {request.match_id}"
            ) from e
