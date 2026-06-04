import logging
import sys
from typing import Any, Callable, Iterable, Protocol, cast

import structlog


class Logger(Protocol):
    """A structural type interface matching any standard key-value logging backend."""

    def debug(self, event: str, **kwargs: Any) -> Any: ...
    def info(self, event: str, **kwargs: Any) -> Any: ...
    def warning(self, event: str, **kwargs: Any) -> Any: ...
    def error(self, event: str, **kwargs: Any) -> Any: ...
    def exception(self, event: str, **kwargs: Any) -> Any: ...

    def bind(self, **kwargs: Any) -> "Logger": ...


def configure_logger(production_mode: bool = False) -> Logger:
    timestamper = structlog.processors.TimeStamper(fmt="iso")

    # Processors shared by both structlog and standard library logging
    shared_processors: list[structlog.typing.Processor] = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # Structlog-specific pipeline
    structlog_processors = [
        structlog.stdlib.filter_by_level,
    ] + shared_processors + [
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]

    structlog.configure(
        processors=cast(Iterable[Callable[..., Any]], structlog_processors),
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Standard logging formatter configuration
    if production_mode:
        formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                # 3. Use the correct method name: remove_processors_meta
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.dict_tracebacks,
                structlog.processors.JSONRenderer(),
            ]
        )
    else:
        formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                # 3. Use the correct method name: remove_processors_meta
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=True),
            ]
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO if production_mode else logging.DEBUG)

    logger: Logger = structlog.get_logger()

    return logger
