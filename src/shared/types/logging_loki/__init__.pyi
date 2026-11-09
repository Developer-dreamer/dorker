# Local typing for the python-logging-loki API used by Dorker.
from logging import Handler

from .emitter import LokiEmitter

class LokiHandler(Handler):
    emitter: LokiEmitter

    def __init__(
        self,
        url: str,
        tags: dict[str, str] | None = None,
        auth: tuple[str, str | None] | None = None,
        version: str | None = None,
    ) -> None: ...
