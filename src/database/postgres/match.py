import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from asyncpg import Pool

from src.database.postgres.job import job_from_record
from src.database.protocols import MatchRepository as MatchRepositoryProtocol
from src.shared.models import (
    JobAnalytics,
    JobForAnalytics,
    MatchCounts,
    MatchedJob,
    RuntimeVersion,
    SuitabilityTier,
)


def analytics_from_storage(value: Any) -> JobAnalytics | None:
    """Read structured JSON and pre-JSON summaries without rewriting historical rows."""
    if value is None or isinstance(value, JobAnalytics):
        return value
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict) and isinstance(decoded.get("summary"), str):
            return JobAnalytics.model_validate(decoded)
        return JobAnalytics(summary=value, pros="", cons="")
    return JobAnalytics.model_validate(value)


def debug_from_storage(value: Any) -> str | None:
    return value if value is None or isinstance(value, str) else json.dumps(value)


def matched_job_from_record(row: Mapping[str, Any]) -> MatchedJob:
    return MatchedJob(
        id=row["match_id"],
        job_id=row["match_job_id"],
        technical_capability_score=row["technical_capability_score"],
        strategic_value_score=row["strategic_value_score"],
        suitability_tier=row["suitability_tier"],
        pipeline_status=row["pipeline_status"],
        job_summary=analytics_from_storage(row["job_summary"]),
        rejection_reason=row["rejection_reason"],
        debug=debug_from_storage(row["match_debug"]),
        created_at=row["match_created_at"],
        updated_at=row.get("match_updated_at") or row["match_created_at"],
    )


def matched_job_from_join(row: Mapping[str, Any]) -> JobForAnalytics:
    job = job_from_record(row)
    job.match = matched_job_from_record(row)
    return job


# Every joined read hydrates both domain objects, including the real database timestamps.
_MATCH_COLUMNS = """j.*, c.name AS company_name,
    m.id AS match_id, m.job_id AS match_job_id, m.technical_capability_score,
    m.strategic_value_score, m.suitability_tier, m.pipeline_status, m.job_summary,
    m.rejection_reason, m.debug AS match_debug, m.created_at AS match_created_at,
    m.updated_at AS match_updated_at"""

# Rank before applying user filters: older matches must never replace a filtered-out latest one.
_LATEST = """WITH ranked AS (
    SELECT m.*, row_number() OVER (
        PARTITION BY m.job_id ORDER BY m.iteration DESC, m.created_at DESC, m.id DESC
    ) AS rn
    FROM matches m JOIN jobs j ON j.id=m.job_id
    WHERE ltrim(m.version, 'v')::semver >= $1::semver
      AND j.deleted_at IS NULL
), latest AS (SELECT * FROM ranked WHERE rn=1)
"""
_FILTERS = """WHERE ($2::text[] IS NULL OR m.suitability_tier=ANY($2))
    AND ($3::text[] IS NULL OR m.pipeline_status::text=ANY($3))"""
_APPLICATION_FILTERS = (
    _FILTERS
    + """
    AND (
        (m.pipeline_status='PENDING' AND EXISTS (
            SELECT 1 FROM applications a WHERE a.match_id=m.id AND a.deleted_at IS NULL
        ))
        OR m.pipeline_status IN (
            'APPLIED', 'INTERVIEWING', 'OFFER',
            'GHOSTED', 'REJECTED_EARLY', 'REJECTED_LATE', 'DECLINED'
        )
    )"""
)


class MatchRepositoryPostgres(MatchRepositoryProtocol):
    def __init__(self, pool: Pool, runtime_version: RuntimeVersion) -> None:
        self.pool = pool
        self.runtime_version = runtime_version

    @property
    def _version(self) -> str:
        return self.runtime_version.min_version or self.runtime_version.version

    async def save_match(self, match: MatchedJob) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO matches
                   (id,job_id,technical_capability_score,strategic_value_score,suitability_tier,
                    job_summary,rejection_reason,debug,model,version,iteration)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",
                match.id,
                match.job_id,
                match.technical_capability_score,
                match.strategic_value_score,
                match.suitability_tier.value,
                match.job_summary.model_dump_json() if match.job_summary is not None else None,
                match.rejection_reason,
                match.debug,
                self.runtime_version.model,
                self.runtime_version.version,
                self.runtime_version.iteration,
            )

    async def get_pending_summary_match_ids(self, limit: int = 100) -> list[UUID]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT m.id FROM matches m JOIN jobs j ON j.id=m.job_id
                   WHERE m.suitability_tier <> 'REJECTED'
                     AND (m.job_summary IS NULL OR btrim(m.job_summary)='')
                     AND ltrim(m.version, 'v')::semver >= $1::semver
                     AND j.deleted_at IS NULL ORDER BY m.id LIMIT $2""",
                self._version,
                limit,
            )
        return [row["id"] for row in rows]

    async def report_match(self, match_id: UUID, message: str) -> None:
        message = message.strip()
        if not message:
            raise ValueError("Report message cannot be empty.")
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """UPDATE matches m
                   SET reported = TRUE, report_message = $1, updated_at = CURRENT_TIMESTAMP
                   WHERE m.id = $2 AND EXISTS
                   (SELECT 1 FROM jobs j WHERE j.id=m.job_id AND j.deleted_at IS NULL)""",
                message,
                match_id,
            )
        if result == "UPDATE 0":
            raise LookupError("Match not found.")

    async def update_summary(self, match_id: UUID, summary: JobAnalytics) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE matches SET job_summary=$2 WHERE id=$1", match_id, summary.model_dump_json()
            )

    async def get_matches_stats(self, pipeline_statuses: list[str] | None) -> MatchCounts:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                _LATEST
                + """SELECT m.suitability_tier, count(*) AS total FROM latest m
                    WHERE ($2::text[] IS NULL OR m.pipeline_status::text=ANY($2))
                    GROUP BY m.suitability_tier""",
                self._version,
                pipeline_statuses,
            )
        return MatchCounts.model_validate(
            {row["suitability_tier"].lower(): row["total"] for row in rows}
        )

    async def update_pipeline_status(self, match_id: UUID, status: str) -> None:
        async with self.pool.acquire() as conn:
            found = await conn.fetchval(
                """UPDATE matches m SET pipeline_status=$2, updated_at=CURRENT_TIMESTAMP
                   WHERE m.id=$1 AND EXISTS
                   (SELECT 1 FROM jobs j WHERE j.id=m.job_id AND j.deleted_at IS NULL)
                   RETURNING m.id""",
                match_id,
                status,
            )
        if found is None:
            raise LookupError("Match not found.")

    async def get_match_debug(self, match_id: UUID) -> str | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT m.debug FROM matches m JOIN jobs j ON j.id=m.job_id
                   WHERE m.id=$1 AND j.deleted_at IS NULL""",
                match_id,
            )
        if row is None:
            raise LookupError("Match not found.")
        return debug_from_storage(row["debug"])

    async def get_job_by_match_id(self, match_id: UUID) -> JobForAnalytics | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT "
                + _MATCH_COLUMNS
                + """ FROM matches m JOIN jobs j ON j.id=m.job_id
                LEFT JOIN companies c ON c.id=j.company_id
                WHERE m.id=$1 AND j.deleted_at IS NULL""",
                match_id,
            )
        return matched_job_from_join(row) if row is not None else None

    async def get_latest_match_by_job_id(self, job_id: str) -> JobForAnalytics | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT "
                + _MATCH_COLUMNS
                + """ FROM matches m JOIN jobs j ON j.id=m.job_id
                LEFT JOIN companies c ON c.id=j.company_id
                WHERE m.job_id=$1 AND j.deleted_at IS NULL
                ORDER BY m.iteration DESC, m.created_at DESC, m.id DESC
                LIMIT 1""",
                job_id,
            )
        return matched_job_from_join(row) if row is not None else None

    async def get_matched_job(
        self, tiers: list[SuitabilityTier], offset: int = 0
    ) -> JobForAnalytics | None:
        # Retain the legacy single-job navigation's tier/company/date priority.
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT "
                + _MATCH_COLUMNS
                + """ FROM matches m JOIN jobs j ON j.id=m.job_id
                LEFT JOIN companies c ON c.id=j.company_id
                WHERE j.deleted_at IS NULL AND m.pipeline_status='PENDING'
                  AND ltrim(m.version, 'v')::semver >= $1::semver
                  AND m.suitability_tier=ANY($2)
                ORDER BY CASE m.suitability_tier WHEN 'SUITABLE' THEN 1 WHEN 'RUNWAY' THEN 2
                    WHEN 'STRETCH' THEN 3 ELSE 4 END, c.tier,
                    COALESCE(j.posted_at, j.fetched_at) DESC
                LIMIT 1 OFFSET $3""",
                self._version,
                [t.value for t in tiers],
                offset,
            )
        return matched_job_from_join(row) if row is not None else None

    async def get_matches(
        self,
        tiers: list[SuitabilityTier] | None,
        statuses: list[str] | None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[JobForAnalytics], int]:
        args = (
            self._version,
            [t.value for t in tiers] if tiers is not None else None,
            statuses,
        )
        async with self.pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                total = await conn.fetchval(
                    _LATEST + "SELECT count(*) FROM latest m " + _FILTERS, *args
                )
                rows = await conn.fetch(
                    _LATEST
                    + "SELECT "
                    + _MATCH_COLUMNS
                    + """ FROM latest m
                    JOIN jobs j ON j.id=m.job_id LEFT JOIN companies c ON c.id=j.company_id
                    """
                    + _FILTERS
                    + " ORDER BY m.created_at DESC, m.id DESC LIMIT $4 OFFSET $5",
                    *args,
                    limit,
                    offset,
                )
        return [matched_job_from_join(row) for row in rows], int(total)

    async def get_application_matches(
        self,
        tiers: list[SuitabilityTier] | None,
        statuses: list[str] | None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[JobForAnalytics], int]:
        args = (
            self._version,
            [t.value for t in tiers] if tiers is not None else None,
            statuses,
        )
        async with self.pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                total = await conn.fetchval(
                    _LATEST + "SELECT count(*) FROM latest m " + _APPLICATION_FILTERS, *args
                )
                rows = await conn.fetch(
                    _LATEST
                    + "SELECT "
                    + _MATCH_COLUMNS
                    + """ FROM latest m
                    JOIN jobs j ON j.id=m.job_id LEFT JOIN companies c ON c.id=j.company_id
                    """
                    + _APPLICATION_FILTERS
                    + " ORDER BY m.updated_at DESC, m.created_at DESC, m.id DESC LIMIT $4 OFFSET $5",
                    *args,
                    limit,
                    offset,
                )
        return [matched_job_from_join(row) for row in rows], int(total)

    async def append_question(self, match_id: UUID, question: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """UPDATE jobs SET questions=array_append(COALESCE(questions,'{}'::text[]),$2)
                   WHERE id=(SELECT job_id FROM matches WHERE id=$1)""",
                match_id,
                question,
            )
