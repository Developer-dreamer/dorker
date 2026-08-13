from datetime import datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, JsonValue

from location import evaluate_location_relevance

# ===== MODELS =====
EmploymentType = Literal["FULL_TIME", "PART_TIME", "CONTRACT", "INTERN", "TEMPORARY"]
IsRemote = Literal[
    "REJECT_EMPTY", "REJECT_NO_REMOTE_CONTEXT", "KEEP_GLOBAL", "EXCLUDE_US",
    "REJECT_HYBRID_NON_LOCAL", "POTENTIAL_PURE", "EXCLUDE_US_STATE",
    "EXCLUDE_OTHER_COUNTRY", "KEEP_GLOBAL", "KEEP_LOCAL", "KEEP_PURE"
]

class Job(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ats_type: str = Field(..., description="The target applicant tracking system platform.")
    ats_id: str = Field(..., description="The unique, platform-specific identifier.")
    url: HttpUrl = Field(..., description="The direct public career page URL.")
    apply_url: HttpUrl | None = Field(default=None, description="The dedicated endpoint for submitting applications.")
    title: str = Field(..., description="The unformatted, literal job title.")
    company_slug: str = Field(..., description="The normalized identifier of the hiring entity.")
    location: str | None = Field(default=None, description="The free-form raw location string.")
    is_remote: IsRemote = Field(default="POTENTIAL_PURE", description="Remote classification logic result.")
    employment_type: EmploymentType | None = Field(default="FULL_TIME", description="Normalized employment category.")
    description: str = Field(..., description="Clean, plain-text job description.")
    salary_min: float | None = Field(default=None, description="Evaluated lower bound of base compensation.")
    salary_max: float | None = Field(default=None, description="Evaluated upper bound of base compensation.")
    salary_currency: str | None = Field(default=None, description="ISO 4217 currency code.")
    application_questions: list[JsonValue] | None = Field(default=None, description="Structured dictionary of custom inputs.")
    posted_at: datetime | None = Field(default=None, description="Initial publication timestamp.")
    fetched_at: datetime = Field(default_factory=datetime.utcnow, description="System timestamp for ingestion.")

def map_row_to_domain(row: dict[str, Any]) -> Job:
    emp_type: EmploymentType = "FULL_TIME"
    raw_emp = row.get("employment_type")
    if raw_emp in ["FULL_TIME", "PART_TIME", "CONTRACT", "INTERN", "TEMPORARY"]:
        emp_type = raw_emp

    posted_at = None
    if row.get("posted_at"):
        try:
            posted_at = datetime.fromisoformat(str(row["posted_at"]).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    return Job(
        ats_type=str(row["ats_type"]).strip(),
        ats_id=str(row["ats_id"]).strip(),
        url=row["url"],
        apply_url=row["apply_url"] if row.get("apply_url") else None,
        title=str(row["title"]).strip(),
        company_slug=str(row["company"]).strip(),
        location=str(row["location"]).strip() if row.get("location") else None,
        is_remote=cast(IsRemote, evaluate_location_relevance(str(row["location"]).strip())),
        employment_type=emp_type,
        description=str(row["description"]) if row.get("description") else "",
        salary_min=float(row["salary_min"]) if row.get("salary_min") is not None else None,
        salary_max=float(row["salary_max"]) if row.get("salary_max") is not None else None,
        salary_currency=str(row["salary_currency"]).strip() if row.get("salary_currency") else None,
        application_questions=None,
        posted_at=posted_at
    )

def extract_job_tuple(job: Job) -> tuple[Any, ...]:
    return (
        job.ats_type,
        job.ats_id,
        str(job.url),
        str(job.apply_url) if job.apply_url else None,
        job.title,
        job.company_slug,
        job.location,
        job.employment_type,
        job.description,
        job.salary_min,
        job.salary_max,
        job.salary_currency,
        None,
        job.posted_at.isoformat() if job.posted_at else None,
        job.fetched_at.isoformat(),
        job.is_remote
    )
