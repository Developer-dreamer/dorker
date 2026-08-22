from enum import Enum
from typing import List

from pydantic import BaseModel, ConfigDict


class ApplicationStatus(str, Enum):
    SUITABLE = "SUITABLE"
    STRETCH = "STRETCH"
    RUNWAY = "RUNWAY"
    REJECTED = "REJECTED"


class Analytics(BaseModel):
    pros: List[str]
    cons: List[str]
    warnings: List[str]


# ==========================================
# Main Root Model
# ==========================================

class MatchedJob(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    is_match: bool
    technical_capability_score: float
    strategic_value_score: float
    application_status: ApplicationStatus
    strategic_reason: str
    analytics: Analytics




class OpenAIBatch(BaseModel):
    pass
