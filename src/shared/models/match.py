from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

import uuid6
from pydantic import BaseModel, ConfigDict, Field


class SuitabilityTier(str, Enum):
    SUITABLE = "SUITABLE"
    STRETCH = "STRETCH"
    RUNWAY = "RUNWAY"
    REJECTED = "REJECTED"


class JobAnalytics(BaseModel):
    """Structured overview; repository reads adapt historical plain text to this shape."""

    summary: str = Field(
        description="2-3 detailed sentences defining the job family and concrete role scope based on requirements and responsibilities."
    )
    pros: str = Field(
        default="",
        description="2-3 detailed sentences detailing personal development, task complexity, and engineering opportunities.",
    )
    cons: str = Field(
        default="",
        description="2-3 detailed sentences detailing operational risks, negative cues, on-call burdens, or career limitations.",
    )


class MatchedJob(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(default_factory=uuid6.uuid7)
    job_id: str = Field(description="Job associated with this match.")

    technical_capability_score: float = Field(
        default=0.0,
        description="Value representing how good candidate's stack aligns with role's required.",
    )
    strategic_value_score: float = Field(
        default=0.0,
        description="Value representing how good job description aligns with candidate's search preferences.",
    )
    suitability_tier: SuitabilityTier = Field(
        default=SuitabilityTier.SUITABLE,
        description="""Computed directly from technical_capability_score and strategic_value_score and constraints α and β respectively.
                    - SUITABLE: technical_capability_score >= α AND strategic_value_score >= β
                    - STRETCH: technical_capability_score < α AND strategic_value_score >= β
                    - RUNWAY: technical_capability_score >= α AND strategic_value_score < β
                    - REJECTED: technical_capability_score < α AND strategic_value_score < β OR if failed other constraints like location.
                    """,
    )

    pipeline_status: str = Field(default="PENDING")
    created_at: datetime | None = Field(
        default=None, description="Database creation time; absent on unsaved classifier results."
    )

    job_summary: Optional[JobAnalytics] = Field(
        default=None, description="Short overview of the position."
    )
    rejection_reason: Optional[str] = Field(
        default=None, description="Reason why a job was rejected. Omitted when NOT REJECTED."
    )

    debug: Optional[str] = Field(
        default=None, description="JSON representing raw model output or internal chain of thought."
    )


class MatchCounts(BaseModel):
    """Counts over the repository's eligible latest matches, including empty tiers."""

    suitable: int = 0
    runway: int = 0
    stretch: int = 0
    rejected: int = 0

    def for_tier(self, tier: SuitabilityTier) -> int:
        match tier:
            case SuitabilityTier.SUITABLE:
                return self.suitable
            case SuitabilityTier.RUNWAY:
                return self.runway
            case SuitabilityTier.STRETCH:
                return self.stretch
            case SuitabilityTier.REJECTED:
                return self.rejected
