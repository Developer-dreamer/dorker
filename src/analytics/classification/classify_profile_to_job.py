from typing import Dict, List, Tuple

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, NoulCriteria, SystemOneResponse

from src.analytics.protocols import Classifier, ProfileRepository
from src.shared.models import ApplicationPacket, JobForAnalytics, Material


class ClassifyProfileToJob(Classifier[ApplicationPacket]):
    def __init__(self, client: AsyncTypeSafeClient, reject_block_threshold: float = 0.65) -> None:
        self.client = client
        self.reject_block_threshold = reject_block_threshold

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
        reraise=True,
    )
    async def classify(self, job: JobForAnalytics, profile: ProfileRepository) -> ApplicationPacket:
        full_state = (
            profile.full_dump()
            + f"""\n
                <job_payload>
                {job.description}
                </job_payload>
                """
        )

        materials, questions = self._generate_request_questions(profile)

        res = await self.client.system_one(state=full_state, questions=questions)

        return self._classify_local(job, materials, res)

    def _generate_request_questions(
        self, profile: ProfileRepository
    ) -> Tuple[list[Material], Dict[str, Noul | Choice]]:
        materials: List[Material] = []
        questions: Dict[str, Noul | Choice] = dict()

        general_noul_criteria = NoulCriteria(
            true="The block explicitly mentions a transferable technical skill, tool, or action required by the job "
            "(e.g., conducting code reviews, writing automated tests, debugging, or using a specific language like Python/Go), "
            "even if the overarching job title or project domain seems unrelated.",
            false="The block contains zero overlapping technical skills or responsibilities with the target job.",
        )

        # query all experience blocks
        experience = profile.get_experience()
        materials.extend(experience)
        for material in experience:
            key = material.id
            questions[key] = Noul(
                instructions=(
                    f"Evaluate the candidate's historical experience block at XML path '{key}'. "
                    "Does this block demonstrate ANY transferable skills (e.g., code reviewing, specific testing frameworks, Python/Go programming, or debugging) "
                    "that directly fulfill a requirement in the Target Job Description? "
                    "Do not evaluate the relevance of the candidate's historical job title or project name. "
                    "Focus exclusively on whether the technical actions, tools, and responsibilities executed in this specific block match the target job's requirements."
                ),
                criteria=general_noul_criteria,
            )

        # query all personal projects
        projects = profile.get_projects()
        materials.extend(projects)
        for material in projects:
            key = material.id
            questions[key] = Noul(
                instructions=(
                    f"Evaluate the candidate's historical experience block at XML path '{key}'. "
                    "Does this block demonstrate ANY transferable skills (e.g., code reviewing, specific testing frameworks, Python/Go programming, or debugging) "
                    "that directly fulfill a requirement in the Target Job Description? "
                    "Do not evaluate the relevance of the candidate's historical job title or project name. "
                    "Focus exclusively on whether the technical actions, tools, and responsibilities executed in this specific block match the target job's requirements."
                ),
                criteria=general_noul_criteria,
            )

        letters = profile.get_cover_letter_examples()
        materials.extend(letters)
        letter_criteria: Dict[str, str] = dict()
        for material in letters:
            key = material.id
            letter_criteria[key] = (
                f"Select this option if the cover letter located at XML path '{key}' is the most suitable match for the job description."
            )

        questions["cover_letter_selection"] = Choice(
            instructions="Evaluate the provided cover letters in the XML profile. Which specific cover letter path represents the best foundational match for this job description's technical and cultural requirements?",
            criteria=letter_criteria,
        )

        # canonical written interview questions
        canonical_written_interview_block = profile.get_dossier()
        materials.extend(canonical_written_interview_block)
        for material in canonical_written_interview_block:
            key = material.id

            questions[key] = Noul(
                instructions=f"How relevant answer to the question from Canonical written interview by the path {key} inside xml profile would be suitable for this job?",
            )

        # academic courses
        academic_courses = profile.get_coursework()
        materials.extend(academic_courses)
        for material in academic_courses:
            key = material.id
            questions[key] = Noul(
                instructions=f"How relevant this course by the path {key} inside xml profile would be suitable for this job?",
            )

        return materials, questions

    def _classify_local(
        self, job: JobForAnalytics, materials: list[Material], res: SystemOneResponse
    ) -> ApplicationPacket:
        approved_materials: List[Material] = []

        # Create a fast lookup dictionary from your materials list
        material_lookup = {m.id: m for m in materials}

        for key, answer in res.answers.items():
            # 1. Handle Cover Letter Selection (Choice primitive)
            if key == "cover_letter_selection" and hasattr(answer, "choice"):
                # Extract the chosen letter's path
                selected_path = answer.choice
                if selected_path in material_lookup:
                    approved_materials.append(material_lookup[selected_path])
                continue

            # 2. Handle Profile Blocks (Noul primitive)
            if answer.type == "noul":
                # The Threshold: Keep blocks with >= 60% probability of relevance
                if answer.noul >= self.reject_block_threshold:
                    if key in material_lookup:
                        approved_materials.append(material_lookup[key])
                elif "project.dorker" in key:
                    approved_materials.append(material_lookup[key])

        assert job.match is not None

        packet = ApplicationPacket(
            job_id=job.id,
            match_id=job.match.id,
            materials=approved_materials,
            debug=res.model_dump_json(),
        )
        return packet
