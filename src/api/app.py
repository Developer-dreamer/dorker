"""Compose Litestar controllers and inject services; business rules stay behind protocols."""

import logging
from typing import Any

from litestar import Litestar, Request, Response
from litestar.config.cors import CORSConfig
from litestar.di import Provide
from litestar.exceptions import HTTPException, SerializationException, ValidationException
from litestar.openapi import OpenAPIConfig
from litestar.openapi.plugins import SwaggerRenderPlugin

from src.api.exceptions import (
    ApplicationGenerationException,
    DiscussionConflictException,
    DiscussionReplyException,
    JobScrapeException,
)
from src.api.handlers.application import ApplicationHandler
from src.api.handlers.discussion import DiscussionHandler
from src.api.handlers.evidence import EvidenceHandler
from src.api.handlers.job import JobHandler
from src.api.handlers.match import MatchHandler
from src.api.handlers.profile import ProfileHandler
from src.api.schema import StrictModelSchemaPlugin
from src.api.services import ApplicationService, DiscussionService, JobService, MatchService
from src.api.services.discussion import SerializedDiscussionService
from src.api.services.evidence import EvidenceServiceProtocol
from src.api.services.profile import ProfileServiceProtocol


def api_error(request: Request[Any, Any, Any], error: Exception) -> Response[dict[str, Any]]:
    """Keep public status/detail contracts independent of Litestar's default error envelope."""
    if isinstance(error, ValidationException):
        return Response({"detail": error.extra or error.detail}, status_code=422)
    if isinstance(error, HTTPException) and isinstance(error.__cause__, SerializationException):
        return Response({"detail": "Malformed request body."}, status_code=422)
    if isinstance(error, HTTPException):
        return Response(
            {"detail": error.detail}, status_code=error.status_code, headers=error.headers
        )
    if isinstance(error, ApplicationGenerationException):
        return Response({"detail": "Application generation failed."}, status_code=502)
    if isinstance(error, JobScrapeException):
        return Response({"detail": str(error) or "Job scraping failed."}, status_code=502)
    if isinstance(error, DiscussionConflictException):
        return Response({"detail": str(error)}, status_code=409)
    if isinstance(error, DiscussionReplyException):
        return Response({"detail": str(error), "saved": error.saved}, status_code=502)
    logging.getLogger(__name__).error("API request failed", exc_info=error)
    return Response({"detail": "Request failed. Check server logs."}, status_code=500)


def create_app(
    job_service: JobService | None = None,
    *,
    match_service: MatchService | None = None,
    discussion_service: DiscussionService | None = None,
    application_service: ApplicationService | None = None,
    evidence_service: EvidenceServiceProtocol | None = None,
    profile_service: ProfileServiceProtocol | None = None,
    allowed_origins: list[str] | None = None,
) -> Litestar:
    # A single decorator owns discussion locks for every request served by this app.
    serialized_discussion = (
        SerializedDiscussionService(discussion_service) if discussion_service is not None else None
    )

    # Providers return already constructed services; they do not create or own their lifetimes.
    async def provide_jobs() -> JobService | None:
        return job_service

    async def provide_matches() -> MatchService | None:
        return match_service

    async def provide_discussion() -> DiscussionService | None:
        return serialized_discussion

    async def provide_applications() -> ApplicationService | None:
        return application_service

    async def provide_evidence() -> EvidenceServiceProtocol | None:
        return evidence_service

    async def provide_profile() -> ProfileServiceProtocol | None:
        return profile_service

    return Litestar(
        # Controllers declare their own paths; construction has no router-registration side effects.
        route_handlers=[
            JobHandler,
            MatchHandler,
            DiscussionHandler,
            ApplicationHandler,
            EvidenceHandler,
            ProfileHandler,
        ],
        dependencies={
            "job_service": Provide(provide_jobs),
            "match_service": Provide(provide_matches),
            "discussion_service": Provide(provide_discussion),
            "application_service": Provide(provide_applications),
            "evidence_service": Provide(provide_evidence),
            "profile_service": Provide(provide_profile),
        },
        exception_handlers={Exception: api_error},
        cors_config=CORSConfig(
            allow_origins=allowed_origins or ["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Content-Type"],
        ),
        openapi_config=OpenAPIConfig(
            title="Dorker API",
            version="0.1.0",
            path="/",
            render_plugins=[SwaggerRenderPlugin(path="/docs")],
        ),
        plugins=[StrictModelSchemaPlugin()],
        logging_config=None,
    )
