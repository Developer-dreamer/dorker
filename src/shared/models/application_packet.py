from typing import List, Optional

from pyarrow.lib import UUID
from pydantic import BaseModel, ConfigDict, Field


class Material(BaseModel):
    model_config = ConfigDict()

    path: str = Field(
        default="",
        description="""The path of the material inside the XML markdown.
                    Identifies how to reach the text block inside the profile.md like files.""",
    )

    text: str = Field(default="", description="The exact text located inside profile.")


class ApplicationGeneratedResponse(BaseModel):
    summary: str = Field(
        description="The summary of the application.",
    )
    follow_up_message: str = Field(
        description="The follow up message when short application message required.",
    )
    cover_letter: str = Field(
        description="The cover letter attached to the application.",
    )

    debug: Optional[str] = Field(default=None, description="The debug of llm thinking")


class ApplicationPacket(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: int = Field(default=0, description="The application ID.")
    job_id: str = Field(description="The id of the job.")
    match_id: UUID = Field(description="The id of the match.")

    summary: Optional[str] = Field(
        default=None, description="The summary of job description and application process."
    )
    follow_up_message: Optional[str] = Field(
        default=None, description="Follow up message when short application message required."
    )
    cover_letter: Optional[str] = Field(
        default=None, description="The cover letter attached to the application."
    )

    materials: List[Material] = Field(
        default_factory=list,
        description="A list of materials inside the application.",
    )

    debug: Optional[str] = Field(
        default=None, description="JSON string representing model's raw answer."
    )
