"""Logging configuration.

P5.4 contract: every log line is a single JSON object on stdout, with
predictable fields that any 12-factor log shipper (Loki, CloudWatch,
Vector, Fluentd) can index. Dev mode uses a human-friendly console
renderer; production uses the JSON renderer.

Required fields on every line (all are strings unless noted):
    timestamp   ISO-8601 UTC
    level       INFO|WARNING|ERROR|DEBUG
    event       dot.case.name  (e.g. "api.generate.start")
    logger      Python logger name

Optional context bound per-request (set by RequestIdMiddleware):
    request_id  str

Override with env vars:
    LOG_FORMAT  json (default) | console
    LOG_LEVEL   DEBUG|INFO|WARNING|ERROR (default: INFO)
"""
import logging
import os
import sys
from typing import Any, Callable, MutableMapping

import structlog

EVENT_KEY = "event"
TIMESTAMP_KEY = "timestamp"
LEVEL_KEY = "level"
LOGGER_KEY = "logger"

Processor = Callable[[Any, str, MutableMapping[str, Any]], Any]


def _configure() -> None:
    # Read env at call time so tests and operational overrides take
    # effect on reconfigure rather than only at process start.
    fmt = os.getenv("LOG_FORMAT", "json").lower()
    level = os.getenv("LOG_LEVEL", "INFO").upper()

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "console":
        renderer: Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    # Hand the event_dict to stdlib unrendered; the formatter below is
    # the single place that produces the final string. This avoids the
    # double-render bug (structlog renders once, formatter renders again).
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Single point of truth for the final line: one formatter renders
    # both structlog-originated events and stdlib log records.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level, logging.INFO))


_configure()


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    if name:
        return structlog.get_logger(name)
    return structlog.get_logger()
