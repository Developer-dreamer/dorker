from asyncpg import Pool

from src.analytics.models import MatchedJob, RuntimeVersion
from src.database.protocols import MatchRepository as MatchRepositoryProtocol


class MatchRepository(MatchRepositoryProtocol):
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
