"""Evidence routes delegate all operations to an injected, owner-scoped service."""

from contextlib import contextmanager
from typing import Iterator
from uuid import UUID

from litestar import Controller, delete, get, patch, post
from litestar.di import NamedDependency
from litestar.exceptions import HTTPException
from litestar.openapi.datastructures import ResponseSpec
from litestar.params import FromPath, FromQuery, JSONBody, SkipValidation

from src.api.dependencies import require_service
from src.api.evidence_models import (
    EvidenceCreate,
    EvidencePatch,
    FolderInput,
    MatchEvidenceInput,
)
from src.api.services.evidence import EvidenceServiceProtocol
from src.database.evidence import EvidenceConflict, EvidenceNotFound
from src.shared.models.evidence import Evidence, EvidenceFolder, MatchEvidence

EvidenceDependency = NamedDependency[SkipValidation[EvidenceServiceProtocol | None]]


class EvidenceHandler(Controller):
    path = "/api"
    tags = ["Evidence"]
    responses = {
        404: ResponseSpec(None, description="Resource not found."),
        409: ResponseSpec(None, description="Library state conflict."),
        422: ResponseSpec(None, description="Invalid input."),
        503: ResponseSpec(None, description="Evidence service is not configured."),
    }

    @contextmanager
    def _errors(self) -> Iterator[None]:
        try:
            yield
        except EvidenceNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except EvidenceConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @get("/folders")
    async def list_folders(self, evidence_service: EvidenceDependency) -> list[EvidenceFolder]:
        with self._errors():
            return await require_service(evidence_service, "Evidence").list_folders()

    @post("/folders", status_code=201)
    async def create_folder(
        self, data: JSONBody[FolderInput], evidence_service: EvidenceDependency
    ) -> EvidenceFolder:
        with self._errors():
            return await require_service(evidence_service, "Evidence").create_folder(data.path)

    @delete("/folders", status_code=204)
    async def delete_folder(
        self, path: FromQuery[str], evidence_service: EvidenceDependency
    ) -> None:
        with self._errors():
            await require_service(evidence_service, "Evidence").delete_folder(path)

    @get("/evidence")
    async def list_evidence(
        self,
        evidence_service: EvidenceDependency,
        folder_path: FromQuery[str | None] = None,
        matching_only: FromQuery[bool] = False,
    ) -> list[Evidence]:
        with self._errors():
            return await require_service(evidence_service, "Evidence").list_evidence(
                folder_path, matching_only
            )

    @post("/evidence", status_code=201)
    async def create_evidence(
        self, data: JSONBody[EvidenceCreate], evidence_service: EvidenceDependency
    ) -> Evidence:
        with self._errors():
            return await require_service(evidence_service, "Evidence").create_evidence(data)

    @get("/evidence/{id:str}")
    async def get_evidence(
        self, id: FromPath[UUID], evidence_service: EvidenceDependency
    ) -> Evidence:
        with self._errors():
            return await require_service(evidence_service, "Evidence").get_evidence(id)

    @patch("/evidence/{id:str}")
    async def update_evidence(
        self,
        id: FromPath[UUID],
        data: JSONBody[EvidencePatch],
        evidence_service: EvidenceDependency,
    ) -> Evidence:
        with self._errors():
            return await require_service(evidence_service, "Evidence").update_evidence(id, data)

    @delete("/evidence/{id:str}", status_code=204)
    async def delete_evidence(
        self, id: FromPath[UUID], evidence_service: EvidenceDependency
    ) -> None:
        with self._errors():
            await require_service(evidence_service, "Evidence").delete_evidence(id)

    @get("/matches/{id:str}/evidence")
    async def get_selection(
        self, id: FromPath[UUID], evidence_service: EvidenceDependency
    ) -> MatchEvidence:
        with self._errors():
            return await require_service(evidence_service, "Evidence").get_match_evidence(id)

    @patch("/matches/{id:str}/evidence")
    async def set_selection(
        self,
        id: FromPath[UUID],
        data: JSONBody[MatchEvidenceInput],
        evidence_service: EvidenceDependency,
    ) -> MatchEvidence:
        with self._errors():
            return await require_service(evidence_service, "Evidence").set_match_evidence(id, data)
