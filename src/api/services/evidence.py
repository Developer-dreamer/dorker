"""Owner-scoped evidence operations. No provider or database driver in this layer."""

from typing import Protocol
from uuid import UUID

from src.database.evidence import EvidenceRepository
from src.shared.models import Material
from src.shared.models.evidence import (
    Evidence,
    EvidenceDraft,
    EvidenceFolder,
    EvidenceUpdate,
    MatchEvidence,
    MatchEvidenceUpdate,
    validate_folder_path,
)


class EvidenceServiceProtocol(Protocol):
    async def list_folders(self) -> list[EvidenceFolder]: ...
    async def create_folder(self, path: str) -> EvidenceFolder: ...
    async def delete_folder(self, path: str) -> None: ...
    async def list_evidence(
        self, folder_path: str | None, matching_only: bool
    ) -> list[Evidence]: ...
    async def get_evidence(self, evidence_id: UUID) -> Evidence: ...
    async def create_evidence(self, data: EvidenceDraft) -> Evidence: ...
    async def update_evidence(self, evidence_id: UUID, patch: EvidenceUpdate) -> Evidence: ...
    async def delete_evidence(self, evidence_id: UUID) -> None: ...
    async def get_match_evidence(self, match_id: UUID) -> MatchEvidence: ...
    async def set_match_evidence(
        self, match_id: UUID, update: MatchEvidenceUpdate
    ) -> MatchEvidence: ...


class EvidenceService:
    def __init__(self, repository: EvidenceRepository, *, user_id: int) -> None:
        self.repository = repository
        self.user_id = user_id

    async def list_folders(self) -> list[EvidenceFolder]:
        return await self.repository.list_folders(self.user_id)

    async def create_folder(self, path: str) -> EvidenceFolder:
        return await self.repository.create_folder(self.user_id, validate_folder_path(path))

    async def delete_folder(self, path: str) -> None:
        await self.repository.delete_folder(self.user_id, validate_folder_path(path))

    async def list_evidence(
        self, folder_path: str | None = None, matching_only: bool = False
    ) -> list[Evidence]:
        if folder_path is not None:
            validate_folder_path(folder_path)
        return await self.repository.list_evidence(
            self.user_id, folder_path=folder_path, matching_only=matching_only
        )

    async def get_evidence(self, evidence_id: UUID) -> Evidence:
        return await self.repository.get_evidence(self.user_id, evidence_id)

    async def create_evidence(self, data: EvidenceDraft) -> Evidence:
        return await self.repository.create_evidence(self.user_id, data.new_evidence())

    async def update_evidence(self, evidence_id: UUID, patch: EvidenceUpdate) -> Evidence:
        return await self.repository.update_evidence(self.user_id, evidence_id, patch)

    async def delete_evidence(self, evidence_id: UUID) -> None:
        await self.repository.delete_evidence(self.user_id, evidence_id)

    async def get_match_evidence(self, match_id: UUID) -> MatchEvidence:
        return await self.repository.get_match_evidence(self.user_id, match_id)

    async def set_match_evidence(
        self, match_id: UUID, update: MatchEvidenceUpdate
    ) -> MatchEvidence:
        return await self.repository.set_match_evidence(self.user_id, match_id, update)

    async def matching_materials(self) -> list[Material]:
        return [
            Material(id=str(item.id), text=item.text)
            for item in await self.list_evidence(matching_only=True)
        ]

    async def selected_materials(self, match_id: UUID) -> list[Material]:
        selection = await self.get_match_evidence(match_id)
        selected = set(selection.checked_ids)
        return [
            Material(id=str(item.id), text=item.text)
            for item in selection.evidence
            if item.id in selected
        ]
