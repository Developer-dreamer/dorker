from enum import Enum
from typing import List

from pydantic import BaseModel, ConfigDict, Field

# ==========================================
# Sub-Models for internal_analysis_cot
# ==========================================

class ScoringMath(BaseModel):
    initial_technical_score: float
    technical_deductions_applied: List[str]
    initial_strategic_score: float
    strategic_deductions_applied: List[str]
    calculated_technical_capability_score: float
    calculated_strategic_value_score: float


class ChainOfThought(BaseModel):
    # Field aliases map the JSON keys to Pythonic variable names
    normalization: str = Field(alias="step_0_normalization")
    constraints: str = Field(alias="step_1_constraints")
    alignment: str = Field(alias="step_2_alignment")
    math: ScoringMath = Field(alias="step_3_scoring_math")


# ==========================================
# Sibling Models for MatchedJob
# ==========================================

class Metadata(BaseModel):
    extracted_company: str
    extracted_title: str
    extracted_location_status: str


class ScoringBreakdown(BaseModel):
    initial_score: float
    phase_1_fatal_violations: List[str]
    phase_2_experience_deductions: List[str]
    phase_3_technical_deductions: List[str]
    phase_3_waiver_applied: bool


class ApplicationStatus(str, Enum):
    SUITABLE = "SUITABLE"
    STRETCH = "STRETCH"
    RUNWAY = "RUNWAY"
    REJECTED = "REJECTED"


class Analytics(BaseModel):
    pros: List[str]
    cons: List[str]
    warnings: List[str]


class ApplicationFormAnswer(BaseModel):
    question: str
    answer: str


# ==========================================
# Main Root Model
# ==========================================

class MatchedJob(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    internal_analysis_cot: ChainOfThought
    metadata: Metadata
    internal_scoring_breakdown: ScoringBreakdown
    is_match: bool
    technical_capability_score: float
    strategic_value_score: float
    application_status: ApplicationStatus
    strategic_reason: str
    analytics: Analytics
    cv_modification_points: List[str]
    tailored_cover_letter: str
    application_form_answers: List[ApplicationFormAnswer]
