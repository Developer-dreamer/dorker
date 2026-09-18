from typing import Protocol

from asyncpg import Pool

from .models import JobFactSheet, JobForAnalytics, RuntimeVersion

# === Protocol definitions ===


class JobRepository(Protocol):
    async def get_matched_jobs(self) -> list[JobForAnalytics]: ...


class JobFactSheetRepository(Protocol):
    async def save_fact_sheet(self, sheet: JobFactSheet) -> None: ...


# ===


class JobRepositoryTest:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    async def get_matched_jobs(self) -> list[JobForAnalytics]:
        query = """
                SELECT j.id,
                       j.title,
                       j.location,
                       j.description,
                       j.salary_min,
                       j.salary_max,
                       j.salary_currency
                FROM jobs j
                WHERE EXISTS (
                    SELECT 1
                    FROM jobs_fact_sheets jfs
                    WHERE jfs.job_id = j.id AND jfs.model = 'golden_set_manual'
                )
                ORDER BY j.posted_at DESC;
                """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query)
            return [JobForAnalytics(**dict(row)) for row in rows]


class JobFactSheetRepositoryPostgres:
    def __init__(self, pool: Pool, ver: RuntimeVersion) -> None:
        self.pool = pool
        self.runtime_version = ver

    async def save_fact_sheet(self, sheet: JobFactSheet) -> None:
        columns = (
            "id",
            "job_id",
            "job_family",
            "geographic_scope",
            "workplace_type",
            "office_location_city",
            "target_jurisdiction",
            "region",
            "min_years_experience",
            "is_experience_flexible",
            "primary_backend_languages",
            "secondary_tools",
            "is_legacy_maintenance",
            "is_pure_network_or_systems",
            "has_mandatory_travel",
            "has_uncompensated_oncall",
            "detected_operational_cues",
            "model",
            "version",
            "iteration",
        )

        query = f"""
                    INSERT INTO jobs_fact_sheets ({", ".join(columns)})
                    VALUES ({", ".join(f"${i + 1}" for i in range(len(columns)))});
                """

        payload = {
            **sheet.model_dump(),
            "model": self.runtime_version.model,
            "version": self.runtime_version.version,
            "iteration": self.runtime_version.iteration,
        }

        async with self.pool.acquire() as conn:
            await conn.execute(query, *(payload[col] for col in columns))

        print("EXECUTED")
