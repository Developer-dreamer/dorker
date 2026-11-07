"""Present typed match records; database representation belongs to the repository."""

from uuid import UUID

from src.api.exceptions import NotFoundException
from src.api.models import MatchAnalytics, MatchesResponse, MatchResponse, PipelineStatus
from src.api.services.protocols import MatchService as MatchServiceProtocol
from src.database.protocols import MatchRepository
from src.shared.models import JobForAnalytics, SuitabilityTier


def match_response(job: JobForAnalytics) -> MatchResponse:
    match = job.match
    if match is None or match.created_at is None:
        raise ValueError("Repository returned an unpersisted match.")
    analytics = match.job_summary

    def paragraphs(text: str) -> list[str]:
        return [p.strip() for p in text.split("\n\n") if p.strip()]

    return MatchResponse(
        id=match.id,
        job_id=match.job_id,
        suitability_tier=match.suitability_tier,
        pipeline_status=PipelineStatus(match.pipeline_status),
        technical_capability_score=match.technical_capability_score,
        strategic_value_score=match.strategic_value_score,
        analytics=MatchAnalytics(
            summary=analytics.summary,
            pros=paragraphs(analytics.pros),
            cons=paragraphs(analytics.cons),
        )
        if analytics
        else None,
        rejection_reason=match.rejection_reason,
        created_at=match.created_at,
        updated_at=match.updated_at or match.created_at,
    )


class MatchService(MatchServiceProtocol):
    def __init__(self, repository: MatchRepository) -> None:
        self.repository = repository

    async def list_matches(
        self,
        *,
        limit: int,
        offset: int,
        tiers: list[SuitabilityTier] | None,
        pipeline_status: list[PipelineStatus] | None,
    ) -> MatchesResponse:
        jobs, total = await self.repository.get_matches(
            tiers,
            [s.value for s in pipeline_status] if pipeline_status is not None else None,
            limit=limit,
            offset=offset,
        )
        return MatchesResponse(
            matches=[match_response(job) for job in jobs], total=total, limit=limit, offset=offset
        )

    async def list_application_matches(
        self,
        *,
        limit: int,
        offset: int,
        tiers: list[SuitabilityTier] | None,
        pipeline_status: list[PipelineStatus] | None,
    ) -> MatchesResponse:
        jobs, total = await self.repository.get_application_matches(
            tiers,
            [s.value for s in pipeline_status] if pipeline_status is not None else None,
            limit=limit,
            offset=offset,
        )
        return MatchesResponse(
            matches=[match_response(job) for job in jobs], total=total, limit=limit, offset=offset
        )

    async def get_match_by_id(self, match_id: UUID) -> MatchResponse:
        job = await self.repository.get_job_by_match_id(match_id)
        if job is None:
            raise NotFoundException("Match not found.")
        return match_response(job)

    async def get_match_by_job_id(self, job_id: str) -> MatchResponse:
        job = await self.repository.get_latest_match_by_job_id(job_id)
        if job is None:
            raise NotFoundException("Match not found.")
        return match_response(job)

    async def get_counts(
        self, *, pipeline_status: list[PipelineStatus] | None
    ) -> dict[SuitabilityTier, int]:
        counts = await self.repository.get_matches_stats(
            [s.value for s in pipeline_status] if pipeline_status is not None else None
        )
        return {tier: counts.for_tier(tier) for tier in SuitabilityTier}

    async def get_debug(self, match_id: UUID) -> str | None:
        try:
            return await self.repository.get_match_debug(match_id)
        except LookupError as error:
            raise NotFoundException("Match not found.") from error

    async def report(self, match_id: UUID, text: str) -> None:
        try:
            await self.repository.report_match(match_id, text)
        except LookupError as error:
            raise NotFoundException("Match not found.") from error

    async def update_pipeline_status(self, match_id: UUID, status: PipelineStatus) -> None:
        try:
            await self.repository.update_pipeline_status(match_id, status.value)
        except LookupError as error:
            raise NotFoundException("Match not found.") from error
