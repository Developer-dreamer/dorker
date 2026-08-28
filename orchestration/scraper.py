import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Tuple

import asyncpg
from rich.console import Group
from rich.live import Live
from rich.progress import BarColumn, Progress, TextColumn
from rich.text import Text

from src.scraping.configuration_manager import CONFIGS
from src.scraping.models import JobDB
from src.scraping.run import _pipeline_lock, run
from src.shared.types.priority_semaphore import PrioritySemaphore

ROOT = Path(__file__).resolve().parent.parent

DB_PATH = ROOT / "cache" / "descriptions.db"
SLEEP_INTERVAL_HOURS = 6
PG_DSN = "postgresql://postgres:password@localhost:5432/dorker_db"

LOG_PATH = ROOT / "logs" / f"scraper_{datetime.now(timezone.utc).isoformat()}.log"

# Configure file-only logging
logging.basicConfig(
    filename=LOG_PATH,
    filemode="a",
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("orchestrator")


@dataclass
class ATSState:
    current: int = 0
    total: int = 0
    slug: str = ""
    found: int = 0
    queued: int = 0
    dupes: int = 0


class Dashboard:
    def __init__(self, ats_list: list[str]):
        self.pending = ats_list.copy()
        self.working: dict[str, ATSState] = {}
        self.finished: list[str] = []
        self.start_time = time.time()
        self.total_ats = len(ats_list)

    def generate_layout(self) -> Group:
        completed = len(self.finished)
        total = self.total_ats

        progress = Progress(
            BarColumn(bar_width=60, complete_style="green", finished_style="green"),
            TextColumn("{task.completed} / {task.total} ({task.percentage:>3.0f}%)", style="green"),
        )
        progress.add_task("", total=total, completed=completed)

        pending_str = ", ".join(self.pending[:5]) + (" ..." if len(self.pending) > 5 else "")
        pending_text = Text(f"Pending list: [ {pending_str} ]", style="white")

        working_lines = [Text("Working:", style="orange3")]
        for ats, state in self.working.items():
            line = f"- {ats} [{state.current}/{state.total}] | {state.slug} | {state.found} found, {state.queued} queued, {state.dupes} dupes"
            working_lines.append(Text(line, style="orange3"))
        if not self.working:
            working_lines.append(Text("- (none)", style="orange3"))

        finished_str = ", ".join(self.finished[-5:]) + (" ..." if len(self.finished) > 5 else "")
        finished_text = Text(f"Finished: [ {finished_str} ]", style="green")

        elapsed_sec = int(time.time() - self.start_time)
        hours, rem = divmod(elapsed_sec, 3600)
        minutes, seconds = divmod(rem, 60)

        parts = []
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0 or hours > 0:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")

        formatted_time = " ".join(parts)
        elapsed_text = Text(f"Elapsed: {formatted_time}", style="deep_sky_blue1")

        return Group(progress, pending_text, *working_lines, finished_text, elapsed_text)


async def get_active_ats_platforms() -> list[Tuple[int, str]]:
    """Retrieve distinct ATS platforms ordered by tier."""
    conn = await asyncpg.connect(PG_DSN)
    try:
        # Fetch rows already sorted by tier
            rows = await conn.fetch("SELECT DISTINCT ats, tier FROM companies ORDER BY tier ASC")

            cfg_keys = CONFIGS.keys()

            # Deduplicate while preserving order
            seen: set[str] = set()
            result: list[Tuple[int, str]] = []

            for ats, tier in rows:
                if ats not in seen and (ats in cfg_keys or CONFIGS[ats].get("singleton")):
                    seen.add(ats)
                    result.append((tier, ats))

            # Explicitly sort by tier to guarantee lower tier numbers run first
            result.sort(key=lambda x: x[0])
            return result
    finally:
        await conn.close()

def _job_to_db_params(job: JobDB) -> tuple[Any, ...]:
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


SAVE_JOBS_BATCH_QUERY = """
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

def sanitize_record(record: tuple | list) -> tuple:
    return tuple(
        val.replace("\x00", "") if isinstance(val, str) else val
        for val in record
    )

async def db_writer_worker(
    queue: asyncio.Queue[JobDB | None], batch_size: int = 500
) -> None:
    db = await asyncpg.connect(PG_DSN)
    buffer: list[tuple[Any, ...]] = []

    async def flush() -> None:
        if not buffer:
            return
        sanitized_buffer = [sanitize_record(row) for row in buffer]

        try:
            # Attempt fast batch insert
            async with db.transaction():
                await db.executemany(SAVE_JOBS_BATCH_QUERY, sanitized_buffer)
        except asyncpg.PostgresError as exc:
            logger.warning(
                f"[DB Writer] Batch insert failed ({type(exc).__name__}). Falling back to row-by-row insert."
            )
            # Fallback: process individually to isolate and drop problematic records
            for row in sanitized_buffer:
                try:
                    async with db.transaction():
                        await db.execute(SAVE_JOBS_BATCH_QUERY, *row)
                except asyncpg.ForeignKeyViolationError as fk_err:
                    logger.error(
                        f"[DB Writer] Dropping job record due to invalid foreign key: {fk_err.detail} | Job URL: {row[3]}"
                    )
                except asyncpg.PostgresError as row_err:
                    logger.error(
                        f"[DB Writer] Dropping job record due to DB error: {row_err} | Job URL: {row[3]}"
                    )
        except Exception as unhandled:
            logger.critical(
                f"[DB Writer] Unexpected error during flush: {unhandled}", exc_info=True
            )
        finally:
            buffer.clear()

    try:
        while True:
            job = await queue.get()
            if job is None:
                await flush()
                queue.task_done()
                break

            buffer.append(_job_to_db_params(job))
            queue.task_done()

            if len(buffer) >= batch_size or (queue.empty() and buffer):
                await flush()
    except asyncio.CancelledError:
        await flush()
    except Exception as exc:
        logger.critical(f"[DB Writer] Fatal error in worker loop: {exc}", exc_info=True)
    finally:
        await flush()
        await db.close()

async def ui_worker(ui_queue: asyncio.Queue, dashboard: Dashboard, live: Live) -> None:
    while True:
        msg = await ui_queue.get()
        if msg is None:
            ui_queue.task_done()
            break

        msg_type = msg.get("type")
        ats = msg.get("ats")

        if msg_type == "start":
            if ats in dashboard.pending:
                dashboard.pending.remove(ats)
            dashboard.working[ats] = ATSState()
        elif msg_type == "progress":
            if ats in dashboard.working:
                state = dashboard.working[ats]
                state.current = msg.get("current", 0)
                state.total = msg.get("total", 0)
                state.slug = msg.get("slug", "")
                state.found = msg.get("found", 0)
                state.queued = msg.get("queued", 0)
                state.dupes = msg.get("dupes", 0)
        elif msg_type == "finish":
            if ats in dashboard.working:
                del dashboard.working[ats]
            if ats not in dashboard.finished:
                dashboard.finished.append(ats)

        live.update(dashboard.generate_layout())
        ui_queue.task_done()


async def run_single_ats(
    semaphore: PrioritySemaphore,
    tier: int,
    ats: str,
    queue: asyncio.Queue[JobDB | None],
    ui_queue: asyncio.Queue,
    pool: asyncpg.Pool,
) -> None:
    await semaphore.acquire(tier)
    conn = await asyncpg.connect(PG_DSN)
    try:
        logger.debug(f"Worker {ats} (Priority {tier}) acquired.")
        ui_queue.put_nowait({"type": "start", "ats": ats})

        with _pipeline_lock(ats) as acquired:
            if not acquired:
                ui_queue.put_nowait({"type": "finish", "ats": ats})
                return

            logger.info(f"=== Starting scrape for ATS: {ats} ===")

            code = await run(
                ats=ats,
                tier=tier,
                pg_db=pool,
                description_cache_db=DB_PATH,
                queue=queue,
                semaphore=semaphore,
                concurrency=8,
                max_tenants=None,
                timeout=30.0,
                ui_queue=ui_queue,
            )
            if code != 0:
                logger.error(f"Scraper {ats} failed with code {code}.")


    finally:
        semaphore.release()
        await conn.close()
        ui_queue.put_nowait({"type": "finish", "ats": ats})



async def main_loop() -> None:
    ats_list = await get_active_ats_platforms()
    ats_names = [ats for _, ats in ats_list]
    logger.info(f"[Orchestrator] Starting cycle across {len(ats_list)} platforms.")

    ui_queue: asyncio.Queue = asyncio.Queue()
    dashboard = Dashboard(ats_names)
    async with asyncpg.create_pool(PG_DSN) as pool:
        with Live(dashboard.generate_layout(), refresh_per_second=4) as live:
            queue: asyncio.Queue[JobDB | None] = asyncio.Queue(maxsize=1000)
            writer_task = asyncio.create_task(db_writer_worker(queue))
            ui_task = asyncio.create_task(ui_worker(ui_queue, dashboard, live))

            sem = PrioritySemaphore(5)

            await asyncio.gather(
                *(run_single_ats(sem, tier, ats, queue, ui_queue, pool) for tier, ats in ats_list)
            )

            await queue.join()
            await queue.put(None)
            await writer_task

            await ui_queue.put(None)
            await ui_task


if __name__ == "__main__":
    asyncio.run(main_loop())
