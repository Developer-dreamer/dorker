"""Domain-only persistence contract. Storage rows and JSON stay in implementations."""

from typing import Protocol

from src.shared.models.evidence import NewEvidence
from src.shared.models.profile import (
    CareerRecordReference,
    EvidenceLinks,
    ProfileUpdate,
    UserProfile,
)


class ProfileRepository(Protocol):
    async def get_profile(self, user_id: int) -> UserProfile: ...
    async def update_profile(self, user_id: int, update: ProfileUpdate) -> UserProfile: ...
    async def set_evidence_links(
        self, user_id: int, target: CareerRecordReference, links: EvidenceLinks
    ) -> UserProfile: ...
    async def create_linked_evidence(
        self, user_id: int, target: CareerRecordReference, evidence: NewEvidence
    ) -> UserProfile: ...
