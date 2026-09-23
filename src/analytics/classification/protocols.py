from typing import Protocol

from src.shared.models import JobForAnalytics, MatchedJob


class Classifier(Protocol):
    async def classify(self, job: JobForAnalytics) -> MatchedJob: ...


class SLM(Protocol):
    def load_model(self) -> None: ...
    def generate[O](self, inp: JobForAnalytics, schema: type[O]) -> O: ...
