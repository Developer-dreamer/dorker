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

from analytics.classification import ClassifyJobTierJev
from database.postgres.job_fact_sheet import JobFactSheetRepositoryPostgres
from src.analytics.engine import MatchingEngine
from src.database.postgres import JobRepositoryPostgres, MatchRepository
from src.shared.models.version import RuntimeVersion

os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = "0.8"
os.environ["PYTORCH_MPS_LOW_WATERMARK_RATIO"] = "0.3"

# --- Logging Configuration ---
ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "logs" / f"analyst_{datetime.now(timezone.utc).isoformat()}.log"

# Configure file-only logging
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("analyst")

PG_DSN = os.environ.get("PG_DSN", "postgresql://postgres:password@localhost:5432/dorker_db")
MODEL = "jev-1.13.0"
PIPELINE_VERSION = "0.2.2"

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


async def run() -> None:
    cv_path = ROOT / "artifacts" / "data" / "prompts" / "cv.md"
    with open(cv_path, "r") as f:
        cv = f.read()

    async with asyncpg.create_pool(dsn=PG_DSN) as pool:
        async with AsyncTypeSafeClient() as client:
            iteration = await define_iteration(pool) + 1

            runtime_version = RuntimeVersion(
                model=MODEL, version=PIPELINE_VERSION, iteration=iteration
            )
            job_repo = JobRepositoryPostgres(pool, runtime_version)
            fact_sheet_repo = JobFactSheetRepositoryPostgres(pool, runtime_version)
            match_repo = MatchRepository(pool, runtime_version)
            jev = ClassifyJobTierJev(client, state=cv)

            engine = MatchingEngine(
                logger, runtime_version, job_repo, fact_sheet_repo, match_repo, jev=jev
            )

            await engine.run()


if __name__ == "__main__":
    asyncio.run(run())
