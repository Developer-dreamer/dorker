from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeVersion:
    model: str
    version: str
    iteration: int
    min_version: str | None = None
