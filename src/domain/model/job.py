from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class Job(BaseModel):
    title: str
    company_name: str
    salary_range: str | None = Field(..., description="Salary range and currency or Empty if not specified.")
    location: str = Field(..., description="Location for the job: Remote, Remote(US), US.")
    description: str
    application_questions: list[str]
    source_url: HttpUrl
    date_published: datetime

def extract_metadata(job: Job) -> str:
    target_properties = {
        "Title",
        "Company name",
        "Salary range",
        "Location",
        "Source url",
        "Date published",
    }

    job_dict = job.model_dump(include=target_properties)

    # 2. Map dictionary values to the precise layout string format
    lines = [f"{key}: {value}" for key, value in job_dict.items()]

    return ",\n".join(lines)

def extract_question(job: Job) -> str:
    if len(job.application_questions) == 0:
        return ""
    
    return ",\n".join(job.application_questions)
