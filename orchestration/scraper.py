import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import asyncpg
from rich.live import Live

from src.scraping.configuration_manager import DynamicConfigManager
from src.scraping.database.base import ATS, CompanyRepository
from src.scraping.database.postgres import (
    CompanyRepositoryPostgres,
    DescriptionCachePostgres,
    JobRepositoryPostgres,
)
from src.scraping.pipeline_engine.engine import RunEngine
from src.scraping.ui.cli import ATSState, Dashboard

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

async def ui_worker(ui_queue: asyncio.Queue, dashboard: Dashboard, live: Live) -> None:
    while True:
        try:
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

            live.update(dashboard.generate_layout(), refresh=True)
        except Exception as exc:
            logger.error(f"[UI Worker] Render error: {exc}", exc_info=True)
        finally:
            ui_queue.task_done()


async def get_active_ats_platforms(company_repo: CompanyRepository, cfg: DynamicConfigManager) -> List[ATS]:
        """
        Retrieves ATS platforms and orders them by tier.
        Increases effectiveness of scraping by fetching more relevant jobs from better ATSs first.
        E.g., Ashby, Greenhouse - Tier 1 (high technical job density), Breezy - Tier 2,  Workday - 3 (usually the longest running tenant, a lot of non technical jobs: nurses, drives, etc.)
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

        ui_queue: asyncio.Queue = asyncio.Queue()
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
                max_concurrent_ats=5
            )
            await engine.run(ats_list)

            await ui_queue.put(None)
            await ui_task


if __name__ == "__main__":
    asyncio.run(main_loop())
