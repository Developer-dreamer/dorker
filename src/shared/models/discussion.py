from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

import uuid6
from pydantic import BaseModel, Field


class DiscussionMessage(BaseModel):
    id: UUID = Field(default_factory=uuid6.uuid7)
    match_id: UUID
    user_id: int
    chat_id: int
    role: Literal["user", "assistant"]
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
