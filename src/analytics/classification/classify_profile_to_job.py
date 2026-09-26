from pathlib import Path
from typing import Dict, Iterator, List, Tuple

import httpx
from bs4 import BeautifulSoup, Tag
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, NoulCriteria, SystemOneResponse

from src.shared.models import ApplicationPacket, JobForAnalytics, Material

from .protocols import Classifier


class ClassifyProfileToJob(Classifier[ApplicationPacket]):
    def __init__(
        self, client: AsyncTypeSafeClient, profile_path: Path, reject_block_threshold: float = 0.65
    ) -> None:
        self.client = client
        self.profile_path = profile_path
        self.reject_block_threshold = reject_block_threshold

        self._generate_request_questions()

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
        reraise=True,
    )
    async def classify(self, job: JobForAnalytics) -> ApplicationPacket:
        full_state = (
            self.state
            + f"""\n
                    <job_payload>
                    {job.model_dump_json()}
                    </job_payload>
                    """
        )

        res = await self.client.system_one(state=full_state, questions=self.questions)

        return self._classify_local(job, res)

    def _generate_request_questions(self) -> None:
        self.questions: Dict[str, Noul | Choice] = dict()
        self.materials: List[Material] = []

        with open(self.profile_path, "r") as f:
            # Use the "xml" parser to strictly respect your custom tag names
            # instead of converting them to lowercase HTML standard tags
            content = f.read()
            self.state = content
            soup = BeautifulSoup(content, "xml")

        general_noul_criteria = NoulCriteria(
            true="The block explicitly mentions a transferable technical skill, tool, or action required by the job "
            "(e.g., conducting code reviews, writing automated tests, debugging, or using a specific language like Python/Go), "
            "even if the overarching job title or project domain seems unrelated.",
            false="The block contains zero overlapping technical skills or responsibilities with the target job.",
        )

        # query all experience blocks
        for key, value in self._query_block(soup, "job", "company"):
            self.materials.append(Material(path=key, text=value))
            self.questions[key] = Noul(
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
        for key, value in self._query_block(soup, "project", "name"):
            self.materials.append(Material(path=key, text=value))
            self.questions[key] = Noul(
                instructions=(
                    f"Evaluate the candidate's historical experience block at XML path '{key}'. "
                    "Does this block demonstrate ANY transferable skills (e.g., code reviewing, specific testing frameworks, Python/Go programming, or debugging) "
                    "that directly fulfill a requirement in the Target Job Description? "
                    "Do not evaluate the relevance of the candidate's historical job title or project name. "
                    "Focus exclusively on whether the technical actions, tools, and responsibilities executed in this specific block match the target job's requirements."
                ),
                criteria=general_noul_criteria,
            )

        letter_criteria: Dict[str, str] = dict()
        for key, value in self._query_block(soup, "letter", "id"):
            self.materials.append(Material(path=key, text=value))
            letter_criteria[key] = (
                f"Select this option if the cover letter located at XML path '{key}' is the most suitable match for the job description."
            )

        self.questions["cover_letter_selection"] = Choice(
            instructions="Evaluate the provided cover letters in the XML profile. Which specific cover letter path represents the best foundational match for this job description's technical and cultural requirements?",
            criteria=letter_criteria,
        )

        # canonical written interview questions
        for key, value in self._query_block(soup, "section", "question_id"):
            self.materials.append(Material(path=key, text=value))
            self.questions[key] = Noul(
                instructions=f"How relevant answer to the question from Canonical written interview by the path {key} inside xml profile would be suitable for this job?",
            )

        # academic courses
        for key, value in self._query_block(soup, "academic_course", "code"):
            self.materials.append(Material(path=key, text=value))
            self.questions[key] = Noul(
                instructions=f"How relevant this course by the path {key} inside xml profile would be suitable for this job?",
            )

    @staticmethod
    def _query_block(
        soup: BeautifulSoup, tag_name: str, attr_key: str
    ) -> Iterator[Tuple[str, str]]:
        # Extract all blocks that match tag name. E.g. all <job>, <project>, etc.
        for tag in soup.find_all(tag_name):
            attr_value = str(tag.get(attr_key, "unknown"))

            # Traverse upward to capture parent tag names ('commercial', 'experience')
            hierarchy: list[str] = []
            for parent in tag.parents:
                if isinstance(parent, Tag) and parent.name != "[document]":
                    hierarchy.append(parent.name)

            # Invert to root-to-leaf order: ['experience', 'commercial']
            hierarchy.reverse()

            # Form the path identifier: "experience.commercial.job.pixelview"
            key = ".".join(hierarchy + [tag_name, attr_value])
            value = tag.get_text(strip=True)
            # Strip excess leading/trailing whitespace while preserving Markdown lines
            yield key, value

    def _classify_local(self, job: JobForAnalytics, res: SystemOneResponse) -> ApplicationPacket:
        approved_materials: List[Material] = []

        # Create a fast lookup dictionary from your materials list
        material_lookup = {m.path: m for m in self.materials}

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

        assert job.match is not None

        packet = ApplicationPacket(
            job_id=job.id,
            match_id=job.match.id,
            materials=approved_materials,
            debug=res.model_dump_json(),
        )
        return packet
