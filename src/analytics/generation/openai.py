import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple, Type
from uuid import UUID

from openai import AsyncOpenAI
from openai.lib._pydantic import to_strict_json_schema
from openai.types.responses import (
    EasyInputMessageParam,
    ResponseTextConfigParam,
)
from openai.types.responses.response_format_text_json_schema_config_param import (
    ResponseFormatTextJSONSchemaConfigParam,
)
from openai.types.shared_params import Reasoning
from pydantic import BaseModel

from src.database.protocols import BatchRepository
from src.shared.models import (
    ApplicationGeneratedResponse,
    BatchStatus,
    JobForAnalytics,
    OpenAIBatchRecord,
)

from .protocols import ApplicationGenerator

MAX_BYTES_PER_BATCH = 2e8  # 200 MB per request
MAX_REQUESTS_PER_BATCH = 50_000  # 50 000 separate questions to AI model


class OpenAIClient(ApplicationGenerator[ApplicationGeneratedResponse]):
    def __init__(
        self,
        prompt_path: Path,
        batch_dir: Path | None = None,
        batch_repo: BatchRepository | None = None,
    ) -> None:
        self._client = AsyncOpenAI()
        self.batch_directory = batch_dir
        self._batch_repo = batch_repo
        self.prompt_path = prompt_path
        self._read_prompt()

    def _read_prompt(self) -> None:
        with open(self.prompt_path, "r") as f:
            self.master_prompt = f.read()

    async def generate_sync(self, job: JobForAnalytics) -> ApplicationGeneratedResponse:
        developer_instructions = self.master_prompt
        if job.application is not None:
            developer_instructions += "\n<candidate_context>\n"
            for material in job.application.materials:
                developer_instructions += material.text + "\n"
            developer_instructions += "</candidate_context>"

        assert job.match is not None
        content = f"""
                    Here is the job and corresponding match (computed with Jev by TypeSafe AI) to it.
                    Your task is to analyze this and by following developer instructions output 
                    requested model. 
                    <computed_match>
                    {job.match.model_dump_json()}
                    </computed_match>
                    <job_payload>
                    {job.model_dump_json(exclude={"match", "application"})}
                    </job_payload>
                    """

        resp = await self._client.responses.create(
            model="gpt-6-luna",
            reasoning=Reasoning(effort="medium"),
            input=[
                EasyInputMessageParam(
                    role="developer",
                    content=developer_instructions,
                ),
                EasyInputMessageParam(
                    role="user",
                    content=content,
                ),
            ],
            text=ResponseTextConfigParam(
                format=ResponseFormatTextJSONSchemaConfigParam(
                    type="json_schema",
                    name=ApplicationGeneratedResponse.__name__,
                    strict=True,
                    schema=to_strict_json_schema(ApplicationGeneratedResponse),
                )
            ),
            max_output_tokens=5000,
        )

        resp_json = resp.output_text
        debug = resp.model_dump_json(exclude_none=True, exclude={"output_text"})
        application = ApplicationGeneratedResponse.model_validate_json(resp_json)
        application.debug = debug
        return application

    async def queue_generation(self, jobs: List[JobForAnalytics]) -> None:
        assert self.batch_directory is not None
        assert self._batch_repo is not None

        batch_file_path, job_ids = self._create_batch_file(
            batch_id=datetime.now(timezone.utc).isoformat(),
            jobs=jobs,
            output_dir=Path(self.batch_directory),
            model="gpt-5-mini-2025-08-07",
            master_prompt=self.master_prompt,
            schema=ApplicationGeneratedResponse,
        )

        print(f"[INFO] Batch file created. Path: {batch_file_path}")

        with open(
            batch_file_path, "rb"
        ) as f:  # used synchronous intentionally. expected to be executed in order.
            batch_input_file = await self._client.files.create(file=f, purpose="batch")
        print(f"[INFO] Batch file sent to OpenAI API. ID: {batch_input_file.id}")

        batch = await self._client.batches.create(
            input_file_id=batch_input_file.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
        print(
            f"[INFO] Batch created. ID: {batch.id}, Status: {batch.status}, "
            f"Jobs sent: {len(job_ids)}."
        )

        record = OpenAIBatchRecord(
            id=batch.id,
            input_file_id=batch_input_file.id,
            status=BatchStatus(batch.status),
            items=job_ids,
        )

        print(f"[INFO] Record created: {record.model_dump_json()}")

        await self._batch_repo.save_batch(record)

    async def harvest_results(self) -> List[Tuple[UUID, ApplicationGeneratedResponse]]:
        raise NotImplementedError()

    def _build_batch_line(
        self,
        model: str,
        master_prompt: str,
        job_id: str,
        job_payload: str,
        schema: Type[BaseModel],
    ) -> Tuple[str, int]:
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

    def _create_batch_file(
        self,
        batch_id: str,
        jobs: List[JobForAnalytics],
        output_dir: Path,
        model: str,
        master_prompt: str,
        schema: Type[BaseModel],
    ) -> Tuple[Path, List[UUID]]:
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / f"batch_{batch_id}.jsonl"

        current_lines: List[str] = []
        current_bytes = 0
        current_count = 0

        processed_jobs: List[UUID] = []
        for job in jobs:
            json_str, line_bytes = self._build_batch_line(
                model=model,
                master_prompt=master_prompt,
                job_id=job.id,
                job_payload=job.model_dump_json(),
                schema=schema,
            )

            if (
                current_bytes + line_bytes > MAX_BYTES_PER_BATCH
                or current_count + 1 > MAX_REQUESTS_PER_BATCH
            ):
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write("\n".join(current_lines) + "\n")

                return file_path, processed_jobs

            assert job.match is not None

            current_lines.append(json_str)
            processed_jobs.append(job.match.id)

            current_bytes += line_bytes
            current_count += 1

        if current_lines:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write("\n".join(current_lines) + "\n")

        return file_path, processed_jobs
