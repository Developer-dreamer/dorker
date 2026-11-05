import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

from litestar import Controller, get, patch, post
from litestar.di import NamedDependency
from litestar.exceptions import HTTPException
from litestar.openapi.datastructures import ResponseSpec
from litestar.params import FromPath, JSONBody, PathParameter, QueryParameter, SkipValidation

from src.api.dependencies import require_service
from src.api.exceptions import NotFoundException
from src.api.models import (
    ApplicationPatchInput,
    ApplicationResponse,
    ApplicationsResponse,
    GenerationInput,
)
from src.api.services import ApplicationService

logger = logging.getLogger(__name__)


class ApplicationHandler(Controller):
    path = "/api"
    tags = ["Applications"]
    responses = {
        422: ResponseSpec(None, description="Invalid request parameters or body."),
        503: ResponseSpec(None, description="The requested service is not configured."),
        404: ResponseSpec(None, description="Match or application not found."),
        502: ResponseSpec(None, description="Application generation failed."),
    }

    @contextmanager
    def _resource_errors(self, missing_detail: str) -> Iterator[None]:
        """Hide unexpected service validation details; HTTP inputs are validated separately."""
        try:
            yield
        except NotFoundException as error:
            raise HTTPException(status_code=404, detail=missing_detail) from error
        except ValueError as error:
            logger.exception("Application request failed")
            raise HTTPException(
                status_code=500, detail="Request failed. Check server logs."
            ) from error

    @post("/matches/{id:str}/applications", status_code=201, responses=responses)
    async def create_application(
        self,
        id: FromPath[UUID],
        data: JSONBody[GenerationInput],
        application_service: NamedDependency[SkipValidation[ApplicationService | None]],
    ) -> ApplicationResponse:
        application_service = require_service(application_service, "Application")
        with self._resource_errors("Match not found."):
            return await application_service.create_application(id, data)

    @get("/matches/{id:str}/applications", responses=responses)
    async def list_applications(
        self,
        id: FromPath[UUID],
        application_service: NamedDependency[SkipValidation[ApplicationService | None]],
        limit: Annotated[int, QueryParameter(ge=1, le=250)] = 50,
        offset: Annotated[int, QueryParameter(ge=0)] = 0,
    ) -> ApplicationsResponse:
        application_service = require_service(application_service, "Application")
        with self._resource_errors("Match not found."):
            return await application_service.list_applications(id, limit=limit, offset=offset)

    @get("/applications/{id:str}", responses=responses)
    async def get_application(
        self,
        id: Annotated[int, PathParameter(ge=1)],
        application_service: NamedDependency[SkipValidation[ApplicationService | None]],
    ) -> ApplicationResponse:
        application_service = require_service(application_service, "Application")
        with self._resource_errors("Application not found."):
            return await application_service.get_application(id)

    @patch("/applications/{id:str}", responses=responses)
    async def update_application(
        self,
        id: Annotated[int, PathParameter(ge=1)],
        data: JSONBody[ApplicationPatchInput],
        application_service: NamedDependency[SkipValidation[ApplicationService | None]],
    ) -> ApplicationResponse:
        application_service = require_service(application_service, "Application")
        # Preserve the Pydantic model_fields_set so omission differs from an explicit null.
        with self._resource_errors("Application not found."):
            return await application_service.update_application(id, data)

    @post("/applications/{id:str}/generation", status_code=200, responses=responses)
    async def regenerate_application(
        self,
        id: Annotated[int, PathParameter(ge=1)],
        data: JSONBody[GenerationInput],
        application_service: NamedDependency[SkipValidation[ApplicationService | None]],
    ) -> ApplicationResponse:
        application_service = require_service(application_service, "Application")
        with self._resource_errors("Application not found."):
            return await application_service.regenerate_application(id, data)
