from requests import Session

class LokiEmitter:
    session_class: type[Session]

    def close(self) -> None: ...
