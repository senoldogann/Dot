"""Structured logging. Messages are constant event names; dynamic values are fields.

A process logs JSON lines to its log file, or, without one, to stderr. The log file is shared by the daemon and the
commands run beside it, and kept under :data:`LOG_LIMIT_BYTES`: the process whose line takes it over the limit moves it
to ``<name>.1`` (dropping the one before) and starts a new one; the others write their next line to the new one. Two
that find it full together take turns on a lock of the file (item 556). A log call never raises into the code that logs:
a line the disk would not take is dropped, and the next line that is written says how many were. The file is written by
a thread of its own (:class:`QueuedLog`), so a disk slow to take a line never holds the code that logs it, the daemon's
event loop among it.
"""

import atexit
import contextlib
import fcntl
import json
import logging
import os
import queue
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO, cast

import structlog
from structlog.typing import FilteringBoundLogger, Processor

LOG_LIMIT_BYTES: int = 10 * 1024 * 1024
# The lines a log holds for its thread to write (:class:`QueuedLog`): about 30 MB of lines, more than a minute of the
# busiest daemon's logging, so only a disk that takes nothing for that long drops a line.
QUEUED_LINES: int = 100_000
# A character UTF-8 cannot encode (a lone surrogate, from a file name of bytes that are no UTF-8) is written as its
# escape: the line keeps it, and its write does not fail.
_UNENCODABLE: str = "backslashreplace"
# How long :meth:`QueuedLog.close` waits at a time to put its last item on a full queue, while the thread still reads.
_CLOSE_WAIT_S: float = 0.5
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
        self._handle: TextIO = path.open("a", encoding="utf-8", errors=_UNENCODABLE)
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

    def count_dropped(self, count: int) -> None:
        """Count ``count`` lines that never reached this file, which the next line it writes says, with those the disk
        would not take."""
        with self._lock:
            self._dropped += count

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
        opened: TextIO = self._path.open("a", encoding="utf-8", errors=_UNENCODABLE)
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


class QueuedLog:
    """The log file of a process as a structlog logger whose lines a thread of its own writes (item 4 of the review of
    2026-11-26): a call puts its line on a queue and returns at once, and the thread writes the lines in the order they
    were logged, through ``file``. A flush of the file took 2-3 s on the daemon's event loop when the disk was slow,
    and every run, the web UI and the phone waited for it.

    :meth:`close`, run when the process exits, writes every line still on the queue before it returns; a line logged
    after it is written by the caller, so none is lost on the way out. A line that finds ``limit_lines`` lines waiting
    is dropped and counted, as a line the disk would not take is, and the next line written says how many were."""

    def __init__(self, file: RotatingLog, limit_lines: int) -> None:
        self._file: RotatingLog = file
        # ``None`` is the last item: the thread ends there.
        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=limit_lines)
        self._state: threading.Lock = threading.Lock()
        self._closed: bool = False
        # The lines dropped on a full queue since the thread last took the count.
        self._overflow: int = 0
        self._writer: threading.Thread = threading.Thread(target=self._drain, name="dot-log-writer", daemon=True)
        self._writer.start()

    def msg(self, message: str) -> None:
        with self._state:
            closed: bool = self._closed
            if not closed:
                try:
                    self._queue.put_nowait(message)
                except queue.Full:
                    self._overflow += 1
        if closed:
            # After the lines queued before it, which the thread has written once it has ended.
            self._writer.join()
            self._file.msg(message)

    log = debug = info = warn = warning = msg
    fatal = failure = err = error = critical = exception = msg

    def close(self) -> None:
        """Write every line on the queue and end the thread; later lines are written by their caller."""
        with self._state:
            if self._closed:
                return
            self._closed = True
        # A thread that has ended reads no more: the last item is not waited to be put on a full queue then.
        while self._writer.is_alive():
            try:
                self._queue.put(None, timeout=_CLOSE_WAIT_S)
            except queue.Full:
                continue
            break
        self._writer.join()

    def _drain(self) -> None:
        """Write the queued lines until the last item. A line whose write fails with an error :class:`RotatingLog`
        does not expect is said on stderr with that error (the daemon's ``daemon.err.log``) and counted among the
        lines lost, and the thread goes on with the next: ended, it would leave every later line unwritten and the
        exit waiting on a full queue (the pre-push review of item 1944)."""
        while (message := self._queue.get()) is not None:
            with self._state:
                overflow: int = self._overflow
                self._overflow = 0
            if overflow:
                self._file.count_dropped(overflow)
            try:
                self._file.msg(message)
            except Exception as error:  # noqa: BLE001 - said on stderr and counted, and the thread must go on
                self._file.count_dropped(1)
                # A stderr that is closed or full too leaves the count, which the next line written says.
                with contextlib.suppress(OSError, ValueError):
                    sys.stderr.write(f"dot: a log line could not be written ({type(error).__name__}: {error})\n")


def _handing(sink: QueuedLog) -> Callable[..., QueuedLog]:
    """A structlog logger factory that hands every logger the one ``sink``."""

    def factory(*_: object) -> QueuedLog:
        return sink

    return factory


def configure_logging(level: str, json_output: bool, log_file: Path | None) -> None:
    """Configure structlog once per process: JSON lines to ``log_file`` (:class:`QueuedLog`), or, without one, to
    stderr, as JSON when ``json_output`` and for a person to read else."""
    renderer: Processor = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if (json_output or log_file is not None)
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    factory: Callable[..., object] = structlog.PrintLoggerFactory(file=sys.stderr)
    if log_file is not None:
        # A process that may not write the file (``dotx`` run by a command of a run, whose sandbox closes Dot's logs,
        # item 676) logs to stderr instead, and says so once, with why: a full disk or a link in the file's place.
        try:
            sink: QueuedLog = QueuedLog(RotatingLog(log_file, LOG_LIMIT_BYTES), QUEUED_LINES)
        except OSError as error:
            sys.stderr.write(f"dot: the log file {log_file} cannot be opened ({error}); logging to stderr\n")
        else:
            # The lines still queued are written before the process ends.
            atexit.register(sink.close)
            factory = _handing(sink)
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
