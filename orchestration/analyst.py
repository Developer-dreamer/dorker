import asyncio
import logging
from pathlib import Path

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
PG_DSN = "postgresql://postgres:password@localhost:5432/dorker_db"

MODEL = "qwen2.5-coder:7b"
PIPELINE_VERSION = "v0.2.3"
ITERATION = 1


async def run() -> None:
    pass


if __name__ == "__main__":
    asyncio.run(run())
