from datetime import datetime, timezone
from logging import Logger
from typing import Any, List

import asyncpg
from asyncpg import Pool

from src.scraping.models import JobDB

from .base import ATS, ATSCompany


class JobRepositoryPostgres:
    def __init__(self, logger: Logger, pool: Pool) -> None:
        self.logger = logger
        self.pool = pool

    async def save_job_batch(self, jobs: List[JobDB]) -> None:
        query = """
                    INSERT INTO jobs (
                        id, ats_type, ats_id, url, apply_url, title, company_id, location,
                        country_iso, region, employment_type, description, salary_min,
                        salary_max, salary_currency, is_normalized, posted_at, fetched_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8,
                        $9, $10, $11, $12, $13, $14, $15, $16,
                        $17, $18
                    )
                    ON CONFLICT (id) DO NOTHING;
                """
        buffer = [self._sanitize_record(self._job_to_db_params(job)) for job in jobs]

        if not buffer:
            return

        async with (self.pool.acquire() as conn):
            try:
                async with conn.transaction():
                    await conn.executemany(query, buffer)
            except asyncpg.PostgresError as exc:
                self.logger.warning(
                    f"[DB Writer] Batch insert failed ({type(exc).__name__}). Falling back to row-by-row insert."
                )
                for row in buffer:
                    try:
                        await conn.execute(query, *row)
                    except asyncpg.ForeignKeyViolationError as fk_err:
                        self.logger.error(
                            f"[DB Writer] Dropping job record due to invalid foreign key: {fk_err.detail} | Job URL: {row[3]}"
                        )
                    except asyncpg.PostgresError as row_err:
                        self.logger.error(
                            f"[DB Writer] Dropping job record due to DB error: {row_err} | Job URL: {row[3]}"
                        )
            except Exception as unhandled:
                self.logger.critical(
                    f"[DB Writer] Unexpected error during flush: {unhandled}", exc_info=True
                )

    def _sanitize_record(self, record: tuple | list) -> tuple:
        return tuple(val.replace("\x00", "") if isinstance(val, str) else val for val in record)

    def _job_to_db_params(self, job: JobDB) -> tuple[Any, ...]:
        return (
            job.id,
            job.ats_type.value,
            job.ats_id,
            str(job.url),
            str(job.apply_url) if job.apply_url else None,
            job.title,
            job.company_id,
            job.location,
            job.country_iso,
            job.region,
            job.employment_type or "FULL_TIME",
            job.description or "",
            job.salary_min,
            job.salary_max,
            job.salary_currency,
            job.is_normalized,
            job.posted_at,
            job.fetched_at or datetime.now(timezone.utc),
        )

class CompanyRepositoryPostgres:
    def __init__(self, logger: Logger, pool: Pool) -> None:
        self.logger = logger
        self.pool = pool

    async def get_tenants(self) -> List[ATS]:
        query = """
                SELECT id, ats, tier, name, slug, url  FROM companies
                    WHERE is_active = TRUE
                """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query)

        seen_ats = dict()
        for row in rows:
            ats_name = row["ats"]

            if not seen_ats.get(ats_name):
                seen_ats[ats_name] = ATS(name=ats_name, tier=row["tier"])

            ats = seen_ats[ats_name]
            company = ATSCompany(id=row["id"],
                                 name=row["name"],
                                 slug=row["slug"],
                                 url=row["url"],)
            ats.companies.append(company)

        return list(seen_ats.values())


    async def get_active_tenats_by_ats(self, ats: str) -> List[ATSCompany]:
        query = """
                    SELECT id, ats, name, slug, url
                    FROM companies
                    WHERE ats = $1 AND is_active = TRUE
                """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, ats)

        return [ATSCompany(**dict(row)) for row in rows]

    async def update_company_stats(
        self, is_success: bool, duration_ms: int, err: Exception | None, jobs_count: int, company_id
    ) -> None:
        query = """
                    UPDATE companies
                    SET 
                        last_attempt_at = CURRENT_TIMESTAMP,
                        last_success_at = CASE WHEN $1::boolean THEN CURRENT_TIMESTAMP ELSE last_success_at END,
                        last_scrape_duration_ms = $2::integer,
                        consecutive_errors = CASE WHEN $1::boolean THEN 0 ELSE consecutive_errors + 1 END,
                        last_error_message = $3::text,
                        last_job_count = CASE WHEN $1::boolean THEN $4::integer ELSE last_job_count END,
                        consecutive_zero_jobs = CASE 
                            WHEN NOT $1::boolean THEN consecutive_zero_jobs 
                            WHEN $4::integer = 0 THEN consecutive_zero_jobs + 1 
                            ELSE 0 
                        END,
                        is_active = CASE 
                            WHEN NOT $1::boolean AND (consecutive_errors + 1) >= 3 THEN FALSE 
                            ELSE is_active 
                        END
                    WHERE id = $5::integer;
                """

        async with self.pool.acquire() as conn:
            await conn.execute(query, is_success, duration_ms, err, jobs_count, company_id)
