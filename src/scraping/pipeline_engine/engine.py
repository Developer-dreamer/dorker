import asyncio
import fcntl
import os
import tempfile
import time
from contextlib import contextmanager
from logging import Logger
from pathlib import Path
from typing import Any, Dict, Iterator, List

from asyncpg import Pool

from src.scraping.configuration_manager import DynamicConfigManager
from src.scraping.database.base import CompanyRepository, DescriptionCache, JobRepository
from src.shared.models.company import ATS
from src.shared.models.job import Job as JobDomain
from src.shared.types.priority_semaphore import PrioritySemaphore

from .scraper_runner import ScraperRunner


class RunEngine:
    def __init__(
        self,
        logger: Logger,
        cfg: DynamicConfigManager,
        pool: Pool,
        job_repository: JobRepository,
        company_repo: CompanyRepository,
        description_cache: DescriptionCache,
        ui_queue: asyncio.Queue[Dict[str, Any] | None] | None = None,
        max_concurrent_ats: int = 5,
    ) -> None:
        self.logger = logger
        self.cfg: DynamicConfigManager = cfg
        self.job_repository = job_repository
        self.company_repo = company_repo
        self.pool: Pool = pool

        self.priority_sem = PrioritySemaphore(max_concurrent_ats)
        self.db_writer_queue: asyncio.Queue[JobDomain | None] = asyncio.Queue(maxsize=1000)
        self.ui_queue: asyncio.Queue[Dict[str, Any] | None] | None = ui_queue
        self.description_cache: DescriptionCache = description_cache

    async def run(self, ats_list: List[ATS]) -> None:
        self.logger.info(f"[Engine] Starting cycle across {len(ats_list)} platforms.")

        db_worker_task = asyncio.create_task(self._db_writer_worker(batch_size=500))

        try:
            await asyncio.gather(*(self._run_single_ats(ats) for ats in ats_list))
        finally:
            await self.db_writer_queue.put(None)
            await db_worker_task

    async def _run_single_ats(self, ats: ATS) -> None:
        await self.priority_sem.acquire(ats.tier)
        try:
            self.logger.debug(f"[Engine] Worker {ats.name} (Priority {ats.tier}) acquired.")

            if self.ui_queue:
                self.ui_queue.put_nowait({"type": "start", "ats": ats.name})

            with self._pipeline_lock(ats.name) as acquired:
                if not acquired:
                    if self.ui_queue:
                        self.ui_queue.put_nowait({"type": "finish", "ats": ats.name})
                    return

                self.logger.info(f"[Engine] === Starting scrape for ATS: {ats.name} ===")

                ats_runner = ScraperRunner(
                    logger=self.logger,
                    ats=ats,
                    cfg=self.cfg,
                    priority_semaphore=self.priority_sem,
                    description_cache=self.description_cache,
                    company_repo=self.company_repo,
                    db_queue=self.db_writer_queue,
                    concurrency=1,
                    timeout=1,
                    ui_queue=self.ui_queue,
                )

                await ats_runner.run()
        finally:
            self.priority_sem.release()
            if self.ui_queue:
                self.ui_queue.put_nowait({"type": "finish", "ats": ats.name})

    @contextmanager
    def _pipeline_lock(self, ats: str) -> Iterator[bool]:
        """Prevent concurrent runs of the same ATS pipeline.

        Cron can start a new daily run while a previous long runner is still
        writing `{ats}/.jobs.csv.tmp`. The publish step correctly refuses to
        publish while that temp output exists, so overlapping runs can block
        deployment for days. `flock` releases automatically if the process dies.
        """
        lock_path = Path(tempfile.gettempdir()) / f"ats-scrapers-run-pipeline-{ats}.lock"
        with lock_path.open("a+") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fh.seek(0)
                owner = fh.read().strip() or "unknown pid"
                self.logger.warning(
                    f"[Engine] | {ats} | another run is already active ({owner}); skipping."
                )
                yield False
                return
            fh.seek(0)
            fh.truncate()
            fh.write(
                f"pid={os.getpid()} started_at="
                f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
            )
            fh.flush()
            try:
                yield True
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    async def _db_writer_worker(self, batch_size: int = 500) -> None:
        buffer: list[JobDomain] = []

        async def _flush() -> None:
            if not buffer:
                return
            await self.job_repository.save_job_batch(buffer)
            buffer.clear()

        try:
            while True:
                job = await self.db_writer_queue.get()
                try:
                    if job is None:
                        await _flush()
                        break

                    buffer.append(job)

                    if len(buffer) >= batch_size or (self.db_writer_queue.empty() and buffer):
                        await _flush()
                finally:
                    self.db_writer_queue.task_done()

        except asyncio.CancelledError:
            await _flush()
            raise
        except Exception as exc:
            self.logger.critical(f"[DB Writer] Fatal error in worker loop: {exc}", exc_info=True)
            await _flush()
