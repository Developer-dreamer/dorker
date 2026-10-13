import asyncio
import atexit
import logging
import os
import signal
import threading
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
from queue import Queue
from typing import Any, Dict, List

import asyncpg
import logging_loki
import pyroscope
from pythonjsonlogger.json import JsonFormatter
from requests import Session
from rich.live import Live

from src.scraping.configuration_manager import DynamicConfigManager
from src.scraping.database.base import CompanyRepository
from src.scraping.database.postgres import (
    CompanyRepositoryPostgres,
    DescriptionCachePostgres,
    JobRepositoryPostgres,
)
from src.scraping.pipeline_engine.engine import RunEngine
from src.scraping.ui.cli import ATSState, Dashboard
from src.shared.models.company import ATS

ROOT = Path(__file__).resolve().parent.parent

DB_PATH = ROOT / "cache" / "descriptions.db"
SLEEP_INTERVAL_HOURS = 6
PG_DSN = "postgresql://postgres:password@localhost:5432/dorker_db"

LOG_PATH = ROOT / "logs" / f"scraper_{datetime.now(timezone.utc).isoformat()}.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

# Configure file-only logging
logging.basicConfig(
    filename=LOG_PATH,
    filemode="a",
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.WARN,
)
logger = logging.getLogger("dorker.scraper")
logger.setLevel(logging.INFO)

now_utc = datetime.now(timezone.utc)
# Format to hh:mm:ss-dd/mm/yy
formatted_date = now_utc.strftime("%H:%M:%S-%d/%m/%y")

RUN_ID = formatted_date
telemetry_tags = {
    "app": "dorker.scraper",
    "environment": "production",
    "run_id": RUN_ID,
}
profiling_logger = logging.getLogger("pyroscope")
pyroscope.configure(
    application_name="dorker.scraper",
    server_address="https://profiles-prod-008.grafana.net",
    basic_auth_username="1503682",
    basic_auth_password=os.getenv("GRAPHANA_API_KEY"),
    tags=telemetry_tags.copy(),
    # Native SDK diagnostics write directly to the terminal, bypassing Python
    # handlers and disrupting Rich's live dashboard. Profiling stays enabled.
    enable_logging=False,
    cpu_enabled=True,  # keep CPU profiling on (default True)
    mem_enabled=True,  # enable memory (allocation) profiling
    mem_max_nframe=128,  # max stack frames per sampled allocation (default)
    mem_heap_sample_size=512 * 1024,  # avg bytes between samples (default)
)


class FileReportingLokiHandler(logging_loki.LokiHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        # Do not call the default handler: it prints tracebacks over Rich's UI.
        self.emitter.close()
        logging.getLogger("dorker.telemetry").warning(
            "Loki upload failed; remote delivery of this record is unconfirmed.",
            exc_info=True,
        )


remote_log_handler = FileReportingLokiHandler(
    url="https://logs-prod-021.grafana.net/loki/api/v1/push",
    tags=telemetry_tags.copy(),
    auth=("1461472", os.getenv("GRAPHANA_API_KEY")),
    version="1",
)
loki_handler = QueueHandler(Queue(-1))
loki_listener = QueueListener(loki_handler.queue, remote_log_handler)
json_formatter = JsonFormatter(
    "%(asctime)s %(levelname)s %(name)s %(message)s",
    rename_fields={"levelname": "level", "asctime": "timestamp"},  # Optional: clean up key names
)
loki_handler.setFormatter(json_formatter)
logger.addHandler(loki_handler)
profiling_logger.addHandler(loki_handler)


class TelemetrySession(Session):
    def request(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", (3, 5))
        return super().request(*args, **kwargs)


remote_log_handler.emitter.session_class = TelemetrySession
loki_listener.start()


def shutdown_telemetry() -> None:
    # Native profiler shutdown and remote logging must not hold process exit forever.
    profiler = threading.Thread(target=pyroscope.shutdown, daemon=True)
    profiler.start()
    profiler.join(timeout=10)
    logger.removeHandler(loki_handler)
    profiling_logger.removeHandler(loki_handler)
    listener = loki_listener
    listener.enqueue_sentinel()
    if listener._thread is not None:
        listener._thread.join(timeout=10)


atexit.register(shutdown_telemetry)
print(f"Application configured with Run ID set to: {RUN_ID}")


async def ui_worker(
    ui_queue: asyncio.Queue[Dict[str, Any] | None], dashboard: Dashboard, live: Live
) -> None:
    while True:
        msg = await ui_queue.get()
        try:
            if msg is None:
                break

            msg_type = msg.get("type")
            ats = msg.get("ats") or ""

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
                    state.deleted = msg.get("deleted", 0)
            elif msg_type == "finish":
                if ats in dashboard.working:
                    del dashboard.working[ats]
                if ats not in dashboard.finished:
                    dashboard.finished.append(ats)

            live.update(dashboard.generate_layout(), refresh=True)
        except Exception as exc:
            logger.error(f"[UI Worker] Render error: {exc}", exc_info=True)
        finally:
            ui_queue.task_done()


async def get_active_ats_platforms(
    company_repo: CompanyRepository, cfg: DynamicConfigManager
) -> List[ATS]:
    """
    Retrieves ATS platforms and orders them by tier.
    Increases effectiveness of scraping by fetching more relevant jobs from better ATSs first.
    E.g., Ashby, Greenhouse - Tier 1 (high technical job density), Breezy - Tier 2,
    Workday - 3 (usually the longest running tenant,
    a lot of non technical jobs: nurses, drives, etc.)
    """
    db_ats = await company_repo.get_tenants()
    cfg_ats = cfg.keys()

    seen: set[ATS] = set()
    result: list[ATS] = []

    for ats in db_ats:
        if ats not in seen and (ats.name in cfg_ats or cfg[ats.name].get("singleton")):
            seen.add(ats)
            result.append(ats)

    result.sort(key=lambda x: x.tier)
    return result


async def main_loop() -> None:
    cfg_path = Path(__file__).resolve().parent.parent / "configs" / "scraper.json"
    cfg = DynamicConfigManager(logger, cfg_path)

    async with asyncpg.create_pool(PG_DSN) as pool:
        job_repo = JobRepositoryPostgres(logger, pool)
        company_repo = CompanyRepositoryPostgres(logger, pool)
        description_cache = DescriptionCachePostgres(logger, pool)

        ats_list = await get_active_ats_platforms(company_repo, cfg)

        ui_queue: asyncio.Queue[Dict[str, Any] | None] = asyncio.Queue()
        dashboard = Dashboard([ats.name for ats in ats_list])

        with Live(dashboard.generate_layout(), refresh_per_second=4) as live:
            ui_task = asyncio.create_task(ui_worker(ui_queue, dashboard, live))

            engine = RunEngine(
                logger,
                cfg,
                pool,
                job_repo,
                company_repo,
                description_cache,
                ui_queue,
                max_concurrent_ats=5,
            )
            try:
                await engine.run(ats_list)
            finally:
                await ui_queue.put(None)
                await ui_task
                await ui_queue.join()


async def run_scraper() -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    stopping = False

    def request_shutdown() -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        print("Stopping scraper; finishing queued writes and cleanup...")
        if task is not None:
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, request_shutdown)
    try:
        await main_loop()
    except asyncio.CancelledError:
        if not stopping:
            raise
        logger.info("Scraper stopped by user.")
        print("Scraper stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(run_scraper())
    except KeyboardInterrupt:
        logger.info("Scraper stopped by user.")
        print("Scraper stopped.")
