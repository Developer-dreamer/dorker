import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from typesafe_sdk import AsyncTypeSafeClient, Noul, NoulCriteria, Score, SystemOneResponse

from .models import JobForAnalytics, MatchedJob, SuitabilityTier


class Jev:
    def __init__(self, client: AsyncTypeSafeClient, state: str) -> None:
        self.client = client
        self.state = state

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
        reraise=True,
    )
    async def classify(self, job: JobForAnalytics) -> MatchedJob:
        full_state = (
            self.state
            + f"""\n
                    <job_payload>
                    {job.model_dump_json()}
                    </job_payload>
                    """
        )

        res = await self.client.system_one(
            state=full_state,
            questions={
                "technical_capability_score": Score(
                    instructions="""
                        How well candidate's profile aligns with the job in terms of technical capability of doing this jobs?
                        Estimate this considering next criteria:
                        - Does core stack aligns with candidate's main technologies: backend languages, cloud infra toolset?
                        - Is there any primary tool that candidate does not possess, and this would affect performance on the work?
                        - Verify if the title is not inflated. For example: title is inflated if it requires Senior engineer, 
                        however requirements and responsibilities describe an average Strong Junior/Middle role, 
                        so candidate might be suitable to it, even if he is not a Senior by only number of years of experience in profile.
                    """,
                    criteria=[
                        "Unviable: Core stack mismatch or missing non-negotiable primary technologies.",
                        "Poor: Significant technology gaps requiring substantial retraining.",
                        "Moderate: Core concepts match, but missing secondary tools or requires slight ramp-up.",
                        "Strong: Core stack matches almost completely, minor gaps only.",
                        "Exceptional: Complete stack alignment, matches distributed systems and infra requirements directly.",
                    ],
                ),
                "strategic_value_score": Score(
                    instructions="""
                        How well candidate's job preferences aligns with the job?
                        Estimate this considering next criteria:
                        - How comprehensively role satisfies candidates preferences?
                        - How high strategic value it gives to the candidate? Strategic value definition: 
                        how well candidate might improve his skills and expand his knowledge doing this job, 
                        and how much more competitive it would represent the candidate on the job market after resignation.
                    """,
                    criteria=[
                        "Harmful: Severely underpaid, legacy monolithic stack, or low-code/no-code maintenance.",
                        "Low: Standard monolithic web CRUD with minimal growth potential.",
                        "Neutral: Acceptable compensation and standard backend development, but average technical upside.",
                        "High: Solid compensation, modern backend/distributed systems work.",
                        "Optimal: High compensation, deep focus on AI infrastructure, distributed systems, or custom protocol design.",
                    ],
                ),
                "location_prohibited": Noul(
                    instructions="Does this job posting contain explicit, non-negotiable legal restrictions that prevent hiring an independent contractor (ФОП) residing in Ukraine?",
                    criteria=NoulCriteria(
                        true="ONLY IF the text explicitly states requirements such as: active government security clearance, mandatory US/EU citizenship or permanent residency, strict W-2 domestic payroll only, or explicit geographical bans.",
                        false="IF the text states 'Remote', mentions worldwide/EMEA hiring, or does not explicitly restrict residency.",
                    ),
                ),
            },
        )

        return self._classify_local(job.id, res)

    @staticmethod
    def _classify_local(j_id: str, resp: SystemOneResponse) -> MatchedJob:
        matched_job = MatchedJob(job_id=j_id)

        raw_tech_score = resp.scores["technical_capability_score"]
        raw_strat_score = resp.scores["strategic_value_score"]

        # 2. Normalize to a 0.0 - 1.0 scale
        tech_score = round(raw_tech_score.score / (len(raw_tech_score.legend) - 1), 2)
        strat_score = round(raw_strat_score.score / (len(raw_strat_score.legend) - 1), 2)

        matched_job.technical_capability_score = tech_score
        matched_job.strategic_value_score = strat_score

        location_prohibited = resp.nouls["location_prohibited"]

        # 2. Evaluate the Hard Gate (Blacklist)
        # If the probability of being explicitly prohibited is > 60%, reject it.
        if location_prohibited.noul > 0.60:
            matched_job.suitability_tier = SuitabilityTier.REJECTED
            matched_job.rejection_reason = "Location explicitly prohibited by requirements."
            return matched_job

        if tech_score >= 0.75 and strat_score >= 0.75:
            tier = SuitabilityTier.SUITABLE
        elif tech_score < 0.75 and strat_score >= 0.75:
            tier = SuitabilityTier.STRETCH
        elif tech_score >= 0.50 and strat_score >= 0.50:
            tier = SuitabilityTier.RUNWAY
        else:
            tier = SuitabilityTier.REJECTED
            matched_job.rejection_reason = "Fails to meet baseline Level 2 (0.50) criteria."

        matched_job.suitability_tier = tier
        matched_job.debug = resp.model_dump_json()

        return matched_job
