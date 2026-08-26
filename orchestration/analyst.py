import asyncio

import aiosqlite

from src.analytics.batch_repo import BatchRepository
from src.analytics.openai import OpenAIClient
from src.database.sqlite import get_db_connection
from src.scraping.models import Job
from src.scraping.normalize_descriptions import normalize_one


async def run() -> None:

    prompts = {
        "rejection": ("You are an ultra-fast technical recruitment gatekeeper. "
                "Your ONLY task is to aggressively filter out completely irrelevant job descriptions. "
                "Candidate constraints: Developer/Engineering roles only. "
                "Evaluate the job payload and return true if relevant (software/ai developer/engineer etc.), false if marketing, sales, or other non-technical."
            ),
    }

    with open("/Users/serafym/Developer/dorker.space/intelligence_core/prompt/ranking_prompt.md", "r") as f:
        prompts["ranking"] = f.read()

    db_path = "/Users/serafym/Developer/dorker.space/intelligence_core/app.db"
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


    ai = OpenAIClient(prompts,
                        "/Users/serafym/Developer/dorker.space/intelligence_core/batches",
                        BatchRepository(conn))


    # await ai.reject_batch(jobs)
    # await conn.commit()

    batch = await ai.monitor_batch()

    await ai.retreive_batch(batch, conn)

    await conn.commit()
    await conn.close()




if __name__ == "__main__":
    asyncio.run(run())
