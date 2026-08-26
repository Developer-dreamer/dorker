import json
from asyncio import Queue
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple, Type

import aiosqlite
from openai import OpenAI
from openai.lib._pydantic import to_strict_json_schema
from openai.types import Batch
from pydantic import BaseModel

from src.database.sqlite import save_technical_match, save_unmatch
from src.scraping.models import Job

from .batch_repo import BatchRepository
from .models import Analytics, ApplicationStatus, MatchedJob, OpenAIBatchRecord, Purpose

MAX_BYTES_PER_BATCH = 2e+8 # 200 MB per request
MAX_REQUESTS_PER_BATCH = 50_000 # 50 000 separate questions to AI model

REJECT_PROMPT = ("You are an ultra-fast technical recruitment gatekeeper. "
                "Your ONLY task is to aggressively filter out completely irrelevant job descriptions. "
                "Candidate constraints: Developer/Engineering roles only. "
                "Evaluate the job payload and return true if relevant (software/ai developer/engineer etc.), false if marketing, sales, or other non-technical."
            )

class GatekeeperDecision(BaseModel):
    is_relevant: bool


class OpenAIClient():

    def __init__(self, prompts: dict[str, str], batch_dir: str, batch_repo: BatchRepository) -> None:
        self._client = OpenAI()
        self._models = {
            "small": "gpt-5.4-nano",
            "medium": "gpt-5.6-terra",
            "large": "gpt-5.6-sol"
        }
        self._prompts: dict[str, str] = prompts

        self.batch_directory = batch_dir
        self._batch_repo = batch_repo
        self.batch_event_queue: Queue[str] = Queue(maxsize=10)


    def reject_single(self, job: Job) -> GatekeeperDecision:
        job_payload = job.model_dump_json(
            include={"title", "department", "team", "employment_type"},
            exclude_none=True
        )

        completion = self._client.chat.completions.parse(
            model=self._models["small"],
            messages=[
                {
                    "role": "system",
                    "content": self._prompts["rejection"]
                },
                {
                    "role": "user",
                    "content": job_payload
                }
            ],
            response_format=GatekeeperDecision,
        )

        return completion.choices[0].message.parsed.is_relevant

    def rank_single(self, job: Job) -> MatchedJob:
        job_payload = job.model_dump_json(
            exclude={"global_id", "url", "ats_type", "ats_id", "lat", "lon", "is_remote", "requisition_id", "apply_url", "raw"}
        )

        completion = self._client.chat.completions.parse(
                model=self._models["medium"],
                messages=[
                    {
                        "role": "system",
                        "content": self._prompts["ranking"]
                    },
                    {
                        "role": "user",
                        "content": job_payload
                    }
                ],
                response_format=MatchedJob,
            )


        return completion.choices[0].message.parsed

    async def reject_batch(self, jobs: List[Job]) -> None:

        batch_file_path, job_ids = self._create_batch_file(
            batch_id=datetime.now(timezone.utc).isoformat(),
            jobs=jobs,
            format_jobs=lambda job: job.model_dump_json(
                                    include={"title", "department", "team", "employment_type"},
                                    exclude_none=True
                                ),
            output_dir=Path(self.batch_directory),
            model=self._models["small"],
            master_prompt=self._prompts["rejection"],
            schema=GatekeeperDecision
        )

        print(f'[INFO] Batch file created. Path: {batch_file_path}')

        batch_input_file = self._client.files.create(
            file=open(batch_file_path, "rb"), purpose="batch"
        )
        print(f'[INFO] Batch file sent to OpenAI API. ID: {batch_input_file.id}')

        batch = self._client.batches.create(
            input_file_id=batch_input_file.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
        print(f'[INFO] Batch created. ID: {batch.id}, Status: {batch.status}, Jobs sent: {len(job_ids)}.')

        record = OpenAIBatchRecord.from_openai_batch(
            batch=batch,
            purpose=Purpose.REJECT,
        )
        print(f"[INFO] Record created: {record.model_dump_json()}")

        await self._batch_repo.create_batch(record, job_ids)

    async def rank_batch(self, jobs: List[Job]) -> None:
        batch_file_path, job_ids = self._create_batch_file(
                batch_id="0",
                jobs=jobs,
                format_jobs=lambda job: job.model_dump_json(
                                        exclude={"global_id", "url", "ats_type", "ats_id", "lat", "lon", "is_remote", "requisition_id", "apply_url", "raw"},
                                        exclude_none=True
                                    ),
                output_dir=Path(self.batch_directory),
                model=self._models["medium"],
                master_prompt=self._prompts["ranking"],
                schema=MatchedJob
            )

        print(f'[INFO] Batch file created. Path: {batch_file_path}')

        batch_input_file = self._client.files.create(
            file=open(batch_file_path, "rb"), purpose="batch"
        )
        print(f'[INFO] Batch file sent to OpenAI API. ID: {batch_input_file.id}')

        batch = self._client.batches.create(
            input_file_id=batch_input_file.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
        print(f'[INFO] Batch created. ID: {batch.id}, Status: {batch.status}, Jobs sent: {len(job_ids)}.')

        record = OpenAIBatchRecord.from_openai_batch(
            batch=batch,
            purpose=Purpose.RANK,
        )

        await self._batch_repo.create_batch(record, job_ids)

    async def monitor_batch(self) -> Batch:
        # try:
        pending_batches = await self._batch_repo.get_pending_batches()
        for batch_id, purpose in pending_batches:
            batch = self._client.batches.retrieve(batch_id)

            batch_record = OpenAIBatchRecord.from_openai_batch(
                                batch,
                                purpose,
                                last_polled_at=int(datetime.now(timezone.utc).timestamp()))
            await self._batch_repo.update_batch(batch_record)

            return batch

        return None

        # except CancelledError:
        #     print("[INFO] Exiting polling coroutine")

    async def retreive_batch(self, batch: Batch, conn: aiosqlite.Connection) -> None:
        file_response = self._client.files.content(batch.output_file_id)

        batch_items = await self._batch_repo.get_batch_items(batch.id)


        for line in file_response.text.strip().splitlines():
            if not line:
                continue

            record = json.loads(line)

            batch_id = record.get("id")
            job_id = record.get("custom_id")
            match = next(
                        (item for item in batch_items if item.job_id == job_id),
                        None,
                    )
            if not match:
                print(f"[WARN] Job with id {job_id} skipped for batch {batch_id}")
                continue

            status_code = int(record.get("response").get("status_code"))
            match.http_status_code = status_code
            match.status = "COMPLETED" if status_code == 200 else "FALIED"
            if status_code != 200:
                match.error_code = record.get("error").get("code")
                match.error_message = record.get("error").get("message")

            content = record.get("response").get("body").get("choices")[0].get("message").get("content")
            content_model = GatekeeperDecision.model_validate_json(content)

            match_id = ""
            if not content_model.is_relevant:
                match_id = await save_unmatch(conn, job_id)
            else:
                match_id = await save_technical_match(conn, job_id)

            match.match_id = match_id

        await self._batch_repo.update_batch_items(batch_items)


    def _build_batch_line(self, model: str,
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

    def _create_batch_file(self,
                       batch_id: str,
                       jobs: List[Job],
                       format_jobs: Callable[[Job], str],
                       output_dir: Path,
                       model: str,
                       master_prompt: str,
                       schema: Type[BaseModel]) -> Tuple[Path, List[str]]:

        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / f"batch_{batch_id}.jsonl"

        current_lines: List[str] = []
        current_bytes = 0
        current_count = 0
        line_bytes = 0

        processed_jobs: List[str] = []
        for job in jobs:
            json_str, line_bytes = self._build_batch_line(
                model=model,
                master_prompt=master_prompt,
                job_id=job.global_id,
                job_payload=format_jobs(job),
                schema=schema,
            )

            if (current_bytes + line_bytes > MAX_BYTES_PER_BATCH
                or current_count + 1 > MAX_REQUESTS_PER_BATCH):

                with open(file_path, "a", encoding="utf-8") as f:
                    f.write("\n".join(current_lines) + "\n")

                return file_path, processed_jobs

            current_lines.append(json_str)
            processed_jobs.append(job.global_id)

            current_bytes += line_bytes
            current_count += 1

        if current_lines:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write("\n".join(current_lines) + "\n")

        return file_path, processed_jobs
