from logging import Logger
from pathlib import Path
from typing import Dict, cast

import guidance
from guidance import gen
from guidance.models import LlamaCpp

from src.analytics.protocols import SLM
from src.analytics.utils import log_guidance_step
from src.shared.models import (
    JobForAnalytics,
)


class SummarySLM(SLM):
    def __init__(
        self,
        logger: Logger,
        model_path: Path | None = None,
        ctx_size: int = 8192,
        thinking_token_limit: int = 150,
    ):
        self.logger = logger

        self.model_path = model_path
        self.ctx_size = ctx_size
        self.thinking_token_limit = thinking_token_limit

        self.load_model()

    def load_model(self) -> None:
        self.logger.info(f"[{id(self)}] Loading model...")
        self.llm = LlamaCpp(
            model=self.model_path, n_gpu_layers=-1, n_ctx=self.ctx_size, chat_template="deepseek"
        )
        self.logger.info(f"[{id(self)}] Model loaded.")

    _load_model = load_model

    def generate[O](self, inp: JobForAnalytics, schema: type[O]) -> O:
        llm = self.llm
        debug: Dict[str, str] = dict()
        job = inp

        summary = ""
        with log_guidance_step(self.logger, str(job.id), "summary", llm) as tracker:
            with guidance.user():
                llm += f"""Analyze this job description:{job.description}."""

            with guidance.assistant():
                step = 1
                llm += f"""Step {step} Summarize this job description. What is the job's family
                        (e.g. data engineering, backend, AI engineering, data science)? What's the role scope
                        based on requirements and responsibilities?
                        """
                llm += (
                    "<think>\n"
                    + gen(
                        name="ev_summary",
                        max_tokens=self.thinking_token_limit,
                        temperature=0.5,
                        stop="</think>",
                    )
                    + "\n"
                )
                debug["ev_summary"] = llm["ev_summary"]

                llm += (
                    "Job's summary (2-3 sentences): " + gen(name="summary", max_tokens=100) + "\n\n"
                )
                summary += llm["summary"]
                step += 1

                llm += f"""Step {step} Identify the role's pros? What personal development this job
                        might give to a candidate? How complex and interesting tasks scope might be?
                        """
                llm += (
                    "<think>\n"
                    + gen(
                        name="ev_pros",
                        max_tokens=self.thinking_token_limit,
                        temperature=0.5,
                        stop="</think>",
                    )
                    + "\n"
                )
                debug["ev_pros"] = llm["ev_pros"]

                llm += "Job's pros (2-3 sentences): " + gen(name="pros", max_tokens=100) + "\n\n"
                summary += llm["pros"]
                step += 1

                llm += f"""Step {step} Identify the role's cons? What negative operational cues
                        might be attached to this position (e.g. vibecoding only, no manual code touch, 
                        over-responsibility and 24/7 availability, etc.)?
                        """
                llm += (
                    "<think>\n"
                    + gen(
                        name="ev_cons",
                        max_tokens=self.thinking_token_limit,
                        temperature=0.5,
                        stop="</think>",
                    )
                    + "\n"
                )
                debug["ev_cons"] = llm["ev_cons"]

                llm += "Job's cons (2-3 sentences): " + gen(name="cons", max_tokens=100) + "\n\n"
                summary += llm["cons"]

            tracker["llm"] = llm

        return cast(O, summary)
