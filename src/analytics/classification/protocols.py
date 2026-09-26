from typing import Protocol

from src.shared.models import JobForAnalytics


class Classifier[T](Protocol):
    async def classify(self, job: JobForAnalytics) -> T: ...


class SLM(Protocol):
    def load_model(self) -> None: ...
    def generate[O](self, inp: JobForAnalytics, schema: type[O]) -> O: ...
