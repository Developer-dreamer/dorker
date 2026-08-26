import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Tuple, Type

import aiosqlite
from openai import OpenAI
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel


class SegmentedBlock(BaseModel):
    text: str
    label: Literal[
        "REQUIREMENTS",
        "RESPONSIBILITIES",
        "COMPENSATION_LOCATION",
        "COMPANY_PROFILE",
        "BENEFITS_PERKS",
        "LEGAL_BOILERPLATE_EEO"
    ]
    confidence: float

class JobDescriptionAnnotation(BaseModel):
    blocks: List[SegmentedBlock]


def build_batch_line(model: str,
                    master_prompt: str,
                    job_id: str,
                    job_payload: str,
                    schema: Type[BaseModel]) -> Tuple[str,int]:
        batch_entry = {
            "custom_id": job_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": model,
                "messages": [
                    {"role": "system", "content": master_prompt},
                    {"role": "user", "content": job_payload},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "strict": True,
                        "schema": to_strict_json_schema(schema),
                    },
                },
            },
        }

        batch_entry_str = json.dumps(batch_entry, ensure_ascii=False)
        byte_size = len(batch_entry_str.encode("utf-8")) + 1

        return batch_entry_str, byte_size


MAX_BYTES_PER_BATCH = 2e+8 # 200 MB per request
MAX_REQUESTS_PER_BATCH = 50_000 # 50 000 separate questions to AI model

def create_batch_file(batch_id: str,
                       model: str,
                       jobs: List[Tuple[str,str]],
                       output_dir: Path) -> Tuple[Path, List[str]]:

        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / f"batch_{batch_id}.jsonl"

        with open("/Users/serafym/Developer/dorker.space/intelligence_core/prompt/labeling_prompt_chatgpt.md", "r") as f:
             master_prompt = f.read()

        current_lines: List[str] = []
        current_bytes = 0
        current_count = 0
        line_bytes = 0

        processed_jobs: List[str] = []
        for job in jobs:

            json_str, line_bytes = build_batch_line(
                model=model,
                master_prompt=master_prompt,
                job_id=job[0],
                job_payload=job[1],
                schema=JobDescriptionAnnotation,
            )

            if (current_bytes + line_bytes > MAX_BYTES_PER_BATCH
                or current_count + 1 > MAX_REQUESTS_PER_BATCH):

                with open(file_path, "a", encoding="utf-8") as f:
                    f.write("\n".join(current_lines) + "\n")

                return file_path, processed_jobs

            current_lines.append(json_str)
            processed_jobs.append(job[0])

            current_bytes += line_bytes
            current_count += 1

        if current_lines:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write("\n".join(current_lines) + "\n")


        return file_path, processed_jobs

async def run() -> None:
    db_path = 'app.db'

    query = """
            WITH matched_ids AS (SELECT rowid,
                                        -- bm25() returns lower values for better matches in FTS5
                                        bm25(jobs_fts) as rank_score
                                FROM jobs_fts
                                WHERE jobs_fts MATCH '
                    ("Go" OR "Golang" OR "Python" OR "C#" OR ".NET" OR "dotnet" OR "ASP.NET" OR "C++")
                    NOT title : (Lead OR Principal OR Staff OR Director OR Architect OR Manager OR VP OR Head OR Executive)
                    NOT (Frontend OR "Front End" OR "UI" OR iOS OR Android OR Flutter OR "React Native" OR PHP OR WordPress OR Magento OR "Ruby on Rails" OR "Network Engineer")
                ')
            SELECT j.id, j.description
            FROM matched_ids m
                    JOIN jobs j ON j.ROWID = m.rowid
            WHERE j.posted_at >= date('now', '-1 month')
            -- Broaden location to catch ATS remote inaccuracies before local LLM extracts the truth
            AND (
                j.location LIKE '%Ukraine%' OR
                j.location LIKE '%Europe%' OR
                j.location LIKE '%Remote%' OR
                j.location LIKE '%EMEA%' OR
                j.location LIKE '%Worldwide%' OR
                j.location LIKE '%Global%'
                )
            ORDER BY m.rank_score ASC,
                    j.posted_at DESC
            LIMIT 1000;
            """

    # async with aiosqlite.connect(db_path) as conn:
    #     cursor = await conn.execute(query)
    #     rows = await cursor.fetchall()

    #     jobs = [(row[0], row[1]) for row in rows]

    # path, ids = create_batch_file(batch_id=datetime.now(timezone.utc).isoformat(),
    #                   model="gpt-5.6-luna",
    #                   jobs=jobs,
    #                   output_dir=Path("/Users/serafym/Developer/dorker.space/intelligence_core/ml"))

    # print(f'[INFO] Batch file created. Path: {path}')

    client = OpenAI()
    
    batch_input_file = client.files.create(
        file=open(Path("/Users/serafym/Developer/dorker.space/intelligence_core/ml/batch_2026-08-26T15:04:34.625737+00:00.jsonl"), "rb"), purpose="batch"
    )
    print(f'[INFO] Batch file sent to OpenAI API. ID: {batch_input_file.id}')

    batch = client.batches.create(
        input_file_id=batch_input_file.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )
    print(f'[INFO] Batch created. ID: {batch.id}, Status: {batch.status}, Jobs sent: {len(ids)}.')

if __name__=="__main__":
     asyncio.run(run())
