"""Structured logging. Messages are constant event names; dynamic values are fields.

A process logs JSON lines to its log file, or, without one, to stderr. The log file is shared by the daemon and the
commands run beside it, and kept under :data:`LOG_LIMIT_BYTES`: the process whose line takes it over the limit moves it
to ``<name>.1`` (dropping the one before) and starts a new one; the others write their next line to the new one.
"""

import logging
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TextIO, cast

import structlog
from structlog.typing import FilteringBoundLogger, Processor

LOG_LIMIT_BYTES: int = 10 * 1024 * 1024
_OLDER_SUFFIX: str = ".1"

_LEVELS: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


class RotatingLog:
    """The log file of this process as a structlog logger: each line is appended whole and flushed, to the file that is
    at ``path`` now, and a file that reaches ``limit_bytes`` is moved to ``<name>.1``."""

    def __init__(self, path: Path, limit_bytes: int) -> None:
        self._path: Path = path
        self._limit_bytes: int = limit_bytes
        # Threads of the process log at once (the workers of asyncio.to_thread).
        self._lock: threading.Lock = threading.Lock()
        self._handle: TextIO = path.open("a", encoding="utf-8")

    def msg(self, message: str) -> None:
        with self._lock:
            if self._moved():
                self._reopen()
            self._handle.write(message + "\n")
            self._handle.flush()
            if os.fstat(self._handle.fileno()).st_size >= self._limit_bytes:
                self._rotate()

    log = debug = info = warn = warning = msg
    fatal = failure = err = error = critical = exception = msg

    def _moved(self) -> bool:
        """Whether the file this process writes is no longer the one at the path: another process moved it, or the
        owner deleted it."""
        try:
            return os.stat(self._path).st_ino != os.fstat(self._handle.fileno()).st_ino
        except FileNotFoundError:
            return True

    def _reopen(self) -> None:
        self._handle.close()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a", encoding="utf-8")

    def _rotate(self) -> None:
        """Move the full file to ``<name>.1`` and start a new one; when another process moved it first, write to the
        one it started."""
        if not self._moved():
            os.replace(self._path, self._path.with_name(self._path.name + _OLDER_SUFFIX))
        self._reopen()


def _handing(sink: RotatingLog) -> Callable[..., RotatingLog]:
    """A structlog logger factory that hands every logger the one ``sink``."""

    def factory(*_: object) -> RotatingLog:
        return sink

    return factory


def configure_logging(level: str, json_output: bool, log_file: Path | None) -> None:
    """Configure structlog once per process: JSON lines to ``log_file`` (:class:`RotatingLog`), or, without one, to
    stderr, as JSON when ``json_output`` and for a person to read else."""
    renderer: Processor = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if (json_output or log_file is not None)
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    factory: Callable[..., object] = (
        structlog.PrintLoggerFactory(file=sys.stderr)
        if log_file is None
        else _handing(RotatingLog(log_file, LOG_LIMIT_BYTES))
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
        logger_factory=factory,
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> FilteringBoundLogger:
    return cast(FilteringBoundLogger, structlog.get_logger(name))
