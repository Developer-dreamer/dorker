import json
import re
import time
from logging import Logger
from pathlib import Path
from typing import Any, Dict, Tuple, cast

from llama_cpp import Llama
from pydantic import BaseModel, Field

from src.analytics.protocols import SLM
from src.shared.models import JobForAnalytics

SYSTEM_PROMPT = (
    "You are an expert technical career and engineering role analyst. "
    "Your objective is to dissect job postings for engineers and provide an objective, "
    "high-signal evaluation covering job family, role scope, genuine technical pros, and negative operational cues."
)

USER_PROMPT_TEMPLATE = """Analyze the following job description thoroughly:

<job_posting>
Title: {title}
Location: {location}
Description:
{description}
</job_posting>

Instructions:
You MUST think step-by-step first inside <think>...</think> before producing the final JSON. In your thinking process:
- Step 1 (Summary Evaluation): Identify the job family (e.g., backend systems, data engineering, distributed systems, platform infrastructure, AI/ML engineering). Evaluate the actual role scope, technical stack expectations, and day-to-day responsibilities.
- Step 2 (Pros Evaluation): Scrutinize the true advantages of the position. What personal and technical development will this job offer? How complex and interesting is the architectural and technical scope?
- Step 3 (Cons Evaluation): Scrutinize negative operational cues and red flags. Is there risk of superficial 'vibecoding' without deep engineering? Heavy legacy technical debt? Over-responsibility, chaotic management, or unrealistic 24/7 / on-call demands?

After your thinking, output ONLY a valid JSON object matching this structure:
```json
{{
  "summary": "<2-3 substantive, detailed sentences defining the job family and concrete role scope based on requirements and responsibilities. Avoid generic filler.>",
  "pros": "<2-3 substantive, detailed sentences detailing personal development, task complexity, and engineering opportunities.>",
  "cons": "<2-3 substantive, detailed sentences detailing operational risks, negative cues, on-call burdens, or career limitations.>"
}}
```

CRITICAL QUALITY RULES:
1. Each field ("summary", "pros", "cons") MUST contain at least 2 to 3 complete, rich, substantive sentences.
2. Do NOT provide short generic phrases or boilerplate slop.
3. Write in third-person objective tone analyzing the position (e.g., "This role falls under the backend engineering family...", "Engineers will benefit from...", "Potential risks include...").
4. The output outside <think> must be strictly valid JSON.
"""


class JobSummaryOutput(BaseModel):
    summary: str = Field(
        description="2-3 detailed sentences defining the job family and concrete role scope based on requirements and responsibilities."
    )
    pros: str = Field(
        description="2-3 detailed sentences detailing personal development, task complexity, and engineering opportunities."
    )
    cons: str = Field(
        description="2-3 detailed sentences detailing operational risks, negative cues, on-call burdens, or career limitations."
    )


class SummarySLM(SLM):
    def __init__(
        self,
        logger: Logger,
        model_path: Path | str | None = None,
        ctx_size: int = 8192,
        max_tokens: int = 2048,
        temperature: float = 0.6,
        ollama_url: str | None = None,
        ollama_model: str = "deepseek-r1:7b",
        thinking_token_limit: int | None = None,
    ):
        self.logger = logger
        self.model_path = model_path
        self.ctx_size = ctx_size
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.ollama_url = ollama_url
        self.ollama_model = ollama_model
        self.thinking_token_limit = thinking_token_limit

        self.llm: Llama | None = None
        self.last_thinking: str = ""

        self.load_model()

    def load_model(self) -> None:
        if self.ollama_url:
            self.logger.info(
                f"[{id(self)}] Configured to use Ollama API at {self.ollama_url} with model {self.ollama_model}."
            )
            return

        self.logger.info(f"[{id(self)}] Loading in-code model from {self.model_path}...")
        if self.model_path is None:
            raise ValueError("model_path must be provided when not using ollama_url")

        self.llm = Llama(
            model_path=str(self.model_path),
            n_gpu_layers=-1,
            n_ctx=self.ctx_size,
            verbose=False,
        )
        self.logger.info(f"[{id(self)}] In-code model loaded successfully.")

    _load_model = load_model

    def _generate_ollama(self, messages: list[dict[str, str]]) -> str:
        import httpx

        assert self.ollama_url is not None
        url = f"{self.ollama_url.rstrip('/')}/api/chat"
        payload = {
            "model": self.ollama_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            message = data.get("message", {})
            return str(message.get("content", ""))

    def _generate_llama_cpp(self, messages: list[dict[str, str]]) -> str:
        assert self.llm is not None, "In-code Llama model is not loaded"
        completion: Any = self.llm.create_chat_completion(
            messages=cast(Any, messages),
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        choice = completion["choices"][0]
        return str(choice["message"]["content"] or "")

    @staticmethod
    def _extract_thinking_and_json(text: str) -> Tuple[str, Dict[str, str]]:
        thinking = ""
        think_match = re.search(r"<think>(.*?)(?:</think>|$)", text, re.DOTALL)
        if think_match:
            thinking = think_match.group(1).strip()

        # Remove think tags and internal reasoning for JSON parsing
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

        # Try extracting markdown json code block
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if json_match:
            raw_json = json_match.group(1)
        else:
            # Fallback to standard JSON curly braces block
            obj_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
            raw_json = obj_match.group(1) if obj_match else cleaned

        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                return thinking, {str(k): str(v) for k, v in parsed.items()}
        except Exception:
            pass

        # Regex fallback in case of unescaped quotes or minor syntax errors
        summary_m = re.search(r'"summary"\s*:\s*"(.*?)(?<!\\)"', raw_json, re.DOTALL)
        pros_m = re.search(r'"pros"\s*:\s*"(.*?)(?<!\\)"', raw_json, re.DOTALL)
        cons_m = re.search(r'"cons"\s*:\s*"(.*?)(?<!\\)"', raw_json, re.DOTALL)

        fallback_data = {
            "summary": summary_m.group(1).replace(r"\"", '"') if summary_m else "",
            "pros": pros_m.group(1).replace(r"\"", '"') if pros_m else "",
            "cons": cons_m.group(1).replace(r"\"", '"') if cons_m else "",
        }
        return thinking, fallback_data

    def generate[O](self, inp: JobForAnalytics, schema: type[O]) -> O:
        job = inp
        user_prompt = USER_PROMPT_TEMPLATE.format(
            title=job.title,
            location=job.location or "Unspecified",
            description=job.description,
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        start_time = time.perf_counter()
        if self.ollama_url:
            raw_response = self._generate_ollama(messages)
        else:
            raw_response = self._generate_llama_cpp(messages)

        thinking, parsed_data = self._extract_thinking_and_json(raw_response)
        elapsed = time.perf_counter() - start_time

        self.last_thinking = thinking
        if thinking:
            self.logger.info(f"[{job.id}] Thinking ({elapsed:.2f}s):\n{thinking}")

        result: Dict[str, str] = {
            "summary": parsed_data.get("summary", "").strip(),
            "pros": parsed_data.get("pros", "").strip(),
            "cons": parsed_data.get("cons", "").strip(),
        }

        self.logger.info(
            f"[{job.id}] Summary generated in {elapsed:.2f}s | "
            f"summary ({len(result['summary'])} chars), "
            f"pros ({len(result['pros'])} chars), "
            f"cons ({len(result['cons'])} chars)"
        )

        if schema is str:
            return cast(O, json.dumps(result, ensure_ascii=False, indent=2))
        if schema is dict:
            return cast(O, result)
        if isinstance(schema, type):
            if issubclass(schema, BaseModel):
                return cast(O, schema.model_validate(result))
            if issubclass(schema, str):
                return cast(O, json.dumps(result, ensure_ascii=False, indent=2))
            if issubclass(schema, dict):
                return cast(O, result)

        return cast(O, json.dumps(result, ensure_ascii=False, indent=2))
