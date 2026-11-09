"""HTTP request and response contracts for API handlers."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

from src.shared.models.match import SuitabilityTier


class JobResponse(BaseModel):
    id: str
    title: str
    company: str | None
    location: str | None
    description: str
    url: str
    apply_url: str | None
    ats_type: str
    employment_type: str | None
    questions: list[str]
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    posted_at: datetime | None
    fetched_at: datetime | None


class JobsResponse(BaseModel):
    jobs: list[JobResponse]
    total: int
    limit: int
    offset: int


class PipelineStatus(StrEnum):
    PENDING = "PENDING"
    APPLIED = "APPLIED"
    GHOSTED = "GHOSTED"
    INTERVIEWING = "INTERVIEWING"
    REJECTED_EARLY = "REJECTED_EARLY"
    REJECTED_LATE = "REJECTED_LATE"
    DECLINED = "DECLINED"
    OFFER = "OFFER"
    IGNORED = "IGNORED"
    SKIPPED = "SKIPPED"


class MatchAnalytics(BaseModel):
    summary: str
    pros: list[str]
    cons: list[str]


class MatchResponse(BaseModel):
    id: UUID
    job_id: str
    suitability_tier: SuitabilityTier
    pipeline_status: PipelineStatus
    technical_capability_score: float
    strategic_value_score: float
    analytics: MatchAnalytics | None
    rejection_reason: str | None
    created_at: datetime
    updated_at: datetime | None = None


class MatchesResponse(BaseModel):
    matches: list[MatchResponse]
    total: int
    limit: int
    offset: int


class MatchDebugResponse(BaseModel):
    debug: str | None


class ScrapeJobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl


class ScrapeJobResponse(BaseModel):
    job: JobResponse
    match: MatchResponse


class ReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16000)]


class StatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: PipelineStatus


class DiscussionMessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16000)]


class DiscussionState(StrEnum):
    """Only not_started -> open -> finished is allowed; finished is terminal."""

    NOT_STARTED = "not_started"
    OPEN = "open"
    FINISHED = "finished"


class DiscussionMessageResponse(BaseModel):
    """A persisted, user-visible message; excludes internal context and provider metadata."""

    id: UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class DiscussionResponse(BaseModel):
    state: DiscussionState
    messages: list[DiscussionMessageResponse] = Field(
        description="Visible messages in chronological order, excluding context and action records."
    )


class DiscussionReplyResponse(BaseModel):
    """Both messages are persisted before the regular reply completes."""

    user_message: DiscussionMessageResponse
    assistant_message: DiscussionMessageResponse


class DiscussionAcceptedEvent(BaseModel):
    type: Literal["accepted"] = "accepted"
    message: DiscussionMessageResponse = Field(description="The persisted user message.")


class DiscussionDeltaEvent(BaseModel):
    type: Literal["delta"] = "delta"
    text: str = Field(description="Incremental reply text; not yet a persisted assistant message.")


class DiscussionDoneEvent(BaseModel):
    type: Literal["done"] = "done"
    message: DiscussionMessageResponse = Field(
        description="The complete persisted assistant message, replacing any accumulated deltas."
    )


DiscussionReplyEvent = Annotated[
    DiscussionAcceptedEvent | DiscussionDeltaEvent | DiscussionDoneEvent,
    Field(discriminator="type"),
]


class DiscussionErrorEvent(BaseModel):
    """Terminal stream failure; no further reply events follow."""

    type: Literal["error"] = "error"
    detail: str
    saved: bool = Field(
        description="Whether the user message was persisted; does not imply an assistant reply exists."
    )


ApplicationQuestionText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16000)
]


class GenerationInput(BaseModel):
    """Select outputs for creation or replacement; other draft sections remain untouched."""

    model_config = ConfigDict(extra="forbid")

    generate_cover_letter: bool = True
    generate_answers: bool = True
    questions: list[ApplicationQuestionText] = Field(
        default_factory=list,
        description="Explicit question texts to answer; no fallback to stored job questions.",
    )

    @model_validator(mode="after")
    def require_output_and_questions(self) -> Self:
        if not self.generate_cover_letter and not self.generate_answers:
            raise ValueError("Select cover letter generation, answer generation, or both.")
        if self.generate_answers and not self.questions:
            raise ValueError("Supply at least one question when generating answers.")
        return self


class ApplicationQuestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: ApplicationQuestionText
    answer: str | None = Field(
        default=None, description="Null means this question awaits an answer."
    )


class ApplicationResponse(BaseModel):
    """Saved application materials; submission status belongs to the associated match."""

    id: int
    match_id: UUID
    job_id: str
    cover_letter: str | None
    questions: list[ApplicationQuestionResponse]
    created_at: datetime
    updated_at: datetime | None


class ApplicationsResponse(BaseModel):
    applications: list[ApplicationResponse]
    total: int
    limit: int
    offset: int


class ApplicationPatchInput(BaseModel):
    """Use model_fields_set or model_dump(exclude_unset=True) to preserve omitted fields.

    Explicit null clears the letter. A supplied questions list replaces the entire
    Q&A section; an empty list clears it. Authored letters and answers are not trimmed.
    """

    model_config = ConfigDict(extra="forbid")

    cover_letter: str | None = None
    questions: list[ApplicationQuestionResponse] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_changes(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Supply at least one draft field to update.")
        return self
