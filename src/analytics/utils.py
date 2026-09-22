import time
from contextlib import contextmanager
from logging import Logger
from typing import Any, Generator

from guidance.models import LlamaCpp


@contextmanager
def log_guidance_step(
    logger: Logger, job_id: str, step_name: str, lm_initial: LlamaCpp
) -> Generator[dict[str, Any], None, None]:
    start_time = time.perf_counter()
    initial_tokens = len(lm_initial)
    metrics = {"llm": lm_initial}

    try:
        yield metrics
    finally:
        elapsed = time.perf_counter() - start_time
        lm_final = metrics.get("llm", lm_initial)
        final_tokens = len(lm_final)

        generated_tokens = max(0, final_tokens - initial_tokens)
        gen_tps = (generated_tokens / elapsed) if elapsed > 0 else 0.0

        logger.info(
            f"[{job_id}] {step_name} completed in {elapsed:.2f}s | "
            f"Prompt: {initial_tokens} tok | Gen: {generated_tokens} tok ({gen_tps:.1f} tok/s) | "
            f"Total context: {final_tokens} tok"
        )
