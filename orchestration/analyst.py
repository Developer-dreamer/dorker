import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import joblib
from guidance import models
from sentence_transformers import SentenceTransformer

from src.analytics.database import JobFactSheetRepositoryPostgres, JobRepositoryTest
from src.analytics.engine import Engine
from src.analytics.models import RuntimeVersion

# --- Logging Configuration ---
ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "logs" / f"analyst_{datetime.now(timezone.utc).isoformat()}.log"

# Configure file-only logging
logging.basicConfig(
    filename=LOG_PATH,
    filemode="a",
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("analyst")

PG_DSN = "postgresql://postgres:password@localhost:5432/dorker_db"

MODEL = "qwen2.5-coder:7b"
PIPELINE_VERSION = "v0.2.0"
ITERATION = 1


async def run() -> None:
    async with asyncpg.create_pool(dsn=PG_DSN) as pool:
        clf, embedder = (
            joblib.load(ROOT / "artifacts" / "model" / "block_classifier_nomic.pkl"),
            SentenceTransformer(
                "nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True, local_files_only=True
            ),
        )

        lm = models.LlamaCpp(
            model=ROOT / "models" / "qwen2.5-7b-instruct-q4_k_m.gguf",
            n_gpu_layers=-1,
            n_ctx=8192,
            temperature=0.5,
        )

        runtime_version = RuntimeVersion(model=MODEL, version=PIPELINE_VERSION, iteration=ITERATION)
        job_repo = JobRepositoryTest(pool)
        fact_sheet_repo = JobFactSheetRepositoryPostgres(pool, runtime_version)

        engine = Engine(logger, runtime_version, job_repo, fact_sheet_repo, lm, embedder, clf)

        await engine.run()


if __name__ == "__main__":
    asyncio.run(run())
