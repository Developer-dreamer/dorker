import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

EmploymentType = Literal["FULL_TIME", "PART_TIME", "CONTRACT", "INTERN", "TEMPORARY"]

class Job(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ats_type: str = Field(...,
        description="The target applicant tracking system platform (e.g., 'greenhouse', 'lever', 'ashby').")
    ats_id: str = Field(...,
        description="The unique, platform-specific identifier assigned to the posting by the source ATS.")
    url: HttpUrl = Field(...,
        description="The direct public career page URL. Used as the primary stable tracking link.")
    apply_url: HttpUrl | None = Field(
        default=None,
        description="The dedicated endpoint for submitting applications, if distinct from the posting page URL.")

    title: str = Field(
        ...,
        description="The unformatted, literal job title as listed by the employer."
    )
    company_slug: str = Field(
        ...,
        description="The normalized identifier of the hiring entity mapped across the system."
    )
    location: str | None = Field(
        default=None,
        description="The free-form raw location string extracted directly from the posting headers."
    )
    is_remote: bool = Field(
        default=False,
        description="Boolean flag indicating if the role supports a 100% remote working model."
    )
    employment_type: EmploymentType | None = Field(
        default="FULL_TIME",
        description="The cross-ATS normalized employment category used for strict database filtering."
    )
    description: str = Field(
        ...,
        description="Clean, plain-text job description with all HTML/Markdown tags stripped for LLM consumption."
    )

    # --- Structured Compensation Architecture ---
    salary_min: float | None = Field(
        default=None,
        description="The evaluated lower bound of the base compensation range."
    )
    salary_max: float | None = Field(
        default=None,
        description="The evaluated upper bound of the base compensation range."
    )
    salary_currency: str | None = Field(
        default=None,
        description="The three-letter ISO 4217 currency code representing the compensation framework."
    )

    # --- Proprietary Application Funnel Enrichment ---
    application_questions: list[dict] | None = Field(
        default=None,
        description="Structured dictionary representation of custom application form inputs required by the ATS."
    )

    # --- System Metrics & Lifecycle Timestamps ---
    posted_at: datetime | None = Field(
        default=None,
        description="The initial publication timestamp reported by the source ATS ecosystem (UTC)."
    )
    fetched_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="The exact system timestamp recording when the row was ingested into the platform database (UTC)."
    )

def extract_metadata(job: Job) -> str:
    """Extracts critical positioning and contextual metadata from a CompactJob instance

    to populate the LLM's initial verification and scoring gates.
    """
    # 1. Format structured compensation safely
    if job.salary_currency and (job.salary_min is not None or job.salary_max is not None):
        min_amt = f"{job.salary_min:,.0f}" if job.salary_min is not None else "Unspecified"
        max_amt = f"{job.salary_max:,.0f}" if job.salary_max is not None else "Unspecified"
        salary_str = f"{min_amt} - {max_amt} {job.salary_currency}"
    else:
        salary_str = "Not disclosed / Competitive"

    # 2. Map Pydantic attributes to uniform English labels for prompt parsing
    metadata_map = {
        "Title": job.title,
        "Company Name": job.company_slug,
        "Salary Range": salary_str,
        "Location": job.location or "Not specified",
        "Is Remote": "Yes" if job.is_remote else "No",
        "Employment Type": job.employment_type or "FULL_TIME",
        "Source URL": str(job.url),
        "Date Published": job.posted_at.isoformat() if job.posted_at else "Unknown",
    }

    lines = [f"{key}: {value}" for key, value in metadata_map.items()]
    return ",\n".join(lines)


def extract_questions(job: Job) -> str:
    """Extracts custom application form questions required by the ATS.

    Serializes the structured dictionary objects into clean, sequential strings
    for target LLM extraction and answering passes.
    """
    if not job.application_questions:
        return ""

    formatted_questions = []

    for index, q_dict in enumerate(job.application_questions, start=1):
        if not isinstance(q_dict, dict):
            continue

        # Extract the human-readable question text based on common ATS form payloads
        # (checks 'label', 'text', 'name', or falls back to standard key-value match)
        question_text = (
            q_dict.get("label") or
            q_dict.get("text") or
            q_dict.get("name") or
            q_dict.get("description")
        )

        # If the schema structure is entirely custom/nested, serialize the raw entity
        if not question_text:
            question_text = json.dumps(q_dict, ensure_ascii=False)

        # Capture requirement flags if surfaced by the platform scraper
        required_flag = " (Required)" if q_dict.get("required") or q_dict.get("is_required") else ""

        formatted_questions.append(f"Question {index}: {str(question_text).strip()}{required_flag}")

    return ",\n".join(formatted_questions)
