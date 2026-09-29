import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import asyncpg
import logging_loki
import pyroscope
from pythonjsonlogger.json import JsonFormatter
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
pyroscope.configure(
    application_name="dorker.scraper",
    server_address="https://profiles-prod-008.grafana.net",
    basic_auth_username="1503682",
    basic_auth_password=os.getenv("GRAPHANA_API_KEY"),
    tags={
        "environment": "development",
        "run_id": RUN_ID,
    },
    cpu_enabled=True,  # keep CPU profiling on (default True)
    mem_enabled=True,  # enable memory (allocation) profiling
    mem_max_nframe=128,  # max stack frames per sampled allocation (default)
    mem_heap_sample_size=512 * 1024,  # avg bytes between samples (default)
)

loki_tags = {
    "app": "dorker.scraper",
    "environment": "production",
    "run_id": RUN_ID,
}

loki_handler = logging_loki.LokiHandler(
    url="https://logs-prod-021.grafana.net/loki/api/v1/push",
    tags=loki_tags,
    auth=("1461472", os.getenv("GRAPHANA_API_KEY")),
    version="1",
)
json_formatter = JsonFormatter(
    "%(asctime)s %(levelname)s %(name)s %(message)s",
    rename_fields={"levelname": "level", "asctime": "timestamp"},  # Optional: clean up key names
)
loki_handler.setFormatter(json_formatter)
logger.addHandler(loki_handler)
print(f"Application configured with Run ID set to: {RUN_ID}")


async def ui_worker(
    ui_queue: asyncio.Queue[Dict[str, Any] | None], dashboard: Dashboard, live: Live
) -> None:
    while True:
        try:
            msg = await ui_queue.get()
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
            await engine.run(ats_list)

            await ui_queue.put(None)
            await ui_task
            await ui_queue.join()


if __name__ == "__main__":
    asyncio.run(main_loop())
