import asyncio
from pathlib import Path

import aiosqlite

from src.analytics.batch_repo import BatchRepository
from src.analytics.openai import OpenAIClient
from src.database.sqlite import get_db_connection
from src.scraping.models import Job
from orchestration.normalize_descriptions import normalize_one

ROOT = Path(__file__).resolve().parent


async def run() -> None:

    prompts = {
        "rejection": (
            "You are an ultra-fast technical recruitment gatekeeper. "
            "Your ONLY task is to aggressively filter out completely irrelevant job descriptions. "
            "Candidate constraints: Developer/Engineering roles only. "
            "Evaluate the job payload and return true if relevant (software/ai developer/engineer etc.), false if marketing, sales, or other non-technical."
        ),
    }

    prompt_path = ROOT / "prompt" / "ranking_prompt.md"
    with open(prompt_path, "r") as f:
        prompts["ranking"] = f.read()

    db_path = ROOT / "app.db"
    conn = await get_db_connection(db_path)
    query = """
                SELECT j.*, j.company_slug AS company
                FROM jobs AS j
                    LEFT JOIN matches m ON j.id = m.job_id
                    LEFT JOIN openai_batch_items obi ON j.id = obi.job_id
                WHERE m.job_id IS NULL
                AND obi.job_id IS NULL
                LIMIT 10000;
            """

    # conn.row_factory = aiosqlite.Row
    # async with conn.execute(query) as cursor:
    #     rows = await cursor.fetchall()
    #     jobs = [Job.model_validate(dict(row)) for row in rows]

    batches_dir = ROOT / "batches"
    ai = OpenAIClient(prompts, batches_dir, BatchRepository(conn))

    # await ai.reject_batch(jobs)
    # await conn.commit()

    batch = await ai.monitor_batch()

    await ai.retreive_batch(batch, conn)

    await conn.commit()
    await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
