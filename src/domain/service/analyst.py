from config.logger import Logger
from domain.model.job import Job, extract_metadata, extract_question
from domain.model.prompt import PromptStructure
from infra.ai.gemini import GeminiFlashClient


class JobAnalyst:

    def __init__(self, logger: Logger, ai_client: GeminiFlashClient,
                 prompt_template: PromptStructure,
                 profile: str):
        self.logger = logger
        self.ai_client = ai_client
        self.prompt_template = prompt_template
        self.user_profile = profile

    def _build_quick_match_prompt(self, job: Job | str) -> str:
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
                                {extract_question(job)}
                                </application_questions>"""

                full_payload = full_payload.format(job=job_payload)

        return full_payload
