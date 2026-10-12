from pydantic import BaseModel, Field


class JobSummaryOutput(BaseModel):
    summary: str = Field(
        description="2-3 detailed sentences defining the job family and concrete role scope based on requirements and responsibilities."
    )
    pros: str = Field(
        description="2-3 detailed sentences detailing personal development, task complexity, and engineering opportunities."
    )
    cons: str = Field(
        description="2-3 detailed sentences detailing operational risks, negative cues, on-call burdens, or career limitations."
    )
