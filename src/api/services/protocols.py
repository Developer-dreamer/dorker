"""Service contracts consumed by HTTP handlers; persistence and providers stay behind them."""

from collections.abc import AsyncGenerator
from typing import Protocol
from uuid import UUID

from src.api.models import (
    ApplicationPatchInput,
    ApplicationResponse,
    ApplicationsResponse,
    DiscussionReplyEvent,
    DiscussionReplyResponse,
    DiscussionResponse,
    GenerationInput,
    JobResponse,
    JobsResponse,
    MatchesResponse,
    MatchResponse,
    PipelineStatus,
    ScrapeJobResponse,
)
from src.shared.models import Job
from src.shared.models.match import SuitabilityTier


class JobScraper(Protocol):
    async def scrape(self, url: str) -> Job: ...


class JobService(Protocol):
    async def list_jobs(self, *, limit: int, offset: int) -> JobsResponse:
        """Return a page and the total number of available jobs, independent of pagination."""
        ...

    async def get_job_by_id(self, job_id: str) -> JobResponse:
        """Return a job or raise NotFoundException if it does not exist."""
        ...

    async def scrape_job(self, url: str) -> ScrapeJobResponse:
        """Scrape a live job URL, persist with UUID7 and company_id=-1, classify, and return."""
        ...


class MatchService(Protocol):
    """Record-specific operations raise NotFoundException for missing matches."""

    async def list_matches(
        self,
        *,
        limit: int,
        offset: int,
        tiers: list[SuitabilityTier] | None,
        pipeline_status: list[PipelineStatus] | None,
    ) -> MatchesResponse:
        """Return matches and the filtered total, independently of pagination.

        None filters mean unrestricted selection.
        """
        ...

    async def get_match_by_id(self, match_id: UUID) -> MatchResponse: ...

    async def get_match_by_job_id(self, job_id: str) -> MatchResponse: ...

    async def get_counts(
        self, *, pipeline_status: list[PipelineStatus] | None
    ) -> dict[SuitabilityTier, int]:
        """Count across tiers, optionally restricted to the supplied statuses."""
        ...

    async def get_debug(self, match_id: UUID) -> str | None: ...

    async def report(self, match_id: UUID, text: str) -> None: ...

    async def update_pipeline_status(self, match_id: UUID, status: PipelineStatus) -> None: ...


class DiscussionService(Protocol):
    """One conversation per match, already scoped to the local user/chat identity.

    The implementation owns initialization, persistence, concurrency, and projection
    of visible history. create_app wraps the injected implementation once with
    SerializedDiscussionService: start/send/stream/finish wait for the previous
    mutation of the same discussion, while reads remain unlocked. Callers outside
    that app must reuse the same coordinator (or enforce coordination themselves).
    No identity or internal repository data crosses this boundary.
    All methods raise NotFoundException for an unknown match. Invalid lifecycle
    operations raise DiscussionConflictException. Reply failures with known user-message
    persistence raise DiscussionReplyException; unexpected exceptions remain server errors.
    """

    async def get_discussion(self, match_id: UUID) -> DiscussionResponse:
        """Return visible history, including after finish; absent discussions are empty/not_started."""
        ...

    async def start(self, match_id: UUID) -> None:
        """Initialize context once. An already open or finished discussion conflicts."""
        ...

    async def send_message(self, match_id: UUID, text: str) -> DiscussionReplyResponse:
        """Require open state, persist the user message, then persist a complete reply."""
        ...

    async def stream_message(
        self, match_id: UUID, text: str
    ) -> AsyncGenerator[DiscussionReplyEvent, None]:
        """Await preflight before headers; iterate the returned generator for the reply.

        Check match existence and open state during the awaited call. The generator
        yields accepted after saving the user, delta for provisional text, and done
        after saving the complete assistant reply. Acquire streaming resources inside
        the generator and release them in finally when it is closed or cancelled.
        """
        ...


class ApplicationService(Protocol):
    """Own persistence, candidate-context selection, generation, and write coordination.

    All methods raise NotFoundException for unknown matches/applications. Known
    generation failures raise ApplicationGenerationException; unexpected failures
    remain server errors. Successful writes return the persisted draft. Failed
    regeneration preserves existing content; failed creation must not expose an
    incomplete draft as a successfully generated application.
    """

    async def create_application(
        self, match_id: UUID, request: GenerationInput
    ) -> ApplicationResponse:
        """Create a separate draft with its assigned integer ID; never mutate the source job."""
        ...

    async def list_applications(
        self, match_id: UUID, *, limit: int, offset: int
    ) -> ApplicationsResponse:
        """Return drafts ordered by created_at DESC, id DESC, and the unpaginated total.

        An existing match with no drafts returns an empty page; an unknown match raises.
        """
        ...

    async def get_application(self, application_id: int) -> ApplicationResponse: ...

    async def update_application(
        self, application_id: int, patch: ApplicationPatchInput
    ) -> ApplicationResponse:
        """Preserve omitted fields; null clears the letter, and questions replace the full list."""
        ...

    async def regenerate_application(
        self, application_id: int, request: GenerationInput
    ) -> ApplicationResponse:
        """Keep the ID and replace only selected sections, including their manual edits.

        Answer generation replaces the Q&A list using the explicit supplied questions.
        Cover-letter-only generation preserves Q&A, and answers-only generation
        preserves the cover letter. Both selectors replace both sections.
        """
        ...
