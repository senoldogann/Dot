"""What dotx.log says of a daemon that ended with an error: ``dotx`` writes a ``command_failed`` event for every command
that ends with one (``cli.main``), and the daemon is such a command, so a configuration it refuses, a daemon already
running or an address taken is on record in one place, next to the starts that went well. ``dotx service status`` and
``dotx service install`` read it back, because the reason otherwise reaches only ``daemon.err.log``, which nobody opens
until the daemon does not answer."""

import json
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from dotagent.core.types import FrozenModel

# How much of the end of dotx.log is read: a start that failed is among the last lines of a log that rotates at 10 MB.
_TAIL_BYTES: int = 262_144
_DAEMON_COMMAND: str = "daemon"
_STARTED_EVENT: str = "daemon_started"
_FAILED_EVENT: str = "command_failed"


class DaemonFailure(FrozenModel):
    """The error that ended a ``dotx daemon``, and when (local time, as the log writes it)."""

    at: datetime
    error: str


class _LogLine(BaseModel):
    """The fields of a log line this module reads; the line's other fields are ignored."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    event: str
    timestamp: datetime
    command: str | None = None
    error: str | None = None


def last_daemon_failure(lines: Iterable[str]) -> DaemonFailure | None:
    """The failure of the latest ``dotx daemon`` that ``lines`` (JSON lines of dotx.log, oldest first) record, or
    ``None`` when the latest word of the log about the daemon is a start that went well. A line that is not a log event
    (the cut first line of a tail) is passed over."""
    latest: DaemonFailure | None = None
    for line in lines:
        try:
            entry: _LogLine = _LogLine.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValidationError):
            continue
        if entry.event == _STARTED_EVENT:
            latest = None
        elif entry.event == _FAILED_EVENT and entry.command == _DAEMON_COMMAND and entry.error is not None:
            latest = DaemonFailure(at=entry.timestamp, error=entry.error)
    return latest


def read_tail(path: Path) -> list[str]:
    """The last lines of the file at ``path``, up to :data:`_TAIL_BYTES` bytes; none when there is no file."""
    try:
        with path.open("rb") as handle:
            size: int = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - _TAIL_BYTES))
            data: bytes = handle.read()
    except FileNotFoundError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()


def daemon_failure_in(log_path: Path) -> DaemonFailure | None:
    """:func:`last_daemon_failure` of the end of the log at ``log_path``."""
    return last_daemon_failure(read_tail(log_path))


def failure_text(failure: DaemonFailure) -> str:
    """``configuration failed validation: web.searxng_url is not a setting ... (2026-10-18 04:12)``"""
    return f"{failure.error} ({failure.at:%Y-%m-%d %H:%M})"
