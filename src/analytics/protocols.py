from abc import ABC, abstractmethod
from typing import List, Protocol, Tuple
from uuid import UUID

from src.shared.models import (
    ApplicationGeneratedResponse,
    ApplicationPacket,
    JobAnalytics,
    JobForAnalytics,
    Material,
    RequestedGeneration,
    SuitabilityTier,
)


class DataIntegrityException(ValueError):
    def __init__(self, value: object) -> None:
        super().__init__(f"Invalid or missing profile block: {value}")


class ProfileRepository(ABC):
    """A readable profile snapshot, independent of its storage format.

    Selection policy belongs to classifiers. Persistence and conversation state
    belong to their existing repositories and services.
    """

    @abstractmethod
    def get_cv(self) -> Material: ...

    @abstractmethod
    def get_preferences(self) -> Material: ...

    @abstractmethod
    def get_experience(self) -> list[Material]: ...

    @abstractmethod
    def get_projects(self) -> list[Material]: ...

    @abstractmethod
    def get_skills(self) -> Material: ...

    @abstractmethod
    def get_edu(self) -> Material: ...

    @abstractmethod
    def get_coursework(self) -> list[Material]: ...

    @abstractmethod
    def get_dossier(self) -> list[Material]: ...

    @abstractmethod
    def get_cover_letter_examples(self) -> list[Material]: ...

    @abstractmethod
    def get_materials(self) -> list[Material]: ...

    @abstractmethod
    def get_material(self, material_id: str) -> Material: ...

    @abstractmethod
    def full_dump(self) -> str:
        """Render the complete profile for profile-to-job classification."""
        ...


class MatchingProvider(Protocol):
    async def report_match(self, match_id: UUID, message: str) -> None: ...
    async def get_matching_count(self) -> dict[str, int]: ...
    async def get_matched_job(
        self, tiers: List[SuitabilityTier], offset: int = 0
    ) -> JobForAnalytics | None: ...
    async def append_question(self, match_id: UUID, question: str) -> None: ...
    async def get_job_by_match_id(self, match_id: UUID) -> JobForAnalytics | None: ...
    async def get_match_debug(self, match_id: UUID) -> str | None: ...
    async def update_pipeline_status(self, match_id: UUID, status: str) -> None: ...
    async def generate_application_for_match(
        self,
        request: RequestedGeneration,
        job: JobForAnalytics,
        materials: list[Material] | None = None,
        selected_packet: ApplicationPacket | None = None,
    ) -> ApplicationGeneratedResponse: ...


class Classifier[T](Protocol):
    async def classify(self, job: JobForAnalytics, profile: ProfileRepository) -> T: ...


class SLM(Protocol):
    def load_model(self) -> None: ...
    def generate[O](self, inp: JobForAnalytics, schema: type[O]) -> O: ...


class ApplicationGenerator[T](Protocol):
    async def generate_sync(self, request: RequestedGeneration, job: JobForAnalytics) -> T: ...
    async def queue_generation(self, jobs: List[JobForAnalytics]) -> None: ...
    async def harvest_results(self) -> List[Tuple[UUID, T]]: ...


class SummaryGenerator(Protocol):
    async def generate(self, job: JobForAnalytics) -> JobAnalytics: ...
