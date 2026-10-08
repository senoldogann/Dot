"""Structured logging. Messages are constant event names; dynamic values are fields."""

import logging
import sys
from pathlib import Path
from typing import TextIO, cast

import structlog
from structlog.typing import FilteringBoundLogger, Processor

_LEVELS: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def configure_logging(level: str, json_output: bool, log_file: Path | None) -> None:
    """Configure structlog once per process; stderr always, plus an optional JSON-lines file."""
    sink: TextIO = log_file.open("a", encoding="utf-8") if log_file is not None else sys.stderr
    renderer: Processor = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if (json_output or log_file is not None)
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_LEVELS[level]),
        logger_factory=structlog.PrintLoggerFactory(file=sink),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> FilteringBoundLogger:
    return cast(FilteringBoundLogger, structlog.get_logger(name))
