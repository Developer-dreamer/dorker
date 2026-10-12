import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import torch
from asyncpg import Pool
from typesafe_sdk import AsyncTypeSafeClient

from src.analytics.classification import ClassifyJobTierJev, ClassifyProfileToJob
from src.analytics.discussion import DiscussionService
from src.analytics.engine import MatchingEngine
from src.analytics.generation import OpenAIClient
from src.analytics.generation.discussion_openai import OpenAIDiscussionModel
from src.analytics.generation.summary_openai import OpenAISummaryGenerator
from src.analytics.profile_repository_xml import ProfileRepositoryXML
from src.analytics.summary_worker import SummaryWorker
from src.client import TelegramBot
from src.database.postgres import (
    ApplicationPacketRepositoryPostgres,
    JobFactSheetRepositoryPostgres,
    JobRepositoryPostgres,
    MatchRepositoryPostgres,
)
from src.database.postgres.discussion import DiscussionRepositoryPostgres
from src.shared.models import RuntimeVersion

os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = "0.8"
os.environ["PYTORCH_MPS_LOW_WATERMARK_RATIO"] = "0.3"

# --- Logging Configuration ---
ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "logs" / f"analyst_{datetime.now(timezone.utc).isoformat()}.log"

logger = logging.getLogger("analyst")

PG_DSN = os.environ.get("PG_DSN", "postgresql://postgres:password@localhost:5432/dorker_db")
MODEL = "jev-1.13.0"
PIPELINE_VERSION = "0.3.1"
MIN_ACCEPTED_PIPELINE_VERSION = "0.3.0"

device = "mps" if torch.backends.mps.is_available() else "cpu"


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
    engine: MatchingEngine, bot: TelegramBot, summary_worker: SummaryWorker
) -> None:
    tasks = [
        asyncio.create_task(classify_periodically(engine), name="classification"),
        asyncio.create_task(summary_worker.run(), name="summaries"),
    ]
    try:
        await bot.run()
    finally:
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for task, result in zip(tasks, results, strict=True):
            if isinstance(result, Exception):
                logger.error("Service %s failed: %s", task.get_name(), result)


async def run() -> None:
    profile = ProfileRepositoryXML(ROOT / "artifacts/data/prompts/profile.xml")

    async with asyncpg.create_pool(dsn=PG_DSN) as pool:
        async with AsyncTypeSafeClient() as client:
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

            telegram_api_key = os.getenv("TELEGRAM_API_KEY")
            if not telegram_api_key:
                raise ValueError("TELEGRAM_API_KEY environment variable is not set")
            discussion_model = OpenAIDiscussionModel(
                model=os.getenv("OPENAI_DISCUSSION_MODEL", "gpt-6-luna")
            )

            discussion = DiscussionService(
                DiscussionRepositoryPostgres(pool),
                discussion_model,
                profile,
                profile_clf,
            )
            bot = TelegramBot(
                logger,
                telegram_api_key,
                engine=engine,
                discussion=discussion,
                chat_id=int(os.getenv("TELEGRAM_CHAT_ID", "975840884")),
            )
            summary_generator = OpenAISummaryGenerator()
            summary_worker = SummaryWorker(match_repo, summary_generator, summary_available, logger)
            try:
                await run_services(engine, bot, summary_worker)
            finally:
                await bot.stop()
                await discussion_model.close()
                await summary_generator.close()


if __name__ == "__main__":
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
