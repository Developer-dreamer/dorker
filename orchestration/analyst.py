import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import joblib
import torch
from asyncpg import Pool
from typesafe_sdk import AsyncTypeSafeClient

from src.analytics.database import (
    JobFactSheetRepositoryPostgres,
    JobRepositoryPostgres,
    MatchRepositoryPostgres,
)
from src.analytics.engine import MatchingEngine
from src.analytics.jev import Jev
from src.analytics.models import RuntimeVersion
from src.analytics.slm import SLMQwenThinking

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

PG_DSN = os.environ.get("PG_DSN")

# Thinking model
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


async def run_old() -> None:
    if PG_DSN is None:
        raise ValueError("PG_DSN not set")

    async with asyncpg.create_pool(dsn=PG_DSN) as pool:
        iteration = await define_iteration(pool) + 1

        clf = joblib.load(ROOT / "artifacts" / "model" / "block_classifier_nomic.pkl")

        slm = SLMQwenThinking(
            logger,
            model_path=ROOT / "models" / "qwen2.5-7b-instruct-q4_k_m.gguf",
            thinking_token_limit=150,
        )

        runtime_version = RuntimeVersion(model=MODEL, version=PIPELINE_VERSION, iteration=iteration)
        job_repo = JobRepositoryPostgres(pool, runtime_version)
        fact_sheet_repo = JobFactSheetRepositoryPostgres(pool, runtime_version)
        match_repo = MatchRepositoryPostgres(pool, runtime_version)
        engine = MatchingEngine(
            logger, runtime_version, job_repo, fact_sheet_repo, match_repo, clf, slm
        )

        await engine.run_slm()


async def run() -> None:
    if PG_DSN is None:
        raise ValueError("PG_DSN not set")

    async with asyncpg.create_pool(dsn=PG_DSN) as pool:
        async with AsyncTypeSafeClient() as client:
            iteration = await define_iteration(pool) + 1

            runtime_version = RuntimeVersion(
                model=MODEL, version=PIPELINE_VERSION, iteration=iteration
            )
            job_repo = JobRepositoryPostgres(pool, runtime_version)
            fact_sheet_repo = JobFactSheetRepositoryPostgres(pool, runtime_version)
            match_repo = MatchRepositoryPostgres(pool, runtime_version)
            with open(ROOT / "orchestration" / "profile.md") as f:
                profile = f.read()
            jev = Jev(client, state=profile)

            engine = MatchingEngine(
                logger, runtime_version, job_repo, fact_sheet_repo, match_repo, jev=jev
            )

            await engine.run_jev()


if __name__ == "__main__":
    asyncio.run(run())
