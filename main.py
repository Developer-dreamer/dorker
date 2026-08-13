import asyncio
import logging
import os
import pathlib
import sys
from datetime import datetime

# ===== CONFIGURATION =====

DATASET_URL = "https://storage.stapply.ai/jobhive/v1/all.parquet"
LOCAL_TMP_FILE = pathlib.Path("./all_jobs_temp.parquet")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)




async def main() -> None:
    await init_sqlite_db()

    start_time = datetime.utcnow()
    try:
        await download_dataset_streamed(DATASET_URL, LOCAL_TMP_FILE)
        await execute_batch_ingestion(LOCAL_TMP_FILE)
    except Exception as e:
        logger.critical(f"Pipeline crashed: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if LOCAL_TMP_FILE.exists():
            logger.info("Cleaning up temporary local Parquet storage...")
            LOCAL_TMP_FILE.unlink()

    duration = (datetime.utcnow() - start_time).total_seconds()
    logger.info(f"Done! Pipeline execution completed in {duration:.1f} seconds.")

if __name__ == "__main__":

    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    asyncio.run(main())
