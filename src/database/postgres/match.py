from typing import List
from uuid import UUID

from asyncpg import Pool

from src.database.protocols import MatchRepository as MatchRepositoryProtocol
from src.shared.models import JobForAnalytics, MatchedJob, RuntimeVersion, SuitabilityTier


class MatchRepositoryPostgres(MatchRepositoryProtocol):
    def __init__(self, pool: Pool, runtime_version: RuntimeVersion) -> None:
        self.pool = pool
        self.runtime_version = runtime_version

    async def save_match(self, match: MatchedJob) -> None:
        columns = (
            "id",
            "job_id",
            "technical_capability_score",
            "strategic_value_score",
            "suitability_tier",
            "job_summary",
            "rejection_reason",
            "debug",
            "model",
            "version",
            "iteration",
        )

        query = f"""
                    INSERT INTO matches ({", ".join(columns)})
                    VALUES ({", ".join(f"${i + 1}" for i in range(len(columns)))});
                """

        payload = {
            **match.model_dump(),
            "model": self.runtime_version.model,
            "version": self.runtime_version.version,
            "iteration": self.runtime_version.iteration,
        }

        async with self.pool.acquire() as conn:
            await conn.execute(query, *(payload[col] for col in columns))

    async def update_summary(self, match_id: UUID, summary: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE matches SET job_summary = $1 WHERE id = $2", summary, match_id
            )

    async def get_matches_stats(self) -> dict[str, int]:
        query = """
                SELECT m.suitability_tier, COUNT(*) FROM matches m
                WHERE ltrim(m.version, 'v')::semver >= $1::semver
                    AND m.pipeline_status = 'PENDING'
                GROUP BY m.suitability_tier;              
                """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, self.runtime_version.version)
            return dict(rows)

    async def update_pipeline_status(self, match_id: UUID, status: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE matches SET pipeline_status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
                status,
                match_id,
            )

    async def get_job_by_match_id(self, match_id: UUID) -> JobForAnalytics | None:
        query = """
                SELECT j.id AS id,
                       j.title,
                       j.location,
                       c.name AS company,
                       j.url,
                       j.description,
                       j.salary_min,
                       j.salary_max,
                       j.salary_currency,
                       m.id AS match_id,
                       m.job_id,
                       m.technical_capability_score,
                       m.strategic_value_score,
                       m.suitability_tier,
                       m.job_summary
                FROM matches m
                         JOIN jobs j ON j.id = m.job_id
                         LEFT JOIN companies c ON j.company_id = c.id
                WHERE m.id = $1
                """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, match_id)
            if not row:
                return None
            data = dict(row)
            job = JobForAnalytics.model_validate(data)
            match_data = {
                "id": data["match_id"],
                "job_id": data["job_id"],
                "technical_capability_score": data["technical_capability_score"],
                "strategic_value_score": data["strategic_value_score"],
                "suitability_tier": data["suitability_tier"],
                "job_summary": data["job_summary"],
            }
            job.match = MatchedJob.model_validate(match_data)
            return job

    async def get_matched_job(
        self, tiers: List[SuitabilityTier], offset: int = 0
    ) -> JobForAnalytics | None:
        query = """
                SELECT j.id   AS id,
                       j.title,
                       j.location,
                       c.name AS company,
                       j.url,
                       j.description,
                       j.salary_min,
                       j.salary_max,
                       j.salary_currency,
                       m.id   AS match_id,
                       m.job_id,
                       m.technical_capability_score,
                       m.strategic_value_score,
                       m.suitability_tier,
                       m.job_summary
                FROM matches m
                         JOIN jobs j ON j.id = m.job_id
                         LEFT JOIN companies c ON j.company_id = c.id
                WHERE j.deleted_at IS NULL
                  AND m.pipeline_status = 'PENDING'
                  AND ltrim(m.version, 'v')::semver >= $1::semver
                  AND m.suitability_tier = ANY ($2)
                ORDER BY CASE m.suitability_tier
                        WHEN 'SUITABLE' THEN 1
                        WHEN 'RUNWAY' THEN 2
                        WHEN 'STRETCH' THEN 3
                        ELSE 4
                    END ASC,
                    c.tier ASC,
                    COALESCE(j.posted_at, j.fetched_at) DESC
                LIMIT 1
                OFFSET $3;
                """
        tier_values = [t.value for t in tiers]

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, self.runtime_version.version, tier_values, offset)
            if not row:
                return None

            data = dict(row)

            # 1. Instantiate the Job
            job = JobForAnalytics.model_validate(data)

            # 2. Instantiate the Match (map match_id to id)
            match_data = {
                "id": data["match_id"],
                "job_id": data["job_id"],
                "technical_capability_score": data["technical_capability_score"],
                "strategic_value_score": data["strategic_value_score"],
                "suitability_tier": data["suitability_tier"],
                "job_summary": data["job_summary"],
            }
            job.match = MatchedJob.model_validate(match_data)

        return job

    async def get_matches(self, tier: SuitabilityTier, limit: int = 1000) -> List[JobForAnalytics]:
        query = r"""
                WITH RankedMatches AS (
                    SELECT m.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY m.job_id
                               ORDER BY m.iteration DESC, m.created_at DESC
                           ) as rn
                    FROM matches m
                    WHERE m.model = $1
                      AND ltrim(m.version, 'v')::semver >= $2::semver
                )
                SELECT j.id AS id,
                       j.title,
                       j.location,
                       j.description,
                       j.salary_min,
                       j.salary_max,
                       j.salary_currency,
                       rm.id AS match_id,
                       rm.job_id,
                       rm.technical_capability_score,
                       rm.strategic_value_score,
                       rm.suitability_tier,
                       rm.job_summary
                FROM RankedMatches rm
                JOIN jobs j ON j.id = rm.job_id
                WHERE rm.rn = 1
                  AND j.deleted_at IS NULL
                  AND rm.suitability_tier = $3
                LIMIT $4;
                """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                query, self.runtime_version.model, self.runtime_version.version, tier.value, limit
            )
            jobs: List[JobForAnalytics] = []

            for row in rows:
                data = dict(row)

                # 1. Instantiate the Job
                job = JobForAnalytics.model_validate(data)

                # 2. Instantiate the Match (map match_id to id)
                match_data = {
                    "id": data["match_id"],
                    "job_id": data["job_id"],
                    "technical_capability_score": data["technical_capability_score"],
                    "strategic_value_score": data["strategic_value_score"],
                    "suitability_tier": data["suitability_tier"],
                    "job_summary": data["job_summary"],
                }
                job.match = MatchedJob.model_validate(match_data)

                jobs.append(job)

            return jobs
