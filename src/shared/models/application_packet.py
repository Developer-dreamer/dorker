from typing import List, Optional
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, PrivateAttr


class Material(BaseModel):
    model_config = ConfigDict()

    id: str = Field(
        default="",
        validation_alias=AliasChoices("id", "path"),
        description="""The path of the material inside the XML markdown.
                    Identifies how to reach the text block inside the profile.md like files.""",
    )

    text: str = Field(default="", description="The exact text located inside profile.")


class Question(BaseModel):
    question: str = Field()
    answer: Optional[str] = Field()


class RequestedGeneration(BaseModel):
    cover_letter: bool = Field(default=True)
    questions: bool = Field(default=True)


class OpenAIBaseResponse(BaseModel):
    _debug: Optional[str] = PrivateAttr(default=None)

    @property
    def debug(self) -> Optional[str]:
        return self._debug

    @debug.setter
    def debug(self, value: Optional[str]) -> None:
        self._debug = value


class RequestedCoverletter(OpenAIBaseResponse):
    cover_letter: str = Field(
        description="The cover letter attached to the application.",
    )


class RequestedQuestions(OpenAIBaseResponse):
    questions: List[Question] = Field(default_factory=list)


class RequestedFullResponse(RequestedCoverletter, RequestedQuestions):
    pass


RequestedFullGeneration = RequestedFullResponse


class ApplicationGeneratedResponse(BaseModel):
    cover_letter: Optional[str] = Field(
        default=None,
        description="The cover letter attached to the application.",
    )
    questions: Optional[List[Question]] = Field(default=None)
    debug: Optional[str] = Field(
        default=None, description="JSON string representing model's raw answer."
    )


class ApplicationPacket(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: int = Field(default=0, description="The application ID.")
    job_id: str = Field(description="The id of the job.")
    match_id: UUID = Field(description="The id of the match.")

    summary: Optional[str] = Field(
        default=None, description="The summary of job description and application process."
    )
    cover_letter: Optional[str] = Field(
        default=None, description="The cover letter attached to the application."
    )
    questions: List[Question] = Field(default_factory=list)

    materials: List[Material] = Field(
        default_factory=list,
        description="A list of materials inside the application.",
    )

    debug: Optional[str] = Field(
        default=None, description="JSON string representing model's raw answer."
    )
