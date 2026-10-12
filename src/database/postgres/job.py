from datetime import timedelta

from asyncpg import Pool

from src.database.protocols import JobRepository
from src.shared.models import JobForAnalytics, RuntimeVersion


class JobRepositoryPostgres(JobRepository):
    def __init__(self, pool: Pool, runtime_version: RuntimeVersion) -> None:
        self.pool = pool
        self.runtime_version = runtime_version

    async def get_matched_jobs(
        self, time_interval: timedelta = timedelta(weeks=1), limit: int = 1000
    ) -> list[JobForAnalytics]:
        query = r"""
                SELECT j.id,
                       j.title,
                       j.location,
                       j.description,
                       j.questions,
                       j.salary_min,
                       j.salary_max,
                       j.salary_currency
                FROM jobs j
                WHERE
                -- 1. Anti-Joins for version control
                    NOT EXISTS (SELECT 1
                                FROM matches m
                                WHERE m.job_id = j.id
                                  AND ltrim(m.version, 'v')::semver >= $1::semver)

                -- 2. Exclude high-level and unwanted roles by TITLE only.
                -- Description mentions of colleagues or migration stacks are not blockers.
                  AND replace(j.title, '_', ' ') !~* '\m(lead|principal|staff|director|architect|manager|vp|head|executive)\M'
                  AND replace(j.title, '_', ' ') !~* '\m(front[ -]?end|ui|ios|android|flutter|react[ -]+native|php|wordpress|magento|ruby[ -]+on[ -]+rails|network[ -]+engineer|angular|qa|sdet|support|sales|solutions?|test|testing|quality)\M'

                -- 3. Require role relevance independently of a language mention.
                  AND (
                    replace(j.title, '_', ' ') ~* '\m(software|ai|ml|machine[ -]+learning|platform|infrastructure|cloud|devops|site[ -]+reliability|python|golang|go)[ -]+(engineer|developer|engineering[ -]+intern(ship)?|development[ -]+intern(ship)?)\M'
                    OR replace(j.title, '_', ' ') ~* '\m(back[ -]?end|SRE)\M'
                    OR (
                        replace(j.title, '_', ' ') ~* '\m(engineer|developer)\M'
                        AND j.searchable @@ to_tsquery('simple',
                            'backend | microservices | distributed <-> systems')
                    )
                  )

                -- 4. Require a target-stack signal as well as a relevant role.
                  AND (
                    j.searchable @@ to_tsquery('simple'
                    , 'python | lang_csharp | framework_dotnet | lang_cpp | lang_golang')
                   OR j.title ~ '\mGo\M'
                   OR j.description ~ '\mGo\M(\s*(1\.[0-9]+|developer|engineer|backend|microservices|concurrency|routine|routines|channel|channels|stack|code|programming|,|/|\mand\M|\mor\M))'
                   OR j.description ~ '(?i)\m(experience with|knowledge of|proficien\w+ in|proficient with|hands-on with|strong)\s+Go\M'
                   OR (j.title || ' ' || j.description) ~* '(\m(python|golang)\M|\mC#|\mC\+\+|\.NET\M|\mASP\.NET\M|\mdotnet\M)'
                    )

                -- 5. Location is a candidate signal; Jev resolves hiring restrictions.
                  AND (
                    j.location ILIKE ANY (ARRAY['%Ukraine%'
                    , '%Europe%'
                    , '%Remote%'
                    , '%EMEA%'
                    , '%Worldwide%'
                    , '%Global%'
                    , '%віддалено%'
                    , '%Київ%'])
                   OR j.location ~* '\mKy(iv|ev)\M'
                    )

                  AND COALESCE(j.posted_at, j.fetched_at) >= (NOW() - ($2::interval))
                  AND j.is_normalized = TRUE
                  AND j.deleted_at IS NULL
                  AND (TRIM (j.description) != '')
                ORDER BY
                    ts_rank_cd(j.searchable, websearch_to_tsquery('simple',
                        'lang_golang OR python OR lang_csharp OR framework_dotnet OR lang_cpp OR backend OR "software engineer" OR "software developer"')) DESC,
                    COALESCE(j.posted_at, j.fetched_at) DESC,
                    j.id
                LIMIT $3;
                """

        version_filter = self.runtime_version.min_version or self.runtime_version.version
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, version_filter, time_interval, limit)
            return [JobForAnalytics(**dict(row)) for row in rows]


class JobRepositoryTest(JobRepository):
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    async def get_matched_jobs(
        self, time_interval: timedelta = timedelta(weeks=1), limit: int = 1000
    ) -> list[JobForAnalytics]:
        query = """
                SELECT j.id,
                       j.title,
                       j.location,
                       j.description,
                       j.questions,
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
