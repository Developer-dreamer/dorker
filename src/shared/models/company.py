from typing import List

from pydantic import BaseModel, Field


class ATSCompany(BaseModel):
    id: int
    name: str
    slug: str
    url: str | None


class ATS(BaseModel):
    name: str
    tier: int

    companies: List[ATSCompany] = Field(default_factory=list)

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ATS):
            return False
        return self.name == other.name
