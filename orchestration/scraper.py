import asyncio
import heapq
import itertools
import logging
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Tuple

import aiosqlite
from rich.console import Group
from rich.live import Live
from rich.progress import BarColumn, Progress, TextColumn
from rich.text import Text

from src.scraping.configuration_manager import CONFIGS
from src.scraping.models import Job
from src.scraping.run import _pipeline_lock, run

DB_PATH = Path("/Users/serafym/Developer/dorker.space/intelligence_core/app.db")
SLEEP_INTERVAL_HOURS = 6

# Configure file-only logging
logging.basicConfig(
    filename="scraper.log",
    filemode="a",
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
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
            TextColumn("{task.completed} / {task.total} ({task.percentage:>3.0f}%)", style="green")
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

        return Group(
            progress,
            pending_text,
            *working_lines,
            finished_text,
            elapsed_text
        )


class PrioritySemaphore:
    def __init__(self, value: int = 5):
        if value < 0:
            raise ValueError("Semaphore value must be >= 0")
        self._value = value
        self._waiters = []
        self._counter = itertools.count()

    async def acquire(self, priority: int) -> bool:
        if self._value > 0 and not self._waiters:
            self._value -= 1
            return True

        event = asyncio.Event()
        waiter_entry = [priority, next(self._counter), event, False]
        heapq.heappush(self._waiters, waiter_entry)

        try:
            await event.wait()
        except asyncio.CancelledError:
            waiter_entry[3] = True
            if event.is_set():
                self.release()
            raise

        return False

    def release(self) -> None:
        while self._waiters:
            waiter_entry = heapq.heappop(self._waiters)
            if not waiter_entry[3]:
                waiter_entry[2].set()
                return
        self._value += 1

    @asynccontextmanager
    async def request(self, priority: int) -> AsyncGenerator["PrioritySemaphore"]:
        await self.acquire(priority)
        try:
            yield self
        finally:
            self.release()


def get_active_ats_platforms() -> list[Tuple[int, str]]:
    """Retrieve distinct ATS platforms ordered by tier."""
    with sqlite3.connect(DB_PATH) as conn:
        # Fetch rows already sorted by tier
        rows = conn.execute("SELECT DISTINCT ats_name, tier FROM ats ORDER BY tier ASC").fetchall()

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
        id, ats_type, ats_id, url, apply_url, title, company_slug, location,
        country_iso, region, employment_type, description, salary_min,
        salary_max, salary_currency, is_normalized, posted_at, fetched_at
    ) VALUES (
        :id, :ats_type, :ats_id, :url, :apply_url, :title, :company_slug,
        :location, :country_iso, :region, :employment_type, :description,
        :salary_min, :salary_max, :salary_currency, :is_normalized,
        :posted_at, :fetched_at
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
                if job is None:
                    await flush()
                    queue.task_done()
                    break

                buffer.append(_job_to_db_params(job))
                queue.task_done()

                if len(buffer) >= batch_size or (queue.empty() and buffer):
                    await flush()
        finally:
            await flush()


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
    queue: asyncio.Queue[Job | None],
    ui_queue: asyncio.Queue
) -> None:
    async with semaphore.request(tier):
        logger.debug(f"Worker {ats} (Priority {tier}) acquired.")
        ui_queue.put_nowait({"type": "start", "ats": ats})

        with _pipeline_lock(ats) as acquired:
            if not acquired:
                ui_queue.put_nowait({"type": "finish", "ats": ats})
                return

            logger.info(f"=== Starting scrape for ATS: {ats} ===")
            code = await run(
                ats=ats,
                db_path=DB_PATH,
                queue=queue,
                concurrency=8,
                max_tenants=None,
                timeout=30.0,
                ui_queue=ui_queue
            )
            if code != 0:
                logger.error(f"Scraper {ats} failed with code {code}.")

        ui_queue.put_nowait({"type": "finish", "ats": ats})


async def main_loop() -> None:
    ats_list = get_active_ats_platforms()
    ats_names = [ats for _, ats in ats_list]
    logger.info(f"[Orchestrator] Starting cycle across {len(ats_list)} platforms.")

    ui_queue: asyncio.Queue = asyncio.Queue()
    dashboard = Dashboard(ats_names)

    with Live(dashboard.generate_layout(), refresh_per_second=4) as live:
        queue: asyncio.Queue[Job | None] = asyncio.Queue(maxsize=1000)
        writer_task = asyncio.create_task(db_writer_worker(DB_PATH, queue))
        ui_task = asyncio.create_task(ui_worker(ui_queue, dashboard, live))

        sem = PrioritySemaphore(5)

        await asyncio.gather(*(run_single_ats(sem, tier, ats, queue, ui_queue) for tier, ats in ats_list))

        await queue.join()
        await queue.put(None)
        await writer_task

        await ui_queue.put(None)
        await ui_task


if __name__ == "__main__":
    asyncio.run(main_loop())