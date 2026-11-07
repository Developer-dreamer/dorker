"""Profile use cases accept and return shared domain objects only."""

from typing import Protocol

from src.database.profile import ProfileRepository
from src.shared.models.evidence import EvidenceDraft
from src.shared.models.profile import (
    CareerRecordReference,
    EvidenceLinks,
    ProfileUpdate,
    UserProfile,
)


class ProfileServiceProtocol(Protocol):
    async def get_profile(self) -> UserProfile: ...
    async def update_profile(self, update: ProfileUpdate) -> UserProfile: ...
    async def set_evidence_links(
        self, target: CareerRecordReference, links: EvidenceLinks
    ) -> UserProfile: ...
    async def create_linked_evidence(
        self, target: CareerRecordReference, draft: EvidenceDraft
    ) -> UserProfile: ...


class ProfileService:
    def __init__(self, repository: ProfileRepository, *, user_id: int) -> None:
        self.repository = repository
        self.user_id = user_id

    async def get_profile(self) -> UserProfile:
        return await self.repository.get_profile(self.user_id)

    async def update_profile(self, update: ProfileUpdate) -> UserProfile:
        return await self.repository.update_profile(self.user_id, update)

    async def set_evidence_links(
        self, target: CareerRecordReference, links: EvidenceLinks
    ) -> UserProfile:
        return await self.repository.set_evidence_links(self.user_id, target, links)

    async def create_linked_evidence(
        self, target: CareerRecordReference, draft: EvidenceDraft
    ) -> UserProfile:
        return await self.repository.create_linked_evidence(
            self.user_id, target, draft.new_evidence()
        )
