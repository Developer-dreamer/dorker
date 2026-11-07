"""Evidence persistence contract; all methods are scoped to the configured owner."""

from typing import Protocol
from uuid import UUID

from src.shared.models.evidence import (
    Evidence,
    EvidenceFolder,
    EvidenceUpdate,
    MatchEvidence,
    MatchEvidenceUpdate,
    NewEvidence,
)


class EvidenceRepository(Protocol):
    async def list_folders(self, user_id: int) -> list[EvidenceFolder]: ...
    async def create_folder(self, user_id: int, path: str) -> EvidenceFolder: ...
    async def delete_folder(self, user_id: int, path: str) -> None: ...
    async def list_evidence(
        self, user_id: int, *, folder_path: str | None, matching_only: bool
    ) -> list[Evidence]: ...
    async def get_evidence(self, user_id: int, evidence_id: UUID) -> Evidence: ...
    async def create_evidence(self, user_id: int, evidence: NewEvidence) -> Evidence: ...
    async def update_evidence(
        self, user_id: int, evidence_id: UUID, update: EvidenceUpdate
    ) -> Evidence: ...
    async def delete_evidence(self, user_id: int, evidence_id: UUID) -> None: ...
    async def get_match_evidence(self, user_id: int, match_id: UUID) -> MatchEvidence: ...
    async def set_match_evidence(
        self, user_id: int, match_id: UUID, update: MatchEvidenceUpdate
    ) -> MatchEvidence: ...


class EvidenceNotFound(Exception):
    """An owner-scoped resource does not exist."""


class EvidenceConflict(Exception):
    """The operation conflicts with the current library state."""
