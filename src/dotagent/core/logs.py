"""Structured logging. Messages are constant event names; dynamic values are fields.

A process logs JSON lines to its log file, or, without one, to stderr. The log file is shared by the daemon and the
commands run beside it, and kept under :data:`LOG_LIMIT_BYTES`: the process whose line takes it over the limit moves it
to ``<name>.1`` (dropping the one before) and starts a new one; the others write their next line to the new one. Two
that find it full together take turns on a lock of the file (item 556). A log call never raises into the code that logs:
a line the disk would not take is dropped, and the next line that is written says how many were.
"""

import contextlib
import fcntl
import json
import logging
import os
import sys
import threading
import time
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
        # The lines the disk would not take since the last one it took (a full disk, a folder that is gone).
        self._dropped: int = 0

    def msg(self, message: str) -> None:
        with self._lock:
            try:
                self._write(message)
            except OSError:
                self._dropped += 1
                return
            # A file that cannot be moved now stays over the limit, and the next line tries again.
            with contextlib.suppress(OSError):
                self._rotate_when_full()

    log = debug = info = warn = warning = msg
    fatal = failure = err = error = critical = exception = msg

    def _write(self, message: str) -> None:
        if self._moved():
            self._reopen()
        lost: str = "" if self._dropped == 0 else f"{self._lost()}\n"
        self._handle.write(f"{lost}{message}\n")
        self._handle.flush()
        self._dropped = 0

    def _lost(self) -> str:
        """The line that says how many lines the disk would not take."""
        notice: dict[str, object] = {
            "event": "log_lines_dropped",
            "level": "warning",
            "count": self._dropped,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        return json.dumps(notice, ensure_ascii=False)

    def _rotate_when_full(self) -> None:
        if os.fstat(self._handle.fileno()).st_size >= self._limit_bytes:
            self._rotate()

    def _moved(self) -> bool:
        """Whether the file this process writes is no longer the one at the path: another process moved it, or the
        owner deleted it."""
        try:
            return os.stat(self._path).st_ino != os.fstat(self._handle.fileno()).st_ino
        except FileNotFoundError:
            return True

    def _reopen(self) -> None:
        """Write to the file at the path from now on. The new one is opened first: a folder that cannot be written
        leaves the handle it had, which is open still."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        opened: TextIO = self._path.open("a", encoding="utf-8")
        self._handle.close()
        self._handle = opened

    def _rotate(self) -> None:
        """Move the full file to ``<name>.1`` and start a new one; when another process moved it first, write to the
        one it started. The process that holds the lock of the file moves it: one that finds it held leaves the moving
        to it, and one that takes it after, finding the file moved, does not move the new one over the older file (item
        556)."""
        held: TextIO = self._handle
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            if not self._moved():
                # A file that is gone by now was moved or deleted by another.
                with contextlib.suppress(FileNotFoundError):
                    os.replace(self._path, self._path.with_name(self._path.name + _OLDER_SUFFIX))
            self._reopen()
        finally:
            if not held.closed:
                fcntl.flock(held, fcntl.LOCK_UN)


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
