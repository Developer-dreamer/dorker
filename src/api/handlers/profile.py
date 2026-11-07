from contextlib import contextmanager
from typing import Iterator
from uuid import UUID
from zoneinfo import available_timezones

from litestar import Controller, get, patch, post
from litestar.di import NamedDependency
from litestar.exceptions import HTTPException
from litestar.openapi.datastructures import ResponseSpec
from litestar.params import FromPath, JSONBody, SkipValidation
from pydantic import ValidationError

from src.api.dependencies import require_service
from src.api.evidence_models import EvidenceCreate
from src.api.profile_models import EvidenceLinksInput, ProfilePatch
from src.api.services.profile import ProfileServiceProtocol
from src.database.evidence import EvidenceConflict, EvidenceNotFound
from src.shared.models.profile import (
    COUNTRY_CODES,
    CareerCollection,
    CareerRecordReference,
    ProfileOptions,
    UserProfile,
)

ProfileDependency = NamedDependency[SkipValidation[ProfileServiceProtocol | None]]


class ProfileHandler(Controller):
    path = "/api/profile"
    tags = ["Profile"]
    responses = {
        404: ResponseSpec(None, description="Resource not found."),
        409: ResponseSpec(None, description="Profile conflict."),
        422: ResponseSpec(None, description="Invalid profile."),
        503: ResponseSpec(None, description="Profile service not configured."),
    }

    @contextmanager
    def _errors(self) -> Iterator[None]:
        try:
            yield
        except EvidenceNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except EvidenceConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValidationError as error:
            item = error.errors(include_input=False)[0]
            location = ".".join(str(part) for part in item["loc"])
            raise HTTPException(status_code=422, detail=f"{location}: {item['msg']}") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @get("/options")
    async def options(self) -> ProfileOptions:
        return ProfileOptions(
            countries=sorted(COUNTRY_CODES), timezones=sorted(available_timezones())
        )

    @get()
    async def get_profile(self, profile_service: ProfileDependency) -> UserProfile:
        with self._errors():
            return await require_service(profile_service, "Profile").get_profile()

    @patch()
    async def update_profile(
        self, data: JSONBody[ProfilePatch], profile_service: ProfileDependency
    ) -> UserProfile:
        with self._errors():
            return await require_service(profile_service, "Profile").update_profile(data)

    @patch("/{collection:str}/{id:str}/evidence-links")
    async def set_links(
        self,
        collection: FromPath[CareerCollection],
        id: FromPath[UUID],
        data: JSONBody[EvidenceLinksInput],
        profile_service: ProfileDependency,
    ) -> UserProfile:
        with self._errors():
            return await require_service(profile_service, "Profile").set_evidence_links(
                CareerRecordReference(collection=collection, entry_id=id), data
            )

    @post("/{collection:str}/{id:str}/evidence", status_code=201)
    async def create_evidence(
        self,
        collection: FromPath[CareerCollection],
        id: FromPath[UUID],
        data: JSONBody[EvidenceCreate],
        profile_service: ProfileDependency,
    ) -> UserProfile:
        with self._errors():
            return await require_service(profile_service, "Profile").create_linked_evidence(
                CareerRecordReference(collection=collection, entry_id=id), data
            )
