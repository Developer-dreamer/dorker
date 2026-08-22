from src.analytics.openai import OpenAIClient
import aiosqlite
from src.scraping.models import Job
import asyncio


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


    ai = OpenAIClient(prompts)

    db_path = "/Users/serafym/Developer/dorker.space/intelligence_core/app.db"
    query = """
                SELECT j.*, j.company_slug AS company
                FROM jobs AS j
                LEFT JOIN matches AS m ON j.id = m.job_id
                WHERE m.job_id IS NULL
                LIMIT 100;
            """

    jobs = []
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(query) as cursor:
            rows = await cursor.fetchall()
            jobs = [Job.model_validate(dict(row)) for row in rows]


    batch_file = ai.reject_batch(jobs)

    print(batch_file)



if __name__ == "__main__":
    asyncio.run(run())
