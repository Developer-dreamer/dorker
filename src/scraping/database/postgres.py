from datetime import datetime, timezone
from logging import Logger
from typing import Any, List, Sequence

import asyncpg
from asyncpg import Pool

from src.scraping.models import Job, JobDB

from .base import ATS, ATSCompany, description_keys


def _sanitize_record(record: Sequence[Any]) -> tuple[Any, ...]:
    return tuple(val.replace("\x00", "") if isinstance(val, str) else val for val in record)


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
        buffer = [_sanitize_record(self._job_to_db_params(job)) for job in jobs]

        if not buffer:
            return

        async with self.pool.acquire() as conn:
            try:
                async with conn.transaction():
                    await conn.executemany(query, buffer)
            except asyncpg.PostgresError as exc:
                self.logger.warning(
                    f"[DB Writer] Batch insert failed ({type(exc).__name__}). "
                    f"Falling back to row-by-row insert."
                )
                for row in buffer:
                    try:
                        await conn.execute(query, *row)
                    except asyncpg.ForeignKeyViolationError as fk_err:
                        self.logger.error(
                            f"[DB Writer] Dropping job record due to invalid foreign key: "
                            f"{fk_err.detail} | Job URL: {row[3]}"
                        )
                    except asyncpg.PostgresError as row_err:
                        self.logger.error(
                            f"[DB Writer] Dropping job record due to DB error: {row_err} "
                            f"| Job URL: {row[3]}"
                        )
            except Exception as unhandled:
                self.logger.critical(
                    f"[DB Writer] Unexpected error during flush: {unhandled}", exc_info=True
                )

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

        seen_ats: dict[str, Any] = dict()
        for row in rows:
            ats_name = row["ats"]

            if not seen_ats.get(ats_name):
                seen_ats[ats_name] = ATS(name=ats_name, tier=row["tier"])

            ats = seen_ats[ats_name]
            company = ATSCompany(
                id=row["id"],
                name=row["name"],
                slug=row["slug"],
                url=row["url"],
            )
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
        self,
        is_success: bool,
        duration_ms: int,
        err: Exception | None,
        jobs_count: int,
        company_id: int,
    ) -> None:
        query = """
                    UPDATE companies
                    SET
                        last_attempt_at = CURRENT_TIMESTAMP,
                        last_success_at = CASE
                            WHEN $1::boolean THEN CURRENT_TIMESTAMP
                            ELSE last_success_at
                        END,
                        last_scrape_duration_ms = $2::integer,
                        consecutive_errors = CASE
                            WHEN $1::boolean THEN 0
                            ELSE consecutive_errors + 1
                        END,
                        last_error_message = $3::text,
                        last_job_count = CASE
                            WHEN $1::boolean THEN $4::integer
                            ELSE last_job_count
                        END,
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


class DescriptionCachePostgres:
    def __init__(self, logger: Logger, pool: Pool, compress: bool = True) -> None:
        self.logger = logger
        self.pool = pool
        self.compress = compress

        if compress:
            import zstandard

            self._compressor = zstandard.ZstdCompressor(level=3)
            self._decompressor = zstandard.ZstdDecompressor()

    async def close(self) -> None:
        """
        Pool is closed from outer scope
        """
        return

    async def get(self, job: Job) -> str | None:
        query = """
                SELECT payload
                FROM description_cache
                WHERE key_type = $1 AND key_value = $2
                """

        for key_type, key_value in description_keys(job):
            key_type = key_type.replace("\x00", "")
            key_value = key_value.replace("\x00", "")

            row = await self.pool.fetchrow(query, key_type, key_value)
            if row:
                self.logger.info(
                    f"Cache HIT for job_id {job.global_id} by key {key_value} of type {key_type}"
                )
                return self._decode(row["payload"])

        return None

    async def set(self, job: Job, description: str) -> None:
        description = description.replace("\x00", "")
        blob = self._encode(description)
        rows = []
        for key_type, key_value in description_keys(job):
            key_type = key_type.replace("\x00", "")
            key_value = key_value.replace("\x00", "")
            rows.append((key_type, key_value, blob))
        if not rows:
            return

        await self._insert_many(rows, replace=True)

    def _decode(self, blob: bytes) -> str:
        raw = self._decompressor.decompress(blob) if self.compress else blob
        return raw.decode("utf-8")

    def _encode(self, description: str) -> bytes:
        raw = description.encode("utf-8")
        return self._compressor.compress(raw) if self.compress else raw

    async def _insert_many(
        self, rows: list[tuple[str, str, bytes]], *, replace: bool = False
    ) -> int:
        if not rows:
            return 0

        conflict_clause = (
            "DO UPDATE SET description = EXCLUDED.description" if replace else "DO NOTHING"
        )

        query = f"""
            INSERT INTO description_cache (key_type, key_value, payload)
            VALUES ($1, $2, $3)
            ON CONFLICT (key_type, key_value) {conflict_clause}
        """

        # asyncpg's executemany returns a command tag string like 'INSERT 0 5'
        status = await self.pool.executemany(query, rows)
        # Parse inserted/updated rows count from status string if needed:
        return int(status.split()[-1]) if status else 0
