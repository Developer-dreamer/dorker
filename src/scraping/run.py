#!/usr/bin/env python3
"""Generic pipeline runner: scrape every tenant of an ATS and write one CSV.

Used by ``full_pipeline.sh`` for ATSes that don't have a legacy
``data/{ats}/main.py`` — scrapers that live only in ats-scrapers.

Reads ``ats-companies/{ats}.csv`` (the canonical tenant list — single
source of truth, columns ``name,url``), scrapes each tenant via the
appropriate ats-scrapers scraper class, dedupes, and writes a flat
``{repo}/{ats}/jobs.csv`` by default. Set ``ATS_SCRAPERS_JOBS_ROOT`` (or
the legacy ``JOBHIVE_JOBS_ROOT``) to write into a separate publication tree.

Usage:
    python scripts/run_pipeline.py cornerstone
    python scripts/run_pipeline.py icims --concurrency 6
    python scripts/run_pipeline.py breezy --max-tenants 50
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from pydantic import ValidationError

from .base import BaseScraper
from .configuration_manager import CONFIGS
from .exceptions import CompanyNotFoundError
from .models import Job

logger = logging.getLogger(__name__)
DATA_ROOT = Path(__file__).resolve().parent.parent  # → repo root


def _jobs_output_root() -> Path:
    configured = os.environ.get("ATS_SCRAPERS_JOBS_ROOT") or os.environ.get(
        "JOBHIVE_JOBS_ROOT"
    )
    return Path(configured).expanduser() if configured else DATA_ROOT


JOB_CSV_FIELDS = [
    "url", "title", "company", "ats_type", "ats_id", "location",
    "country_iso", "region", "language", "lat", "lon",
    "is_remote", "salary_min", "salary_max", "salary_currency",
    "salary_period", "salary_summary", "employment_type",
    "department", "team", "description", "posted_at",
    "requisition_id", "apply_url", "commitment", "raw",
]
STREAM_DESCRIPTION_CONCURRENCY = 8


@contextmanager
def _pipeline_lock(ats: str) -> Iterator[bool]:
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
            logger.warning(f"[{ats}] another run is already active ({owner}); skipping.")
            yield False
            return
        fh.seek(0)
        fh.truncate()
        fh.write(f"pid={os.getpid()} started_at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
        fh.flush()
        try:
            yield True
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


_CACHE_SCHEMA_VERSION = 2


class DescriptionCache:
    """Disk-backed description cache, optionally persistent and zstd-compressed."""
    def __init__(self, db_path: Path, ats_name: str, compress: bool = False) -> None:
        self.conn: sqlite3.Connection | None = None
        self.compress = compress
        self._compressor = None
        self._decompressor = None
        if compress:
            import zstandard
            self._compressor = zstandard.ZstdCompressor(level=3)
            self._decompressor = zstandard.ZstdDecompressor()

        with tempfile.NamedTemporaryFile(
            prefix="ats-scrapers-description-cache-",
            suffix=".sqlite3",
            delete=False,
        ) as tmp:
            self.path = Path(tmp.name)
        self._owns_tempfile = True

        try:
            self.conn = sqlite3.connect(self.path)
            if self._owns_tempfile:
                self.conn.execute("PRAGMA journal_mode=OFF")
                self.conn.execute("PRAGMA synchronous=OFF")
            else:
                self.conn.execute("PRAGMA journal_mode=WAL")
                self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA temp_store=MEMORY")

            current_user_version = self.conn.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            existing_rows = 0
            existing_table = self.conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='descriptions'"
            ).fetchone()
            if existing_table is not None:
                existing_rows = self.conn.execute(
                    "SELECT COUNT(*) FROM descriptions"
                ).fetchone()[0]
            if existing_rows > 0 and current_user_version != _CACHE_SCHEMA_VERSION:
                if self.conn is not None:
                    self.conn.close()
                    self.conn = None
                if self._owns_tempfile:
                    self.path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"DescriptionCache schema mismatch at {self.path}: "
                    f"file user_version={current_user_version}, "
                    f"code expects {_CACHE_SCHEMA_VERSION}. Delete the "
                    f"file and let the pipeline reseed it from the "
                    f"current jobs.csv (or run scripts/build_workday_cache "
                    f"if a backfill seed is available)."
                )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS descriptions (
                    kind TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    description BLOB NOT NULL,
                    PRIMARY KEY (kind, cache_key)
                )
                """
            )
            self.conn.execute(
                f"PRAGMA user_version = {_CACHE_SCHEMA_VERSION}"
            )
            self.count = self.conn.execute(
                "SELECT COUNT(*) FROM descriptions"
            ).fetchone()[0]

            self._load_sql(db_path, ats_name)
        except Exception:
            if self.conn is not None:
                self.conn.close()
            if self._owns_tempfile:
                self.path.unlink(missing_ok=True)
            raise

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        if self._owns_tempfile:
            self.path.unlink(missing_ok=True)

    def _encode(self, description: str) -> bytes:
        raw = description.encode("utf-8")
        return self._compressor.compress(raw) if self.compress else raw

    def _decode(self, blob: bytes) -> str:
        raw = self._decompressor.decompress(blob) if self.compress else blob
        return raw.decode("utf-8")

    def _load_sql(self, db_path: Path, ats_name: str) -> None:
        batch: list[tuple[str, str, bytes]] = []
        uri = f"{db_path.resolve().as_uri()}?mode=ro"

        try:
            with sqlite3.connect(uri, uri=True) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    """
                    SELECT ats_id, ats_type, company_slug AS company, url, description
                    FROM jobs
                    WHERE ats_name = ?
                    AND description IS NOT NULL
                    AND description != ''
                    """,
                    (ats_name,)
                )
                for raw_row in cursor:
                    row = dict(raw_row)
                    description = row["description"].strip()
                    if not description:
                        continue

                    blob = self._encode(description)
                    for key in _row_description_keys(row):
                        batch.append((*key, blob))
                    if len(batch) >= 2_000:
                        self._insert_many(batch)
                        batch.clear()
                if batch:
                    self._insert_many(batch)
        except (OSError, sqlite3.Error):
            self.conn.execute("DELETE FROM descriptions")
            self.conn.commit()

        self.count = self.conn.execute(
            "SELECT COUNT(*) FROM descriptions"
        ).fetchone()[0]

    def _insert_many(self, rows: list[tuple[str, str, bytes]], *, replace: bool = False) -> int:
        verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
        cur = self.conn.executemany(
            f"""
            {verb} INTO descriptions (kind, cache_key, description)
            VALUES (?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()
        return cur.rowcount

    def get(self, job: Job) -> str | None:
        for kind, key in _description_keys(job):
            row = self.conn.execute(
                """
                SELECT description FROM descriptions
                WHERE kind = ? AND cache_key = ?
                """,
                (kind, key),
            ).fetchone()
            if row:
                logger.debug(f"[{job.ats_type}] [INFO] Cache HIT ")
                return self._decode(row[0])
        return None

    def set(self, job: Job, description: str) -> None:
        blob = self._encode(description)
        rows = [(*key, blob) for key in _description_keys(job)]
        if not rows:
            return
        new_keys = 0
        existing = self.conn.execute(
            "SELECT COUNT(*) FROM descriptions WHERE (kind, cache_key) IN ("
            + ",".join("(?,?)" for _ in rows)
            + ")",
            [v for kind, key, _ in rows for v in (kind, key)],
        ).fetchone()[0]
        new_keys = len(rows) - existing
        self._insert_many(rows, replace=True)
        self.count += max(0, new_keys)


def _description_keys(job: Job) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    url = str(job.url).strip()
    if job.ats_type.value == "icims":
        return [("url", url)] if url else []
    company = (job.company or "").strip()
    ats_id = (job.ats_id or "").strip()
    if company and ats_id:
        keys.append(("company_ats_id", f"{company}\0{ats_id}"))
    if url:
        keys.append(("url", url))
    return keys


def _job_dedupe_key(
    job: Job,
    config: dict[str, Any],
) -> tuple[str, str]:
    if config.get("dedupe_by_url"):
        parsed = urlparse(str(job.url))
        canonical_url = (
            f"{(parsed.hostname or '').casefold()}"
            f"{parsed.path.rstrip('/').casefold()}"
        )
        return "", canonical_url
    ats_id = job.ats_id or ""
    if config.get("dedupe_by_ats_id"):
        return "", ats_id
    return job.company, ats_id


def _row_description_keys(row: dict[str, str]) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    url = (row.get("url") or "").strip()
    if (row.get("ats_type") or "").strip().casefold() == "icims":
        return [("url", url)] if url else []
    company = (row.get("company") or "").strip()
    ats_id = (row.get("ats_id") or "").strip()
    if company and ats_id:
        keys.append(("company_ats_id", f"{company}\0{ats_id}"))
    if url:
        keys.append(("url", url))
    return keys


def _cached_description(job: Job, cache: DescriptionCache) -> str | None:
    return cache.get(job)


async def _ensure_description(
    scraper: BaseScraper,
    job: Job,
    cache: DescriptionCache,
) -> str:
    cached = _cached_description(job, cache)
    fresh = job.description
    if cached:
        if not fresh or len(cached) > len(fresh):
            job.description = cached
            return "cache"
        cache.set(job, fresh)
        return "present"
    if fresh:
        return "present"
    try:
        description = await asyncio.to_thread(scraper.get_description, job)
    except Exception as exc:
        logger.error(
            f"  description fetch failed for {job.url}: {type(exc).__name__}: {str(exc)[:200]}"
        )
        return "error"
    if description:
        job.description = description[:25_000]
        cache.set(job, job.description)
        return "fetched"
    return "missing"


def _bounded_concurrency(cfg: dict[str, Any], requested: int) -> int:
    if requested < 1:
        raise ValueError("concurrency must be at least 1")
    configured = cfg.get("max_concurrency")
    if isinstance(configured, int) and configured > 0:
        return min(requested, configured)
    return requested


async def _run_scraper(
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


async def run(
    ats: str,
    db_path: Path,
    queue: asyncio.Queue[Job | None],
    concurrency: int,
    max_tenants: int | None,
    timeout: float,
    ui_queue: asyncio.Queue | None = None,
) -> int:
    cfg = CONFIGS[ats]
    concurrency = _bounded_concurrency(cfg, concurrency)
    configured_max_concurrency = cfg.get("max_concurrency")
    if isinstance(configured_max_concurrency, int):
        concurrency = min(concurrency, max(1, configured_max_concurrency))

    targets: list[tuple[str, dict[str, Any]]] = []
    if cfg.get("singleton"):
        targets = [(ats, {})]
    else:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT ats_name, company_slug as slug, company_name as name, url, tier FROM ats WHERE ats_name = ?",
                (ats,),
            ).fetchall()

        if not rows:
            logger.warning(f"[{ats}] [WARN] No active tenants found in SQLite ({db_path}); skipping run.")
            return 0

        kwargs_factory = cfg.get("kwargs")
        for r in rows:
            row_dict = dict(r)
            slug = cfg["slug"](row_dict)
            if slug:
                kw = kwargs_factory(row_dict) if kwargs_factory else {}
                targets.append((slug, kw))

    configured_target_count = len(targets)
    omitted_required_shards = 0
    if max_tenants:
        targets = targets[:max_tenants]
        if cfg.get("fail_closed_on_any_error"):
            omitted_required_shards = configured_target_count - len(targets)

    logger.info(
        f"[{ats}] [INFO] Starting pipeline: {len(targets)} targets "
        f"(concurrency={concurrency}, desc_concurrency={cfg.get('description_concurrency', concurrency)}, "
        f"timeout={timeout}s, singleton={bool(cfg.get('singleton'))})"
    )

    sem = asyncio.Semaphore(concurrency)
    description_concurrency = cfg.get("description_concurrency", concurrency)
    if not isinstance(description_concurrency, int):
        description_concurrency = concurrency
    description_sem = asyncio.Semaphore(max(1, description_concurrency))
    tenant_delay = float(cfg.get("tenant_delay_seconds", 0))
    description_delay = float(cfg.get("description_delay_seconds", 0))

    counts = {
        "success": 0,
        "not_found": 0,
        "error": 0,
        "jobs_scraped": 0,
        "jobs_queued": 0,
        "jobs_deduped": 0,
    }
    total_desc_stats = {
        "cache": 0,
        "present": 0,
        "fetched": 0,
        "missing": 0,
        "error": 0,
    }

    jobs_output_root = _jobs_output_root()
    uses_streaming = bool(cfg.get("singleton") and hasattr(cfg["scraper"], "fetch_stream"))
    persistent_path_rel = cfg.get("description_cache_path")
    persistent_path = (
        jobs_output_root / persistent_path_rel if persistent_path_rel else None
    )
    cache_compress = bool(cfg.get("description_cache_compress"))

    logger.info(f"[{ats}] [CACHE] Initializing description cache from {db_path}...")
    description_cache = DescriptionCache(db_path, ats, compress=cache_compress)
    if description_cache.count:
        location = "persistent" if persistent_path else "ephemeral"
        logger.info(
            f"[{ats}] [CACHE] Loaded {description_cache.count:,} warm description keys "
            f"({location} cache at {description_cache.path})"
        )
    else:
        logger.info(f"[{ats}] [CACHE] Description cache initialized empty.")

    seen_keys: set[tuple[str, str]] = set()
    tenants_completed = 0
    t0 = time.time()

    try:
        if uses_streaming:
            logger.info(f"[{ats}] [STREAM] Executing streaming scraper...")
            scraper = cfg["scraper"](ats, timeout=timeout)
            pending_descriptions: set[asyncio.Task[Job]] = set()

            async def write_streamed_job(job: Job) -> None:
                await queue.put(job)
                counts["jobs_scraped"] += 1
                counts["jobs_queued"] += 1

                if ui_queue:
                    ui_queue.put_nowait({
                        "type": "progress",
                        "ats": ats,
                        "current": counts["jobs_queued"],
                        "total": counts["jobs_queued"],
                        "slug": "streaming...",
                        "found": counts["jobs_scraped"],
                        "queued": counts["jobs_queued"],
                        "dupes": counts["jobs_deduped"]
                    })

                if counts["jobs_queued"] % 5_000 == 0:
                    elapsed = time.time() - t0
                    rate = counts["jobs_queued"] / max(1.0, elapsed)
                    logger.info(
                        f"  [{ats}] [STREAM PROGRESS] {counts['jobs_queued']:,} jobs queued "
                        f"in {elapsed:.0f}s ({rate:.1f} jobs/s) | Pending tasks: {len(pending_descriptions)}"
                    )

            async def enrich_missing_stream_description(job: Job) -> Job:
                await _ensure_description(scraper, job, description_cache)
                return job

            async def drain_description_tasks(*, all_tasks: bool = False) -> None:
                nonlocal pending_descriptions
                if not pending_descriptions:
                    return
                done, pending_descriptions = await asyncio.wait(
                    pending_descriptions,
                    return_when=(
                        asyncio.ALL_COMPLETED if all_tasks else asyncio.FIRST_COMPLETED
                    ),
                )
                for task in done:
                    await write_streamed_job(task.result())

            try:
                async for job in scraper.fetch_stream():
                    cached = _cached_description(job, description_cache)
                    if cached:
                        job.description = cached
                        total_desc_stats["cache"] += 1
                        await write_streamed_job(job)
                    elif job.description:
                        total_desc_stats["present"] += 1
                        await write_streamed_job(job)
                    else:
                        pending_descriptions.add(
                            asyncio.create_task(enrich_missing_stream_description(job))
                        )
                        if len(pending_descriptions) >= STREAM_DESCRIPTION_CONCURRENCY:
                            await drain_description_tasks()

                await drain_description_tasks(all_tasks=True)
                counts["success"] = 1

            except CompanyNotFoundError:
                for task in pending_descriptions:
                    task.cancel()
                counts["not_found"] = 1
                logger.warning(f"[{ats}] [WARN] Streaming target company not found.")
            except Exception as exc:
                for task in pending_descriptions:
                    task.cancel()
                counts["error"] = 1
                logger.error(f"[{ats}] [ERROR] Streaming failed: {type(exc).__name__}: {str(exc)[:300]}")

        else:
            async def scrape_tenant(slug: str, kw: dict[str, Any]) -> None:
                nonlocal tenants_completed

                if not CONFIGS.is_enabled(ats):
                    logger.warning(f"[{ats}] Scraper disabled mid-run via ats_config.json. Skipping tenant {slug}.")
                    return

                current_cfg = CONFIGS.get(ats)
                active_tenant_delay = float(current_cfg.get("tenant_delay_seconds", 0))

                started = time.time()
                jobs: list[Job] = []
                scraper = None
                err = None

                async with sem:
                    try:
                        _, scraper, jobs, err = await _run_scraper(
                            cfg["scraper"],
                            slug,
                            kw,
                            timeout,
                            include_descriptions=not bool(
                                cfg.get("defer_descriptions_to_cache")
                            ),
                        )
                    except ValidationError as e:
                        err = f"ValidationError: {e.errors()}"
                    except Exception as e:
                        err = f"Unhandled {type(e).__name__}: {str(e)[:150]}"
                    finally:
                        if active_tenant_delay:
                            await asyncio.sleep(active_tenant_delay)

                elapsed = time.time() - started
                tenants_completed += 1

                if err == "not_found":
                    counts["not_found"] += 1
                    logger.warning(f"  [{ats}] [{tenants_completed}/{len(targets)}] 404 Not Found: '{slug}' ({elapsed:.1f}s)")
                elif err:
                    counts["error"] += 1
                    logger.error(f"  [{ats}] [{tenants_completed}/{len(targets)}] FAILED: '{slug}' after {elapsed:.1f}s -> {err}")
                else:
                    counts["success"] += 1

                tenant_desc_stats = {
                    "cache": 0, "present": 0, "fetched": 0, "missing": 0, "error": 0,
                }
                tenant_queued = 0
                tenant_deduped = 0

                if not err and err != "not_found":
                    for job in jobs:
                        counts["jobs_scraped"] += 1
                        key = _job_dedupe_key(job, cfg)
                        if key in seen_keys:
                            counts["jobs_deduped"] += 1
                            tenant_deduped += 1
                            continue
                        seen_keys.add(key)

                        if scraper is not None and not cfg.get("skip_description_enrichment"):
                            if _cached_description(job, description_cache) or job.description:
                                desc_status = await _ensure_description(
                                    scraper, job, description_cache
                                )
                            else:
                                async with description_sem:
                                    try:
                                        desc_status = await _ensure_description(
                                            scraper, job, description_cache
                                        )
                                    finally:
                                        if description_delay:
                                            await asyncio.sleep(description_delay)
                            tenant_desc_stats[desc_status] += 1
                            total_desc_stats[desc_status] += 1

                        await queue.put(job)
                        counts["jobs_queued"] += 1
                        tenant_queued += 1

                if ui_queue:
                    ui_queue.put_nowait({
                        "type": "progress",
                        "ats": ats,
                        "current": tenants_completed,
                        "total": len(targets),
                        "slug": slug,
                        "found": len(jobs),
                        "queued": tenant_queued,
                        "dupes": tenant_deduped
                    })

                is_slow = elapsed >= float(cfg.get("slow_tenant_log_seconds", 300))
                tag = "SLOW TENANT" if is_slow else "OK"
                logger.info(
                    f"  [{ats}] [{tenants_completed}/{len(targets)}] [{tag}] '{slug}' in {elapsed:.1f}s: "
                    f"{len(jobs)} found -> {tenant_queued} queued, {tenant_deduped} dupes "
                    f"(desc: {tenant_desc_stats['fetched']} fetched, {tenant_desc_stats['cache']} cached, "
                    f"{tenant_desc_stats['present']} present, {tenant_desc_stats['missing']} missing)"
                )

            batch_size = 50
            for i in range(0, len(targets), batch_size):
                batch = targets[i:i + batch_size]
                batch_t0 = time.time()
                logger.info(
                    f"[{ats}] [BATCH] Dispatching tenants {i + 1} to {min(i + batch_size, len(targets))} "
                    f"of {len(targets)}..."
                )
                await asyncio.gather(*(scrape_tenant(s, kw) for s, kw in batch))
                batch_elapsed = time.time() - batch_t0
                total_elapsed = time.time() - t0
                logger.info(
                    f"[{ats}] [MILESTONE] Processed {min(i + batch_size, len(targets))}/{len(targets)} tenants "
                    f"(batch: {batch_elapsed:.1f}s, total: {total_elapsed:.0f}s) | "
                    f"Counts: {counts['success']} OK, {counts['not_found']} 404, {counts['error']} ERR | "
                    f"Jobs queued: {counts['jobs_queued']:,} (dupes dropped: {counts['jobs_deduped']:,})"
                )

        elapsed = time.time() - t0
        rate = counts["jobs_queued"] / max(1.0, elapsed)
        
        summary = (
            f"\n{'=' * 60}\n"
            f"[{ats}] RUN SUMMARY:\n"
            f"  Duration:         {elapsed:.1f}s (~{elapsed / 60:.1f} min)\n"
            f"  Tenants:          {counts['success']} success, {counts['not_found']} not found, {counts['error']} failed / {len(targets)} total\n"
            f"  Jobs Processed:   {counts['jobs_scraped']:,} scraped -> {counts['jobs_queued']:,} queued ({counts['jobs_deduped']:,} deduped)\n"
            f"  Throughput:       {rate:.1f} jobs/sec\n"
            f"  Descriptions:     {total_desc_stats['fetched']} fetched over HTTP, "
            f"{total_desc_stats['cache']} cache hits, {total_desc_stats['present']} present in payload, "
            f"{total_desc_stats['missing']} missing, {total_desc_stats['error']} failed\n"
            f"{'=' * 60}\n"
        )
        logger.info(summary)

        failure_threshold = max(1, (len(targets) + 1) // 2)
        if uses_streaming and counts["error"] > 0:
            logger.error(f"[{ats}] [FAILURE] Streaming scrape terminated with fatal error.")
            return 1

        if bool(cfg.get("fail_closed_on_empty")) and bool(targets) and counts["jobs_queued"] == 0:
            logger.error(f"[{ats}] [FAILURE] fail_closed_on_empty triggered: 0 jobs queued from {len(targets)} tenants.")
            return 1

        required_not_found = (
            counts["not_found"] if cfg.get("fail_closed_on_not_found") else 0
        )
        sharded_failure = (
            (
                bool(cfg.get("fail_closed_on_any_error"))
                and (counts["error"] > 0 or omitted_required_shards > 0)
            )
            or required_not_found > 0
        )
        if sharded_failure:
            required_failures = (
                counts["error"] + omitted_required_shards + required_not_found
            )
            logger.error(
                f"[{ats}] [FAILURE] Required shards failed: {required_failures}/{configured_target_count} "
                f"failures (errors={counts['error']}, omitted={omitted_required_shards}, not_found={required_not_found})."
            )
            return 1

        catastrophic_failure = (
            bool(targets)
            and counts["jobs_queued"] == 0
            and counts["error"] >= failure_threshold
        )
        if catastrophic_failure:
            logger.error(
                f"[{ats}] [FAILURE] Catastrophic failure: 0 jobs produced and "
                f"{counts['error']}/{len(targets)} tenant errors exceeded threshold ({failure_threshold})."
            )
            return 1

        if counts["error"] >= failure_threshold:
            logger.warning(f"[{ats}] [WARN] Kept partial data but {counts['error']}/{len(targets)} tenants failed.")
            return 1

        return 0

    finally:
        description_cache.close()