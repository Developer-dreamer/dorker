import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, List, Tuple, Type

from openai import OpenAI
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel

from src.scraping.models import Job

from .models import MatchedJob

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

    def __init__(self, prompts: dict[str, str]) -> None:
        self._client = OpenAI()
        self._models = {
            "small": "gpt-5.4-nano",
            "medium": "gpt-5.6-terra",
            "large": "gpt-5.6-sol"
        }

        self._prompts: dict[str, str] = prompts
        self.batch_lines = 0


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

    def reject_batch(self, jobs: List[Job]) -> Any:

        batch_file_path = self._create_batch_file(
            batch_id="0",
            jobs=jobs,
            format_jobs=lambda job: job.model_dump_json(
                                    include={"title", "department", "team", "employment_type"},
                                    exclude_none=True
                                ),
            output_dir=Path("/Users/serafym/Developer/dorker.space/intelligence_core/batches"),
            model=self._models["small"],
            master_prompt=self._prompts["rejection"],
            schema=GatekeeperDecision
        )

        # batch_input_file = self._client.files.create(
        #     file=open(batch_file_path, "rb"), purpose="batch"
        # )

        # batch = self._client.batches.create(
        #     input_file_id=batch_input_file.id,
        #     endpoint="/v1/chat/completions",
        #     completion_window="24h",
        #     metadata={"description": "nightly eval job"},
        # )

        return batch_file_path

    def rank_batch(self, jobs: List[Job]) -> Any:
        pass


    async def monitor_batch(self) -> None:
        pass

    async def retreive_batch(self) -> None:
        pass

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
                       schema: Type[BaseModel]) -> Path:

        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / f"batch_{batch_id}.jsonl"

        current_lines: List[str] = []
        current_bytes = 0
        current_count = 0
        line_bytes = 0

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

                return file_path

            current_lines.append(json_str)
            current_bytes += line_bytes
            current_count += 1

        if current_lines:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write("\n".join(current_lines) + "\n")

        return file_path
