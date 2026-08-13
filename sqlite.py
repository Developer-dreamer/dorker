import pathlib
from logging import Logger

import aiosqlite
import polars as pl

from model import map_row_to_domain

DATABASE_FILE = "/Users/serafym/Developer/dorker.space/jobs.db"
BATCH_SIZE = 1000

SQL_SCHEMA = """
             CREATE TABLE IF NOT EXISTS jobs (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 ats_type TEXT NOT NULL,
                 ats_id TEXT NOT NULL,
                 url TEXT NOT NULL,
                 apply_url TEXT,
                 title TEXT NOT NULL,
                 company_slug TEXT NOT NULL,
                 location TEXT,
                 is_remote TEXT,
                 employment_type TEXT DEFAULT 'FULL_TIME',
                 description TEXT NOT NULL,
                 salary_min REAL,
                 salary_max REAL,
                 salary_currency TEXT,
                 application_questions TEXT,
                 posted_at TEXT,
                 fetched_at TEXT NOT NULL
             );

             CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_ats_composite ON jobs (ats_type, ats_id);
             CREATE INDEX IF NOT EXISTS idx_jobs_search_routing ON jobs (is_remote, employment_type); \
             """

INSERT_QUERY = """
               INSERT INTO jobs (
                   ats_type, ats_id, url, apply_url, title, company_slug, location,
                   employment_type, description, salary_min, salary_max, salary_currency,
                   application_questions, posted_at, fetched_at, is_remote
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(ats_type, ats_id) DO NOTHING; \
               """


async def init_sqlite_db(logger: Logger) -> None:
    logger.info("Initializing SQLite file and applying database schema...")
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.executescript(SQL_SCHEMA)
        await db.commit()
    logger.info("Database schema applied successfully.")


async def execute_batch_ingestion(logger: Logger, parquet_path: pathlib.Path) -> None:
    logger.info("Opening memory-mapped view of the Parquet file...")
    lazy_engine = pl.scan_parquet(str(parquet_path))

    total_available_rows = lazy_engine.select(pl.len()).collect().item()
    logger.info(f"Total available rows in source file: {total_available_rows:,}")

    target_ingestion_limit = total_available_rows

    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute("PRAGMA journal_mode=WAL;")

        for offset in range(0, target_ingestion_limit, BATCH_SIZE):
            batch_df = lazy_engine.slice(offset, BATCH_SIZE).collect()

            # Map into a dictionary to preserve the structured domain objects for logging
            batch_map = {}
            for row_dict in batch_df.iter_rows(named=True):
                try:
                    domain_model = map_row_to_domain(row_dict)
                    key = (domain_model.ats_type, domain_model.ats_id)
                    batch_map[key] = domain_model
                except Exception as row_error:
                    logger.error(f"Error parsing row at offset {offset}: {row_error}")
                    continue

            if not batch_map:
                continue

            # Generate placeholders to check existing records in a single roundtrip
            keys_list = list(batch_map.keys())
            placeholders = ", ".join(["(?, ?)" for _ in keys_list])
            flattened_keys = [item for key in keys_list for item in key]

            check_query = f"""
                SELECT ats_type, ats_id 
                FROM jobs 
                WHERE (ats_type, ats_id) IN ({placeholders})
            """

            # Identify which items already exist in the database
            existing_keys = set()
            async with db.execute(check_query, flattened_keys) as cursor:
                async for row in cursor:
                    existing_keys.add((row[0], row[1]))

            # Split data into valid inserts and conflict logs
            insert_parameters = []
            for key, job in batch_map.items():
                if key in existing_keys:
                    logger.warning(
                        f"Conflict detected: Job {key[0]}:{key[1]} already exists. SQLite omitted entry."
                    )
                else:
                    insert_parameters.append(extract_job_tuple(job))

            if insert_parameters:
                await db.executemany(INSERT_QUERY, insert_parameters)
                await db.commit()

            processed_count = offset + batch_df.height
            progress_percentage = (processed_count / target_ingestion_limit) * 100

            skipped = len(existing_keys)
            inserted = len(insert_parameters)
            logger.info(
                f"Progress: {processed_count:,}/{target_ingestion_limit:,} ({progress_percentage:.2f}%) "
                f"| Batch: {inserted} inserted, {skipped} conflicts skipped."
            )
