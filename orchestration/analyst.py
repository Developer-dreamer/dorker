import asyncio
import logging
import os
import signal
import sys
from collections.abc import Iterator
from contextlib import AsyncExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import FrameType

import asyncpg
import uvicorn
from asyncpg import Pool
from typesafe_sdk import AsyncTypeSafeClient

from src.analytics.classification import ClassifyJobTierJev, ClassifyProfileToJob
from src.analytics.discussion import DiscussionService as AnalyticsDiscussionService
from src.analytics.engine import MatchingEngine
from src.analytics.generation import OpenAIClient
from src.analytics.generation.discussion_openai import OpenAIDiscussionModel
from src.analytics.generation.summary_openai import OpenAISummaryGenerator
from src.analytics.profile_repository_xml import ProfileRepositoryXML
from src.analytics.summary_worker import SummaryWorker
from src.api import create_app
from src.api.services.application import ApplicationService
from src.api.services.discussion_adapter import DiscussionService, SafeDiscussionModel
from src.api.services.job import JobService
from src.api.services.match import MatchService
from src.database.postgres import (
    ApplicationPacketRepositoryPostgres,
    JobFactSheetRepositoryPostgres,
    JobRepositoryPostgres,
    MatchRepositoryPostgres,
)
from src.database.postgres.discussion import DiscussionRepositoryPostgres
from src.shared.models import RuntimeVersion
from src.shared.scripts.bu import LiveJobScraper

os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = "0.8"
os.environ["PYTORCH_MPS_LOW_WATERMARK_RATIO"] = "0.3"

# --- Logging Configuration ---
ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "logs" / f"analyst_{datetime.now(timezone.utc).isoformat()}.log"

logger = logging.getLogger("analyst")

PG_DSN = os.environ.get("PG_DSN", "postgresql://postgres:password@localhost:5432/dorker_db")
MODEL = "jev-1.14.0"
PIPELINE_VERSION = "0.3.1"
MIN_ACCEPTED_PIPELINE_VERSION = "0.3.0"


class AnalystServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        # run() owns signals through startup, serving, and resource cleanup.
        yield


async def define_iteration(pool: Pool) -> int:
    query = """
            SELECT COALESCE(MAX(iteration), 0)
            FROM matches
            WHERE version = $1 AND model = $2 \
            """
    async with pool.acquire() as conn:
        iteration = await conn.fetchval(query, PIPELINE_VERSION, MODEL)
        return int(iteration)


async def classify_periodically(engine: MatchingEngine, interval_seconds: float = 3600) -> None:
    """Run immediately, then wait between completed cycles so batches cannot overlap."""
    if interval_seconds <= 0:
        raise ValueError("Classification interval must be positive.")
    while True:
        try:
            await engine.classify_background()
        except Exception:
            logger.exception("Classification cycle failed; retrying after the polling interval.")
        await asyncio.sleep(interval_seconds)


async def run_services(
    engine: MatchingEngine, server: uvicorn.Server, summary_worker: SummaryWorker
) -> None:
    tasks = [
        asyncio.create_task(classify_periodically(engine), name="classification"),
        asyncio.create_task(summary_worker.run(), name="summaries"),
    ]
    server_task = asyncio.create_task(server.serve(), name="api")
    try:
        # Cancellation must request Uvicorn shutdown, not interrupt its request/lifespan cleanup.
        await asyncio.shield(server_task)
    finally:
        server.should_exit = True
        for task in tasks:
            task.cancel()
        services = [server_task, *tasks]
        results = await asyncio.gather(*services, return_exceptions=True)
        for task, result in zip(services, results, strict=True):
            if isinstance(result, Exception):
                logger.error("Service %s failed: %s", task.get_name(), result)


async def run() -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None
    stopping = False

    def request_shutdown(signum: int, _frame: FrameType | None) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        logger.info(
            "Received %s; stopping analyst services and cleaning up.", signal.Signals(signum).name
        )
        # Wake the selector as well: startup may be awaiting I/O with no active timers.
        loop.call_soon_threadsafe(task.cancel)

    previous_handlers = {
        sig: signal.signal(sig, request_shutdown) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        await _run()
    except asyncio.CancelledError:
        if not stopping:
            raise
        logger.info("Analyst stopped.")
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


async def _run() -> None:
    profile = ProfileRepositoryXML(ROOT / "artifacts/data/prompts/profile.xml")

    async with asyncpg.create_pool(dsn=PG_DSN) as pool:
        async with AsyncTypeSafeClient(model=MODEL) as client, AsyncExitStack() as resources:
            iteration = await define_iteration(pool) + 1

            runtime_version = RuntimeVersion(
                model=MODEL,
                version=PIPELINE_VERSION,
                iteration=iteration,
                min_version=MIN_ACCEPTED_PIPELINE_VERSION,
            )
            job_repo = JobRepositoryPostgres(pool, runtime_version)
            fact_sheet_repo = JobFactSheetRepositoryPostgres(pool, runtime_version)
            match_repo = MatchRepositoryPostgres(pool, runtime_version)
            job_clf = ClassifyJobTierJev(client)
            application_repo = ApplicationPacketRepositoryPostgres(pool, runtime_version)
            profile_clf = ClassifyProfileToJob(client)
            generator = OpenAIClient(
                prompt_path=ROOT / "artifacts" / "data" / "prompts" / "coverletter_example.md"
            )
            resources.push_async_callback(generator.close)
            summary_available = asyncio.Event()

            engine = MatchingEngine(
                logger,
                runtime_version,
                job_repo,
                fact_sheet_repo,
                match_repo,
                application_repo,
                job_clf=job_clf,
                profile_clf=profile_clf,
                profile=profile,
                application_generator=generator,
                summary_available=summary_available,
            )

            discussion_repository = DiscussionRepositoryPostgres(pool)
            discussion_model = OpenAIDiscussionModel()
            resources.push_async_callback(discussion_model.close)
            discussion = AnalyticsDiscussionService(
                discussion_repository, SafeDiscussionModel(discussion_model), profile, profile_clf
            )
            scraper = LiveJobScraper()
            resources.push_async_callback(scraper.close)
            # Preserve the local identity used by existing Telegram discussion history.
            owner_id = int(os.environ.get("TELEGRAM_CHAT_ID", "975840884"))
            app = create_app(
                JobService(
                    job_repo,
                    match_repository=match_repo,
                    classifier=job_clf,
                    profile=profile,
                    scraper=scraper,
                    summary_available=summary_available,
                ),
                match_service=MatchService(match_repo),
                application_service=ApplicationService(
                    application_repo, match_repo, profile_clf, profile, generator
                ),
                discussion_service=DiscussionService(
                    discussion,
                    discussion_repository,
                    match_repo,
                    user_id=owner_id,
                    chat_id=owner_id,
                ),
            )
            server = AnalystServer(
                uvicorn.Config(
                    app,
                    host="127.0.0.1",
                    port=int(os.getenv("API_PORT", "8000")),
                )
            )
            summary_generator = OpenAISummaryGenerator()
            resources.push_async_callback(summary_generator.close)
            summary_worker = SummaryWorker(match_repo, summary_generator, summary_available, logger)
            await run_services(engine, server, summary_worker)


if __name__ == "__main__":
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    asyncio.run(run())
