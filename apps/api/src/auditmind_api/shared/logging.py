"""Structured JSON logging.

Every log line is one JSON object, never a bare string. Request-scoped context (``trace_id``,
``engagement_id``, ``run_id``) is bound via ``structlog.contextvars`` in request middleware
(``main.py``) so every log line emitted while handling a request automatically carries it,
without every call site needing to pass those values explicitly.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    """Configure stdlib logging + structlog to emit one JSON object per line to stdout.

    Called exactly once, from the FastAPI lifespan at process startup — never per-request.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso")

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Returns a structlog logger bound to ``name`` (typically ``__name__``)."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
