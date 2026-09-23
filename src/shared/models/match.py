from enum import Enum
from uuid import UUID

import uuid6
from pydantic import BaseModel, ConfigDict, Field


class SuitabilityTier(str, Enum):
    SUITABLE = "SUITABLE"
    STRETCH = "STRETCH"
    RUNWAY = "RUNWAY"
    REJECTED = "REJECTED"


class MatchedJob(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

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

    strategic_reason: str = Field(
        default="", description="Reason why a candidate should apply. Omitted when REJECTED."
    )
    rejection_reason: str = Field(
        default="", description="Reason why a job was rejected. Omitted when NOT REJECTED."
    )

    debug: str | None = Field(
        default=None, description="JSON representing raw model output or internal chain of thought."
    )
