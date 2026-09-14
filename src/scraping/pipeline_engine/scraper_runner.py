import asyncio
import json
from datetime import time
from logging import Logger
from typing import Any, Tuple
from urllib.parse import urlparse

from base import BaseScraper
from description_cache import DescriptionCache
from exceptions import CompanyNotFoundError
from models import Job, JobDB
from pydantic import ValidationError
from ui.cli import Counts, DescCounts

from shared.types.priority_semaphore import PrioritySemaphore
from src.scraping.configuration_manager import DynamicConfigManager
from src.scraping.database.base import ATS, CompanyRepository

STREAM_DESCRIPTION_CONCURRENCY = 8


class ScraperRunner:
    def __init__(self, logger: Logger,
                       ats: ATS,
                       cfg: DynamicConfigManager,
                       priority_semaphore: PrioritySemaphore,
                       description_cache: DescriptionCache,
                       company_repo: CompanyRepository,
                       db_queue: asyncio.Queue[JobDB | None],
                       concurrency: int,
                       timeout: float,
                       max_tenants: int | None = None,
                       ui_queue: asyncio.Queue | None = None
                 ) -> None:
        self.logger = logger

        self.cfg = cfg
        self.per_ats_cfg_copy = cfg[ats.name]
        self.ats = ats
        self.concurrency = self._bounded_concurrency(self.per_ats_cfg_copy, concurrency)
        configured_max_concurrency = self.per_ats_cfg_copy.get("max_concurrency")
        if isinstance(configured_max_concurrency, int):
            self.concurrency = min(self.concurrency, max(1, configured_max_concurrency))

        self.timeout = timeout
        self.configured_target_count = len(ats.companies)
        self.omitted_required_shards = 0
        if max_tenants:
            self.ats.companies = self.ats.companies[:max_tenants]
            if self.per_ats_cfg_copy.get("fail_closed_on_any_error"):
                self.omitted_required_shards = self.configured_target_count - len(self.ats.companies)

        self.logger.info(
            f"[{ats.name}] [INFO] Starting pipeline: {len(self.ats.companies)} targets "
            f"(concurrency={self.concurrency}, desc_concurrency={self.per_ats_cfg_copy.get('description_concurrency', self.concurrency)}, "
            f"timeout={self.timeout}s, singleton={bool(self.per_ats_cfg_copy.get('singleton'))})"
        )

        # Main semaphore managing maximum concurrent Scraper Runners
        self.priority_semaphore = priority_semaphore
        # Internal semaphore per ATS for description fetching
        self.sem = asyncio.Semaphore(self.concurrency)
        description_concurrency = self.per_ats_cfg_copy.get("description_concurrency", concurrency)
        if not isinstance(description_concurrency, int):
            self.description_concurrency = concurrency
        else:
            self.description_concurrency = 0

        self.description_sem = asyncio.Semaphore(max(1, self.description_concurrency))
        self.tenant_delay = float(self.per_ats_cfg_copy.get("tenant_delay_seconds"), 0)
        self.description_delay = float(self.per_ats_cfg_copy.get("description_delay_seconds", 0))

        self.uses_streaming = bool(self.per_ats_cfg_copy.get("singleton") and hasattr(cfg["scraper"], "fetch_stream"))

        self.description_cache = description_cache
        self.company_repo = company_repo
        self.db_queue = db_queue
        self.ui_queue = ui_queue

        self.seen_keys: set[tuple[str, str]] = set()
        self.tenants_completed = 0
        self.start = time.time()

        self.scraper = self.per_ats_cfg_copy["scraper"](self.ats.name, timeout=self.timeout)
        self.pending_descriptions: set[asyncio.Task[Tuple[int, Job]]] = set()

        self.counts, self.desc_counts = Counts(), DescCounts()

        self.incr_lock = asyncio.Lock()
        self.tenants_completed = 0

        self.logger.debug(
            f"[{self.ats.name}] Static configuration: {json.dumps(self.static_config, indent=2)}"
        )

    @property
    def static_config(self) -> dict:
        """Returns the immutable/static runtime parameters of this instance."""
        return {
            "ats_name": self.ats.name,
            "concurrency": self.concurrency,
            "description_concurrency": self.description_concurrency,
            "timeout": self.timeout,
            "configured_target_count": self.configured_target_count,
            "omitted_required_shards": self.omitted_required_shards,
            "tenant_delay": self.tenant_delay,
            "description_delay": self.description_delay,
            "uses_streaming": self.uses_streaming,
            "singleton": bool(self.per_ats_cfg_copy.get("singleton")),
        }

    def _bounded_concurrency(self, cfg: dict[str, Any], requested: int) -> int:
        if requested < 1:
            raise ValueError("concurrency must be at least 1")
        configured = cfg.get("max_concurrency")
        if isinstance(configured, int) and configured > 0:
            return min(requested, configured)
        return requested

    async def run(self) -> None:
        if self.uses_streaming:
            await self._run_streaming()
        else:
            await self._run_default()

        self._log_summary()

    def _log_summary(self) -> None:
        elapsed = time.time() - self.start
        rate = self.counts.jobs_queued / max(1.0, elapsed)

        summary = (
            f"\n{'=' * 60}\n"
            f"[{self.ats.name}] RUN SUMMARY:\n"
            f"  Duration:         {elapsed:.1f}s (~{elapsed / 60:.1f} min)\n"
            f"  Tenants:          {self.counts.success} success, {self.counts.not_found} not found, {self.counts.error} failed / {len(self.ats.companies)} total\n"
            f"  Jobs Processed:   {self.counts.jobs_scraped:,} scraped -> {self.counts.jobs_queued:,} queued ({self.counts.jobs_deduped:,} deduped)\n"
            f"  Throughput:       {rate:.1f} jobs/sec\n"
            f"  Descriptions:     {self.desc_counts.fetched} fetched over HTTP, "
            f"{self.desc_counts.cache} cache hits, {self.desc_counts.present} present in payload, "
            f"{self.desc_counts.missing} missing, {self.desc_counts.error} failed\n"
            f"{'=' * 60}\n"
        )

        self.logger.info(summary)

        failure_threshold = max(1, (len(self.ats.companies) + 1) // 2)
        if self.uses_streaming and self.counts.error > 0:
            self.logger.error(f"[{self.ats.name}] [FAILURE] Streaming scrape terminated with fatal error.")

        if bool(self.per_ats_cfg_copy.get("fail_closed_on_empty")) and bool(self.ats.companies) and self.counts.jobs_queued == 0:
            self.logger.error(
                f"[{self.ats.name}] [FAILURE] fail_closed_on_empty triggered: 0 jobs queued from {len(self.ats.companies)} tenants."
            )

        required_not_found = self.counts.not_found if self.per_ats_cfg_copy.get("fail_closed_on_not_found") else 0
        sharded_failure = (
            bool(self.per_ats_cfg_copy.get("fail_closed_on_any_error"))
            and (self.counts.error > 0 or self.omitted_required_shards > 0)
        ) or required_not_found > 0
        if sharded_failure:
            required_failures = self.counts.error + self.omitted_required_shards + required_not_found
            self.logger.error(
                f"[{self.ats.name}] [FAILURE] Required shards failed: {required_failures}/{self.configured_target_count} "
                f"failures (errors={self.counts.error}, omitted={self.omitted_required_shards}, not_found={required_not_found})."
            )

        catastrophic_failure = (
                bool(self.ats.companies) and self.counts.jobs_queued == 0 and self.counts.error >= failure_threshold
        )
        if catastrophic_failure:
            self.logger.error(
                f"[{self.ats.name}] [FAILURE] Catastrophic failure: 0 jobs produced and "
                f"{self.counts.error}/{len(self.ats.companies)} tenant errors exceeded threshold ({failure_threshold})."
            )

        if self.counts.error >= failure_threshold:
            self.logger.warning(
                f"[{self.ats.name}] [WARN] Kept partial data but {self.counts.error}/{len(self.ats.companies)} tenants failed."
            )

    async def _run_streaming(self):
        company_id = self.ats.companies[0].id
        desc_stats = DescCounts()

        try:
            async for job in self.scraper.fetch_stream():
                cached = self.description_cache.get(job)
                if cached:
                    job.description = cached
                    desc_stats.cache += 1
                elif job.description:
                    desc_stats.present += 1
                else:
                    self.pending_descriptions.add(
                        asyncio.create_task(self._enrich_missing_stream_description(company_id, job))
                    )
                    if len(self.pending_descriptions) >= STREAM_DESCRIPTION_CONCURRENCY:
                        await self._drain_description_tasks()

                await self._write_streamed_job(company_id, job)
            await self._drain_description_tasks(all_tasks=True)
            self.counts.success = 1

        except CompanyNotFoundError:
            for task in self.pending_descriptions:
                task.cancel()
            self.counts.not_found = 1
            self.logger.warning(f"[{self.ats.name}] [WARN] Streaming target company not found.")
        except Exception as exc:
            for task in self.pending_descriptions:
                task.cancel()
            self.counts.error = 1
            self.logger.error(
                f"[{self.ats.name}] [ERROR] Streaming failed: {type(exc).__name__}: {str(exc)[:300]}"
            )

    async def _run_default(self, batch_size: int = 50):
        for i in range(0, len(self.ats.companies), batch_size):
            if await self._check_yield_or_exit(i):
                return

            batch = self.ats.companies[i : i + batch_size]
            batch_t0 = time.time()
            self.logger.info(
                f"[{self.ats.name}] [BATCH] Dispatching tenants {i + 1} to {min(i + batch_size, len(self.ats.companies))} "
                f"of {len(self.ats.companies)}..."
            )
            await asyncio.gather(*(self._scrape_tenant(c_id, s, kw) for c_id, s, kw in batch))

            batch_elapsed = time.time() - batch_t0
            total_elapsed = time.time() - self.start
            self.logger.info(
                f"[{self.ats.name}] [MILESTONE] Processed {min(i + batch_size, len(self.ats.companies))}/{len(self.ats.companies)} tenants "
                f"(batch: {batch_elapsed:.1f}s, total: {total_elapsed:.0f}s) | "
                f"Counts: {self.counts.success} OK, {self.counts.not_found} 404, {self.counts.error} ERR | "
                f"Jobs queued: {self.counts.jobs_queued:,} (dupes dropped: {self.counts.jobs_deduped:,})"
            )

    async def _check_yield_or_exit(self, i: int) -> bool:
        if self.cfg.is_paused(self.ats.name):
            self.logger.info(
                f"[{self.ats.name}] Paused via config at tenant {i}/{len(self.ats.companies)}. Releasing semaphore slot..."
            )
            self.priority_semaphore.release()

            while self.cfg.get(self.ats.name).get("paused", False):
                await asyncio.sleep(10)

            current_tier = self.cfg.get(self.ats.name).get("tier", self.ats.tier)
            self.logger.info(
                f"[{self.ats.name}] Resuming. Re-acquiring semaphore slot with Tier {current_tier}..."
            )
            await self.priority_semaphore.acquire(current_tier)

        # 2. LIVE YIELD / DEMOTE CHECK
        elif self.cfg.is_yielded(self.ats.name):
            current_tier = self.cfg.get(self.ats.name).get("tier", 99)
            self.logger.info(
                f"[{self.ats.name}] Yielding semaphore slot at tenant {i}/{len(self.ats.companies)} to re-queue at Tier {current_tier}..."
            )
            self.priority_semaphore.release()

            # Reset yield trigger in memory/config
            self.cfg.get(self.ats.name)["yield_slot"] = False

            # Re-enters priority heap; lower tiers will execute first
            await self.priority_semaphore.acquire(current_tier)

        # 3. LIVE DISABLE CHECK
        if not self.cfg.is_enabled(self.ats.name):
            self.logger.warning(
                f"[{self.ats.name}] Scraper disabled mid-run. Aborting at tenant {i}/{len(self.ats.companies)}."
            )
            return True

        return False

    async def _scrape_tenant(self, company_id: int, slug: str, kw: dict[str, Any]) -> None:
        active_tenant_delay = float(self.cfg.get(self.ats.name).get("tenant_delay_seconds", 0))

        started = time.time()
        jobs: list[Job] = []
        scraper = None
        err = None

        async with self.sem:
            try:
                _, scraper, jobs, err = await self._run_scraper(
                    self.per_ats_cfg_copy["scraper"],
                    slug,
                    kw,
                    self.timeout,
                    include_descriptions=not bool(self.per_ats_cfg_copy.get("defer_descriptions_to_cache")),
                )
            except ValidationError as e:
                err = f"ValidationError: {e.errors()}"
            except Exception as e:
                err = f"Unhandled {type(e).__name__}: {str(e)[:150]}"
            finally:
                if active_tenant_delay:
                    await asyncio.sleep(active_tenant_delay)

        async with self.incr_lock:
            self.tenants_completed += 1

        elapsed = time.time() - started
        duration_ms = int(elapsed * 1000)
        is_success = err is None and err != "not_found"

        async with self.incr_lock:
            if err == "not_found":
                self.counts.not_found += 1
                self.logger.warning(
                    f"  [{self.ats.name}] [{self.tenants_completed}/{len(self.ats.companies)}] 404 Not Found: '{slug}' ({elapsed:.1f}s)"
                )
            elif err:
                self.counts.error += 1
                self.logger.error(
                    f"  [{self.ats.name}] [{self.tenants_completed}/{len(self.ats.companies)}] FAILED: '{slug}' after {elapsed:.1f}s -> {err}"
                )
            else:
                self.counts.success += 1

        await self.company_repo.update_company_stats(is_success, duration_ms, err, len(jobs), company_id)

        tenant_desc_stats = DescCounts()
        tenant_queued = 0
        tenant_deduped = 0

        if is_success:
            for job in jobs:
                self.counts.jobs_scraped += 1
                key = self._job_dedupe_key(job)
                if key in self.seen_keys:
                    self.counts.jobs_deduped += 1
                    tenant_deduped += 1
                    continue
                self.seen_keys.add(key)

                if scraper is not None and not self.per_ats_cfg_copy.get("skip_description_enrichment"):
                    if self.description_cache.get(job) or job.description:
                        await self._ensure_description(job, tenant_desc_stats)
                    else:
                        async with self.description_sem:
                            try:
                                await self._ensure_description(job, tenant_desc_stats)
                            finally:
                                if self.description_delay:
                                    await asyncio.sleep(self.description_delay)

                db_job = JobDB.from_domain(company_id, job)
                await self.db_queue.put(db_job)
                self.counts.jobs_queued += 1
                tenant_queued += 1


        if self.ui_queue:
            self.ui_queue.put_nowait(
                {
                    "type": "progress",
                    "ats": self.ats.name,
                    "current": self.tenants_completed,
                    "total": len(self.ats.companies),
                    "slug": slug,
                    "found": len(jobs),
                    "queued": tenant_queued,
                    "dupes": tenant_deduped,
                }
            )

        is_slow = elapsed >= float(self.per_ats_cfg_copy.get("slow_tenant_log_seconds", 300))
        tag = "SLOW TENANT" if is_slow else "OK"
        self.logger.info(
            f"  [{self.ats.name}] [{self.tenants_completed}/{len(self.ats.companies)}] [{tag}] '{slug}' in {elapsed:.1f}s: "
            f"{len(jobs)} found -> {tenant_queued} queued, {tenant_deduped} dupes "
            f"(desc: {tenant_desc_stats['fetched']} fetched, {tenant_desc_stats['cache']} cached, "
            f"{tenant_desc_stats['present']} present, {tenant_desc_stats['missing']} missing)"
        )

    async def _run_scraper(self,
        scraper_cls: Any,
        slug: str,
        kwargs: dict[str, Any] | None = None,
        timeout: float = 30,
        *,
        include_descriptions: bool = True,
    ) -> tuple[str, BaseScraper | None, list[Job], str | None]:
        extra = kwargs or {}

        def _run() -> tuple[BaseScraper, list[Job]]:
            scraper = scraper_cls(slug, timeout=timeout, **extra)
            scraper.include_descriptions = include_descriptions
            return scraper, scraper.fetch()

        try:
            scraper, jobs = await asyncio.to_thread(_run)
            return slug, scraper, jobs, None
        except CompanyNotFoundError:
            return slug, None, [], "not_found"
        except Exception as exc:
            return slug, None, [], f"{type(exc).__name__}: {str(exc)[:120]}"


    async def _write_streamed_job(self, company_id: int, job: Job):
        db_job = JobDB.from_domain(company_id, job)
        await self.db_queue.put(db_job)

        self.counts.jobs_scraped += 1
        self.counts.jobs_queued += 1

        if self.ui_queue:
            self.ui_queue.put_nowait({
                            "type": "progress",
                            "ats": self.ats.name,
                            "current": self.counts.jobs_queued,
                            "total": self.counts.jobs_queued,
                            "slug": "streaming...",
                            "found": self.counts.jobs_scraped,
                            "queued": self.counts.jobs_queued,
                            "dupes": self.counts.jobs_deduped,
                        })

        if self.counts.jobs_queued % 5_000 == 0:
            elapsed = time.time() - self.start
            rate = self.counts.jobs_scraped / max(1.0, elapsed)
            self.logger.info(
                f"  [{self.ats.name}] [STREAM PROGRESS] {self.counts.jobs_queued:,} jobs queued "
                f"in {elapsed:.0f}s ({rate:.1f} jobs/s) | Pending tasks: {len(self.pending_descriptions)}"
            )

    async def _drain_description_tasks(self, *, all_tasks: bool = False) -> None:
        if not self.pending_descriptions:
            return
        done, _ = await asyncio.wait(
            self.pending_descriptions,
            return_when=(asyncio.ALL_COMPLETED if all_tasks else asyncio.FIRST_COMPLETED),
        )
        for task in done:
            company_id, job = task.result()
            await self._write_streamed_job(company_id, job)

    async def _ensure_description(self, job: Job, tenant_desc_counts: DescCounts | None = None):
        cached = self.description_cache.get(job)
        fresh = job.description

        async with self.incr_lock:
            if cached:
                if not fresh or len(cached) > len(fresh):
                    job.description = cached

                    self.desc_counts.cache += 1
                    if tenant_desc_counts:
                        tenant_desc_counts.cache += 1

                    return

                self.description_cache.set(job, fresh)

                self.desc_counts.present += 1
                if tenant_desc_counts:
                    tenant_desc_counts.present += 1

                return
            if fresh:
                self.desc_counts.present += 1
                if tenant_desc_counts:
                    tenant_desc_counts.present += 1
                return

        try:
            description = await asyncio.to_thread(self.scraper.get_description, job)
        except Exception as exc:
            self.logger.error(
                f"  description fetch failed for {job.url}: {type(exc).__name__}: {str(exc)[:200]}"
            )
            async with self.incr_lock:
                self.desc_counts.error += 1
                if tenant_desc_counts:
                    tenant_desc_counts.error += 1

                return

        async with self.incr_lock:
            if description:
                job.description = description[:25_000]
                self.description_cache.set(job, job.description)
                self.desc_counts.fetched += 1
                if tenant_desc_counts:
                    tenant_desc_counts.fetched += 1
                return

            self.desc_counts.missing += 1
            if tenant_desc_counts:
                tenant_desc_counts.missing += 1

    async def _enrich_missing_stream_description(self, company_id: int, job: Job) -> Tuple[int, Job]:
        await self._ensure_description(job)
        return company_id, job

    def _job_dedupe_key(self, job: Job) -> tuple[str, str]:
        if self.per_ats_cfg_copy.get("dedupe_by_url"):
            parsed = urlparse(str(job.url))
            canonical_url = (
                f"{(parsed.hostname or '').casefold()}{parsed.path.rstrip('/').casefold()}"
            )
            return "", canonical_url
        ats_id = job.ats_id or ""
        if self.per_ats_cfg_copy.get("dedupe_by_ats_id"):
            return "", ats_id
        return job.company, ats_id
