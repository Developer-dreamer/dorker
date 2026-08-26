from enum import Enum
from typing import Any, Dict, List, Optional

from openai.types import Batch
from pydantic import BaseModel, ConfigDict, Field


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

    technical_capability_score: float
    strategic_value_score: float
    application_status: ApplicationStatus
    strategic_reason: str
    analytics: Analytics


class Purpose(str, Enum):
    REJECT = "REJECT"
    RANK = "RANK"
    GENERATE = "GENERATE"


class BatchStatus(str, Enum):
    VALIDATING = "validating"
    FAILED = "failed"
    IN_PROGRESS = "in_progress"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class OpenAIBatchRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    # OpenAI Identifiers
    id: str
    endpoint: str
    input_file_id: str
    output_file_id: Optional[str] = None
    error_file_id: Optional[str] = None

    # Application Purpose & Lifecycle
    purpose: Purpose = Field(
        default=Purpose.REJECT,
        description=(
            "REJECT: Filter non-technical jobs; "
            "RANK: Score alignment and suitability tier; "
            "GENERATE: Produce tailored cover letters and answers."
        ),
    )
    status: BatchStatus
    completion_window: str = "24h"

    # Request Counters (Flattened from OpenAI request_counts)
    total_requests: int = 0
    completed_requests: int = 0
    failed_requests: int = 0

    # OpenAI Timestamps (Unix epoch seconds)
    created_at: int
    in_progress_at: Optional[int] = None
    expires_at: Optional[int] = None
    finalizing_at: Optional[int] = None
    completed_at: Optional[int] = None
    failed_at: Optional[int] = None
    cancelled_at: Optional[int] = None

    # Application Ingestion & Tracking
    custom_metadata: Optional[Dict[str, Any]] = None
    last_polled_at: Optional[int] = None
    downloaded_at: Optional[int] = None
    processed_at: Optional[int] = None
    error_message: Optional[str] = None

    @classmethod
    def from_openai_batch(
        cls,
        batch: Batch,
        purpose: Purpose,
        last_polled_at: Optional[int] = None,
        downloaded_at: Optional[int] = None,
        processed_at: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> "OpenAIBatchRecord":
        """Converts an OpenAI SDK Batch object and app state into a database record."""
        total = batch.request_counts.total if batch.request_counts else 0
        completed = (
            batch.request_counts.completed if batch.request_counts else 0
        )
        failed = batch.request_counts.failed if batch.request_counts else 0

        # Extract top-level error message if present in OpenAI batch response
        if not error_message and batch.errors and batch.errors.data:
            error_message = "; ".join(
                f"[{err.code}] {err.message}" for err in batch.errors.data
            )

        return cls(
            id=batch.id,
            endpoint=batch.endpoint,
            input_file_id=batch.input_file_id,
            output_file_id=batch.output_file_id,
            error_file_id=batch.error_file_id,
            purpose=purpose,
            status=BatchStatus(batch.status),
            completion_window=batch.completion_window,
            total_requests=total,
            completed_requests=completed,
            failed_requests=failed,
            created_at=batch.created_at,
            in_progress_at=batch.in_progress_at,
            expires_at=batch.expires_at,
            finalizing_at=batch.finalizing_at,
            completed_at=batch.completed_at,
            failed_at=batch.failed_at,
            cancelled_at=batch.cancelled_at,
            custom_metadata=batch.metadata,
            last_polled_at=last_polled_at,
            downloaded_at=downloaded_at,
            processed_at=processed_at,
            error_message=error_message,
        )
