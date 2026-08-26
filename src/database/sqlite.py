import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import aiosqlite
import uuid6

from src.analytics.models import MatchedJob
from src.scraping.models import Job
from src.shared.models.company import Company


async def run_migrations(db_path: str | Path, migrations_dir: str | Path) -> None:
    migrations_path = Path(migrations_dir)

    if not migrations_path.exists() or not migrations_path.is_dir():
        raise FileNotFoundError(f"Migrations directory not found: {migrations_path}")

    # Sorting is mandatory to maintain sequential execution (e.g., 001_init.sql, 002_update.sql)
    migration_files = sorted(migrations_path.glob("*.sql"))

    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL;")

        # 1. Initialize migration tracking table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()

        # 2. Retrieve history of applied migrations
        async with db.execute("SELECT filename FROM schema_migrations") as cursor:
            applied_migrations = {row[0] async for row in cursor}

        # 3. Execute pending migrations
        for file_path in migration_files:
            filename = file_path.name

            if filename not in applied_migrations:
                sql_content = file_path.read_text(encoding="utf-8")

                try:
                    # executescript is required for multi-statement .sql files
                    await db.executescript(sql_content)

                    # Record successful execution
                    await db.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (?)",
                        (filename,)
                    )
                    await db.commit()
                except Exception as e:
                    # Rollback implicitly handled if not committed, 
                    # but explicit log/raise is required to stop the pipeline
                    raise RuntimeError(f"Migration failed on {filename}: {e}")

async def get_db_connection(db_path: str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(db_path, timeout=20.0)

    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA synchronous=NORMAL;")

    return conn


async def get_companies_from_ats_randomly(
        db_path: str | Path,
        ats_name: str,
        limit: int = 10) -> List[Company]:

    query = """SELECT
                    ats_name as ats_name,
                    company_name as name,
                    company_slug as slug,
                    url,
                    tier
               FROM ats
               WHERE ats_name = ?
               ORDER BY RANDOM()
               LIMIT ?;
            """

    async with aiosqlite.connect(db_path) as db:
        # Enables dictionary-like access on rows (row["column_name"])
        db.row_factory = aiosqlite.Row

        async with db.execute(query, (ats_name, limit)) as cursor:
            rows = await cursor.fetchall()
            # Map directly using dictionary unpacking into the Pydantic model
            return [Company(**dict(row)) for row in rows]

'''
Do not forget to set company inside job model to company_slug before call
'''
async def save_job(db_path: str, job: Job) -> None:
    query = """
            INSERT INTO jobs (
                id,
                ats_type,
                ats_id,
                url,
                apply_url,
                title,
                company_slug,
                location,
                country_iso,
                region,
                employment_type,
                description,
                salary_min,
                salary_max,
                salary_currency,
                posted_at,
                fetched_at
            ) VALUES (
                :id,
                :ats_type,
                :ats_id,
                :url,
                :apply_url,
                :title,
                :company_slug,
                :location,
                :country_iso,
                :region,
                :employment_type,
                :description,
                :salary_min,
                :salary_max,
                :salary_currency,
                :posted_at,
                :fetched_at
            )
            ON CONFLICT (id) DO NOTHING;
            """

    params = {
                "id": job.global_id,
                "ats_type": job.ats_type.value,
                "ats_id": job.ats_id or job.global_id,
                "url": str(job.url),
                "apply_url": str(job.apply_url) if job.apply_url else None,
                "title": job.title,
                "company_slug": job.company,
                "location": job.location,
                "country_iso": job.country_iso,
                "region": job.region,
                "employment_type": job.employment_type or "FULL_TIME",
                "description": job.description or "",
                "salary_min": job.salary_min,
                "salary_max": job.salary_max,
                "salary_currency": job.salary_currency,
                "posted_at": job.posted_at.isoformat() if job.posted_at else None,
                "fetched_at": (job.fetched_at or datetime.now(timezone.utc)).isoformat(),
            }

    async with aiosqlite.connect(db_path) as db:
        await db.execute(query, params)
        await db.commit()

async def job_exists(db_path: str, global_id: str) -> bool:
    query = """
            SELECT 1 FROM jobs
            WHERE id = ?
            LIMIT 1;
            """
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(query, (global_id,)) as cursor:
            row = await cursor.fetchone()
            return row is not None

async def save_technical_match(
    db: aiosqlite.Connection,
    job_id: str,
    pipeline_status: str = "PENDING",
) -> str:
    query = """
    INSERT INTO matches (
        id,
        job_id,
        is_technical,
        pipeline_status,
        technical_capability_score,
        strategic_value_score,
        analytics
    ) VALUES (
        :id,
        :job_id,
        :is_technical,
        :pipeline_status,
        :technical_capability_score,
        :strategic_value_score,
        :analytics
    );
    """
    uuid = str(uuid6.uuid7())
    params = {
        "id": uuid,
        "job_id": job_id,
        "is_technical": 1,
        "pipeline_status": pipeline_status,
        "technical_capability_score": 0.0,
        "strategic_value_score": 0.0,
        "analytics": json.dumps({"pros": [], "cons": [], "warnings": []}),
    }

    await db.execute(query, params)
    await db.commit()

    return uuid

async def save_unmatch(
    db: aiosqlite.Connection,
    job_id: str,
) -> str:
    query = """
    INSERT INTO matches (
        id,
        job_id,
        is_technical,
        pipeline_status,
        technical_capability_score,
        strategic_value_score,
        strategic_reason,
        analytics
    ) VALUES (
        :id,
        :job_id,
        :is_technical,
        :pipeline_status,
        :technical_capability_score,
        :strategic_value_score,
        :strategic_reason,
        :analytics
    );
    """
    uuid =  str(uuid6.uuid7())
    params = {
        "id": uuid,
        "job_id": job_id,
        "is_technical": 0,
        "pipeline_status": "DECLINED",
        "technical_capability_score": 0.0,
        "strategic_value_score": 0.0,
        "strategic_reason": "Filtered by gatekeeper",
        "analytics": json.dumps({"pros": [], "cons": [], "warnings": []}),
    }

    await db.execute(query, params)
    await db.commit()

    return uuid

