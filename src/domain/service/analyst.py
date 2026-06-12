from typing import Any

from src.config.logger import Logger
from src.domain.model.job import Job, extract_metadata, extract_questions
from src.domain.model.match import MatchedJob
from src.domain.model.prompt import PromptStructure
from src.infra.ai.gemini import GeminiProClient
from src.infra.db.sql_lite.job_repository import JobRepository
from src.infra.db.sql_lite.match_repository import MatchRepository


class JobAnalyst:

    def __init__(self, logger: Logger, ai_client: GeminiProClient, job_repo: JobRepository,
                 match_repo: MatchRepository,
                 prompt_template: PromptStructure,
                 profile: str):
        self.logger = logger
        self.ai_client = ai_client
        self.prompt_template = prompt_template
        self.user_profile = profile
        self.job_repository = job_repo
        self.match_repository = match_repo

    async def find_top_matches(self) -> list[tuple[(str, MatchedJob)]]:
        try:

            profile = f"<candidate_context>{self.user_profile}</candidate_context>"

            self.ai_client.init_cache(data=profile,
                                        system_instruction=self.prompt_template.master_prompt_with_generation)

            matched_jobs: list[tuple[(str, MatchedJob)]] = []

            async for job in self.job_repository.get_relevant_jobs():
                job_payload = f"""
                        Here is a job. Match it according to master prompt and candidate's context
                        <job_payload>
                        # Source: user
                        {job}
                        <job_payload>
                            """

                # asking model to generate the matching criteria
                response = await self.ai_client.generate_json(job_payload)

                matched_job = MatchedJob.model_validate_json(response)

                await self.match_repository.save_new_match(job["id"], 1, matched_job)

                if not matched_job.is_match:
                    continue

                matched_jobs.append((job["url"], matched_job))

            return matched_jobs

        except Exception as e:
            self.logger.info("Find top matches failed with exception:", exception=e)
            self.ai_client.reset_cache()
            return []


    def _build_quick_match_prompt(self, job: Job | str) -> str:
        return ""

    def _build_job_block(self, job: dict[str, Any]) -> str:
        return ""

    def _build_core_prompt(self, job: Job | str) -> str:
        full_payload =  f"""{self.prompt_template.master_prompt_with_generation}

                            <candidate_context>
                            {self.user_profile}
                            </candidate_context>

                            <job_payload>
                            # Source: {"user" if isinstance(job, str) else "scraper"}
                            {{job}}
                            <job_payload>"""

        # Forming core template
        match job:
            case str(): # The job description comes as a raw text from bot
                full_payload = full_payload.format(job=job)
            case Job(): # The job description comes from scraped after parsing and normaliation
                job_payload = f"""<metadata>
                                {extract_metadata(job)}
                                </metadata>
                                <description>
                                {job.description}
                                </description>
                                <application_questions>
                                {extract_questions(job)}
                                </application_questions>"""

                full_payload = full_payload.format(job=job_payload)

        return full_payload

