"""Evidence entities and typed mutations shared by HTTP, services, and repositories."""

import re
from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

import uuid6
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


def validate_folder_path(path: str) -> str:
    if (
        not re.fullmatch(r"/[^/]+(?:/[^/]+)*", path)
        or any(part in {".", ".."} for part in path.split("/"))
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
    ):
        raise ValueError(
            "Use an absolute folder path without trailing slash, empty or dot segments."
        )
    return path


FolderPath = Annotated[str, AfterValidator(validate_folder_path)]
EvidenceTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
EvidenceText = Annotated[str, StringConstraints(min_length=1, max_length=200000)]


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceFolder(EvidenceModel):
    path: FolderPath
    created_at: datetime


class EvidenceDraft(EvidenceModel):
    """Validated content without a client-assigned identity or storage metadata."""

    folder_path: FolderPath
    title: EvidenceTitle
    text: EvidenceText
    use_for_matching: bool = False

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Evidence text cannot be blank.")
        return value

    def new_evidence(self) -> "NewEvidence":
        return NewEvidence(
            folder_path=self.folder_path,
            title=self.title,
            text=self.text,
            use_for_matching=self.use_for_matching,
        )


class NewEvidence(EvidenceDraft):
    """Creation command with a stable, server-assigned UUIDv7."""

    id: UUID = Field(default_factory=uuid6.uuid7)


class Evidence(EvidenceDraft):
    id: UUID
    use_for_matching: bool = Field(...)
    created_at: datetime
    updated_at: datetime


class EvidenceUpdate(EvidenceModel):
    """Typed partial edit; omission preserves values and explicit null is rejected."""

    title: EvidenceTitle | None = None
    text: EvidenceText | None = None
    use_for_matching: bool | None = None

    @model_validator(mode="after")
    def supplied_fields(self) -> Self:
        if not self.model_fields_set or any(
            getattr(self, name) is None for name in self.model_fields_set
        ):
            raise ValueError("Supply at least one non-null field.")
        if self.text is not None and not self.text.strip():
            raise ValueError("Evidence text cannot be blank.")
        return self


class MatchEvidenceUpdate(EvidenceModel):
    checked_ids: list[UUID] = Field(max_length=10000)

    @field_validator("checked_ids")
    @classmethod
    def unique_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("Evidence IDs must be unique.")
        return value


class MatchEvidence(EvidenceModel):
    match_id: UUID
    initialized_at: datetime | None
    evidence: list[Evidence]
    checked_ids: list[UUID]
