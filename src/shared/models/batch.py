from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# ==========================================
# Main Root Model
# ==========================================


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

    id: str
    input_file_id: str
    output_file_id: Optional[str] = None

    status: BatchStatus
    items: list[UUID]
