import asyncio
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from src.scraping.configuration_manager import CONFIGS
from src.scraping.models import Job
from src.scraping.run import _pipeline_lock, run

DB_PATH = Path("/Users/serafym/Developer/dorker.space/intelligence_core/app.db")
SLEEP_INTERVAL_HOURS = 6

def get_active_ats_platforms() -> list[str]:
    """Retrieve distinct ATS platforms present in your SQLite database."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT DISTINCT ats_name FROM ats ORDER BY tier").fetchall()
        db_ats = {row[0] for row in rows}
    # Only run platforms that exist both in the database and CONFIGS
    return [ats for ats in CONFIGS.keys() if ats in db_ats or CONFIGS[ats].get("singleton")]

def _job_to_db_params(job: Job) -> dict[str, Any]:
    return {
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
        "is_normalized": False,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "fetched_at": (job.fetched_at or datetime.now(timezone.utc)).isoformat(),
    }

SAVE_JOBS_BATCH_QUERY = """
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
            is_normalized,
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
            :is_normalized,
            :posted_at,
            :fetched_at
        )
        ON CONFLICT (ats_type, ats_id) DO NOTHING;
    """

async def db_writer_worker(
    db_path: Path,
    queue: asyncio.Queue[Job | None],
    batch_size: int = 500
) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")

        buffer: list[dict[str, Any]] = []

        async def flush() -> None:
            if not buffer:
                return
            await db.executemany(SAVE_JOBS_BATCH_QUERY, buffer)
            await db.commit()
            buffer.clear()

        try:
            while True:
                job = await queue.get()
                if job is None:  # Shutdown sentinel
                    await flush()
                    queue.task_done()
                    break

                buffer.append(_job_to_db_params(job))
                queue.task_done()

                # Flush if threshold met or no more items immediately waiting in queue
                if len(buffer) >= batch_size or (queue.empty() and buffer):
                    await flush()
        finally:
            await flush()

async def run_single_ats(ats: str, queue: asyncio.Queue[Job | None]) -> None:
    # Use existing file-lock to prevent overlapping cron/script instances
    with _pipeline_lock(ats) as acquired:
        if not acquired:
            return
        print(f"\n=== Starting scrape for ATS: {ats} ===")
        # Default concurrency: 8, max_tenants: None (all), timeout: 30s
        code = await run(ats=ats, db_path=DB_PATH, queue=queue, concurrency=8, max_tenants=None, timeout=30.0)
        if code != 0:
            print("Ahh, failed nigga.")



async def main_loop() -> None:
    while True:
        cycle_start = time.time()
        ats_list = get_active_ats_platforms()
        print(f"[Orchestrator] Starting cycle across {len(ats_list)} platforms.")

        queue: asyncio.Queue[Job | None] = asyncio.Queue(maxsize=1000)
        writer_task = asyncio.create_task(db_writer_worker(DB_PATH, queue, batch_size=10))

        ats_sem = asyncio.Semaphore(5)

        async def run_guarded_ats(ats: str) -> None:
            async with ats_sem:
                try:
                    await run_single_ats(ats, queue)
                except Exception as e:
                    print(f"[Orchestrator] Failed {ats}: {type(e).__name__}: {e}")

        # Launch all ATS scrapers concurrently
        await asyncio.gather(*(run_guarded_ats(ats) for ats in ats_list))

        await queue.join()

        # 4. Stop writer worker cleanly
        await queue.put(None)
        await writer_task

        elapsed = time.time() - cycle_start
        sleep_seconds = max(0.0, (SLEEP_INTERVAL_HOURS * 3600) - elapsed)
        print(f"[Orchestrator] Cycle finished in {elapsed:.0f}s. Sleeping for {sleep_seconds/3600:.1f}h...")
        await asyncio.sleep(sleep_seconds)

if __name__ == "__main__":
    asyncio.run(main_loop())