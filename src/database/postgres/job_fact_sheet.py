import json

from asyncpg import Pool

from src.database.protocols import JobFactSheetRepository
from src.shared.models import JobFactSheet, Region, RuntimeVersion


class JobFactSheetRepositoryPostgres(JobFactSheetRepository):
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
            "min_years_experience",
            "is_experience_flexible",
            "primary_backend_languages",
            "secondary_tools",
            "is_legacy_maintenance",
            "is_pure_network_or_systems",
            "has_mandatory_travel",
            "has_uncompensated_oncall",
            "region",
            "debug",
            "model",
            "version",
            "iteration",
        )

        query = f"""
                    INSERT INTO jobs_fact_sheets ({", ".join(columns)})
                    VALUES ({", ".join(f"${i + 1}" for i in range(len(columns)))});
                """

        payload = {
            **sheet.model_dump(exclude={"region", "debug"}),
            "region": sheet.region if sheet.region != Region.UNKNOWN else None,
            "debug": json.dumps(sheet.debug),
            "model": self.runtime_version.model,
            "version": self.runtime_version.version,
            "iteration": self.runtime_version.iteration,
        }

        async with self.pool.acquire() as conn:
            await conn.execute(query, *(payload[col] for col in columns))
