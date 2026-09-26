from typing import List

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
            "strategic_reason",
            "rejection_reason",
            "debug",
            "is_technical",
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
            "is_technical": True,
            "model": self.runtime_version.model,
            "version": self.runtime_version.version,
            "iteration": self.runtime_version.iteration,
        }

        async with self.pool.acquire() as conn:
            await conn.execute(query, *(payload[col] for col in columns))

    async def get_matches(self, tier: SuitabilityTier, limit: int = 1) -> List[JobForAnalytics]:
        query = rf"""
                WITH RankedMatches AS (
                    SELECT m.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY m.job_id
                               ORDER BY m.iteration DESC, m.created_at DESC
                           ) as rn
                    FROM matches m
                    WHERE NOT EXISTS(
                            SELECT 1 FROM applications
                            WHERE match_id = m.id
                        )
                      AND m.pipeline_status = 'PENDING'
                      AND m.model = '{self.runtime_version.model}'
                      AND ltrim(m.version, 'v')::semver >= '{self.runtime_version.version}'::semver
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
                       rm.suitability_tier
                FROM RankedMatches rm
                JOIN jobs j ON j.id = rm.job_id
                WHERE rm.rn = 1
                  AND j.deleted_at IS NULL
                  AND rm.suitability_tier = '{tier.value}'
                ORDER BY RANDOM()
                LIMIT {limit};
                """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query)
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
                }
                job.match = MatchedJob.model_validate(match_data)

                jobs.append(job)

            return jobs
