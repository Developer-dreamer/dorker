import json
from typing import Protocol

from asyncpg import Pool

from .models import JobFactSheet, JobForAnalytics, MatchedJob, RuntimeVersion

# === Protocol definitions ===


class JobRepository(Protocol):
    async def get_matched_jobs(self) -> list[JobForAnalytics]: ...


class JobFactSheetRepository(Protocol):
    async def save_fact_sheet(self, sheet: JobFactSheet) -> None: ...


class MatchRepository(Protocol):
    async def save_match(self, match: MatchedJob) -> None: ...


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


class JobRepositoryPostgres:
    def __init__(self, pool: Pool, runtime_version: RuntimeVersion) -> None:
        self.pool = pool
        self.runtime_version = runtime_version

    async def get_matched_jobs(self) -> list[JobForAnalytics]:
        query = f"""
                SELECT j.id,
                       j.title,
                       j.location,
                       j.description,
                       j.salary_min,
                       j.salary_max,
                       j.salary_currency
                FROM jobs j
                WHERE
                  -- 1. Anti-Joins for version control
                  NOT EXISTS (
                      SELECT 1 FROM jobs_fact_sheets jfs
                      WHERE jfs.job_id = j.id AND ltrim(jfs.version, 'v')::semver >= '{self.runtime_version.version}'::semver
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM matches m
                      WHERE m.job_id = j.id AND ltrim(m.version, 'v')::semver >= '{self.runtime_version.version}'::semver
                  )
                  -- 2. MUST CONTAIN ONE OF THESE (Positive Match)
                  AND j.searchable @@ websearch_to_tsquery('simple',
                      'lang_golang OR python OR lang_csharp OR framework_dotnet OR lang_cpp OR backend OR "software engineer" OR "software developer"'
                  )
                  -- 3. MUST NOT CONTAIN ANY OF THESE (Negative Match)
                  AND NOT j.searchable @@ websearch_to_tsquery('simple',
                      'lead OR principal OR staff OR director OR architect OR manager OR vp OR head OR executive OR frontend OR "front end" OR ui OR ios OR android OR flutter OR "react native" OR php OR wordpress OR magento OR "ruby on rails" OR "network engineer" OR angular OR qa'
                  )
                  -- 4. Explicitly filter Title seniority to be extra safe (optional but recommended)
                  AND j.title !~* '\\m(lead|principal|staff|director|architect|manager|vp|head|executive|qa)\\M'
                  -- 5. Match Target Stack Extensions
                  AND (
                      j.searchable @@ to_tsquery('simple', 'python | lang_csharp | framework_dotnet | lang_cpp | lang_golang')
                      OR j.title ~ '\\mGo\\M'
                      OR j.description ~ '\\mGo\\M(\\s*(1\\.[0-9]+|developer|engineer|backend|microservices|concurrency|routine|routines|channel|channels|stack|code|programming|,|/|\band\b|\bor\b))'
                      OR j.description ~ '(?i)\b(experience with|knowledge of|proficien\\w+ in|proficient with|hands-on with|strong)\\s+Go\b'
                  )
                  -- 6. Safe Location Matching
                  AND (
                      j.location ILIKE ANY (ARRAY['%Ukraine%', '%Europe%', '%Remote%', '%EMEA%', '%Worldwide%', '%Global%', '%віддалено%', '%Київ%'])
                      OR j.location ~* '\\mKy(iv|ev)\\M'
                  )
                  AND COALESCE(j.posted_at, j.fetched_at) >= NOW() - INTERVAL '1 week'
                  AND j.is_normalized = TRUE
                  AND j.deleted_at IS NULL
                  AND (j.description IS NOT NULL AND TRIM(j.description) != '')
                ORDER BY
                    ts_rank_cd(j.searchable, websearch_to_tsquery('simple', 'lang_golang OR python OR lang_csharp OR framework_dotnet OR lang_cpp OR backend OR "software engineer" OR "software developer"')) DESC,
                    COALESCE(j.posted_at, j.fetched_at) DESC
                LIMIT 10;
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
            **sheet.model_dump(exclude={"debug"}),
            "debug": json.dumps(sheet.debug),
            "model": self.runtime_version.model,
            "version": self.runtime_version.version,
            "iteration": self.runtime_version.iteration,
        }

        async with self.pool.acquire() as conn:
            await conn.execute(query, *(payload[col] for col in columns))


class MatchRepositoryPostgres:
    def __init__(self, pool: Pool, runtime_version: RuntimeVersion) -> None:
        self.pool = pool
        self.runtime_version = runtime_version

    async def save_match(self, match: MatchedJob) -> None:
        columns = (
            "id",
            "job_id",
            "is_technical",
            "suitability_tier",
            "model",
            "version",
            "iteration",
        )

        query = f"""
                    INSERT INTO matches ({", ".join(columns)})
                    VALUES ({", ".join(f"${i + 1}" for i in range(len(columns)))});
                """

        payload = {
            "id": match.id,
            "job_id": match.job_id,
            "is_technical": True,
            "suitability_tier": match.suitability_tier,
            "model": self.runtime_version.model,
            "version": self.runtime_version.version,
            "iteration": self.runtime_version.iteration,
        }

        async with self.pool.acquire() as conn:
            await conn.execute(query, *(payload[col] for col in columns))
