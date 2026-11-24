"""The process groups Dot starts for a run, written down while they work, so that a daemon that starts again after a
crash stops the ones its dead self left behind (item 1670).

A shell command, a terminal session, a Claude Code program and a code-mode script each start in a session and process
group of their own (``start_new_session``), so that a Stop ends the whole group. When the process that started one dies
without ending it (a crash, a ``kill -9``), the group lives on, adopted by ``launchd``: a ``claude -p`` keeps spending
the owner's plan, a loop keeps writing, and no Stop reaches them. Each group is written down here as it starts, one file
under :func:`~dotagent.core.paths.process_groups_dir` (its pgid, the start time of its leader, the run, what it runs,
and the Dot process that started it with that process's own start time), and forgotten when Dot is done with it.

:func:`stop_orphans` runs when the daemon starts (``RunManager.recover``): a group whose Dot process is gone and whose
run no longer works is ended, SIGTERM and then SIGKILL after a grace, but only while it is still the group that was
written down. A pid is reused once its process is gone, so the leader's start time must match to the microsecond; a
group whose leader has ended is taken only when each of its members started after that leader did (a pgid is not given
out again while any member of its group lives). The start times are read from the kernel (``proc_pidinfo``), not from
``ps``.
"""

import contextlib
import ctypes
import functools
import os
import signal
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from dotagent.core.logs import get_logger
from dotagent.core.paths import process_groups_dir
from dotagent.core.text import truncate_middle
from dotagent.core.types import FrozenModel

log = get_logger(__name__)

type GroupKind = Literal["shell", "terminal", "claude_code", "codemode"]

# How long an orphaned group is given to end on SIGTERM before it gets SIGKILL, and how often it is looked at meanwhile.
TERM_GRACE_S: float = 3.0
_POLL_S: float = 0.1
_WHAT_CHARS: int = 160
_PRIVATE_DIR_MODE: int = 0o700
_PRIVATE_FILE_MODE: int = 0o600
_SUFFIX: str = ".json"
# ``proc_pidinfo`` flavor and ``proc_listpids`` type of macOS's libproc (``sys/proc_info.h``).
_PROC_PIDTBSDINFO: int = 3
_PROC_PGRP_ONLY: int = 2
_MOST_MEMBERS: int = 4096
_MICRO: float = 1_000_000.0


class _BsdInfo(ctypes.Structure):
    """``struct proc_bsdinfo`` of ``sys/proc_info.h``, 136 bytes."""

    _fields_ = (
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    )


@functools.cache
def _libproc() -> ctypes.CDLL:
    """macOS's libproc, loaded on first use (the module is imported on the Linux of the gate's unit tests too)."""
    return ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)


class ProcessSeen(FrozenModel):
    """A process as the kernel has it now: its process group and when it started (epoch seconds, to the
    microsecond)."""

    pgid: int
    started: float


def process_seen(pid: int) -> ProcessSeen | None:
    """The process ``pid`` as the kernel has it, ``None`` when there is none (it ended, or it is not this user's)."""
    info: _BsdInfo = _BsdInfo()
    size: int = _libproc().proc_pidinfo(
        pid, _PROC_PIDTBSDINFO, ctypes.c_uint64(0), ctypes.byref(info), ctypes.sizeof(info)
    )
    if size != ctypes.sizeof(info) or info.pbi_pid != pid:
        return None
    return ProcessSeen(pgid=int(info.pbi_pgid), started=info.pbi_start_tvsec + info.pbi_start_tvusec / _MICRO)


def group_members(pgid: int) -> tuple[int, ...]:
    """The processes of the process group ``pgid`` now."""
    buffer = (ctypes.c_int * _MOST_MEMBERS)()
    size: int = _libproc().proc_listpids(_PROC_PGRP_ONLY, ctypes.c_uint32(pgid), buffer, ctypes.sizeof(buffer))
    return tuple(pid for pid in buffer[: max(0, size) // ctypes.sizeof(ctypes.c_int)] if pid > 0)


class GroupRecord(FrozenModel):
    """A process group Dot started for the run ``run_id``: its pgid and its leader's start time, what it runs (the start
    of its command, for the resumed run's note and the log) and the Dot process that started it, by pid and start
    time, which a daemon that starts looks at to tell whether that process is gone."""

    pgid: int
    started: float
    run_id: str
    kind: GroupKind
    what: str
    dot_pid: int
    dot_started: float
    at: float


def record_group(pid: int, run_id: str, kind: GroupKind, what: str) -> None:
    """Write down the process group that the process ``pid``, which leads it, started for ``run_id``, until
    :func:`forget_group`. Nothing is written when the process has ended already (there is nothing left to stop) or
    leads no group of its own. The file is named for the pgid: a pid is not given out again while a group of that id
    lives, so a file a group that ended left under the same name is stale, and is written over. Off macOS (the gate's
    Linux, never a Dot of the owner's) nothing is written: the start times are read from macOS's kernel."""
    if sys.platform != "darwin":
        return
    leader: ProcessSeen | None = process_seen(pid)
    me: ProcessSeen | None = process_seen(os.getpid())
    if leader is None or leader.pgid != pid or me is None:
        return
    record: GroupRecord = GroupRecord(
        pgid=pid,
        started=leader.started,
        run_id=run_id,
        kind=kind,
        what=truncate_middle(" ".join(what.split()), _WHAT_CHARS),
        dot_pid=os.getpid(),
        dot_started=me.started,
        at=time.time(),
    )
    directory: Path = process_groups_dir()
    directory.mkdir(mode=_PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    making: Path = directory / f"{pid}.making"
    descriptor: int = os.open(making, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _PRIVATE_FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(record.model_dump_json())
    os.replace(making, directory / f"{pid}{_SUFFIX}")


def forget_group(pid: int) -> None:
    """Forget the group the process ``pid`` leads, which :func:`record_group` wrote down: Dot ended it, or is done
    with it. Called once the process is reaped, before its pid can lead a group Dot starts again."""
    (process_groups_dir() / f"{pid}{_SUFFIX}").unlink(missing_ok=True)


def is_orphan(record: GroupRecord, leader: ProcessSeen | None, members: Sequence[ProcessSeen]) -> bool:
    """Whether the group ``record`` names is still that group, given its ``leader`` as the kernel has it now (``None``
    when that pid has no process) and the processes in a group of that pgid now: a living leader of the same group
    that started at the same microsecond, or, with the leader gone, members that each started after it did (a pid
    that came back for another process starts later than the group's leader did, and leads a group of its own)."""
    if leader is not None:
        return leader.pgid == record.pgid and abs(leader.started - record.started) < 1 / _MICRO
    return bool(members) and all(member.started >= record.started for member in members)


def _read(path: Path) -> GroupRecord | None:
    try:
        return GroupRecord.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        log.warning("process_group_record_unreadable", path=str(path), error=str(exc))
        return None


def _dot_alive(record: GroupRecord) -> bool:
    seen: ProcessSeen | None = process_seen(record.dot_pid)
    return seen is not None and abs(seen.started - record.dot_started) < 1 / _MICRO


def _still_there(record: GroupRecord) -> bool:
    members: tuple[ProcessSeen, ...] = tuple(
        seen for pid in group_members(record.pgid) if (seen := process_seen(pid)) is not None
    )
    return is_orphan(record, process_seen(record.pgid), members)


def _signal(pgid: int, number: signal.Signals) -> None:
    # A group that ended meanwhile has nothing left to signal: ESRCH, or EPERM while its members are not reaped yet.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, number)


def stop_orphans(working: frozenset[str], grace_s: float) -> tuple[GroupRecord, ...]:
    """End the process groups that Dot processes that are gone left behind (a crash, a ``kill -9``), and forget their
    files: each that is still the group written down (:func:`is_orphan`), whose Dot process is gone and whose run is
    not one of ``working`` (the runs at work, read after the daemon closed those of the processes that are gone),
    gets SIGTERM, then SIGKILL once ``grace_s`` has passed. A group of a Dot process that lives (a ``dotx run`` in a
    terminal) is left, and so is its file. Blocks for up to ``grace_s``: the daemon runs it in a thread of its own.
    Returns the groups that were ended, each logged as ``orphan_group_stopped``."""
    directory: Path = process_groups_dir()
    if not directory.is_dir():
        return ()
    orphans: list[tuple[Path, GroupRecord]] = []
    for path in sorted(directory.glob(f"*{_SUFFIX}")):
        record: GroupRecord | None = _read(path)
        if record is None:
            path.unlink(missing_ok=True)
            continue
        if _dot_alive(record) or record.run_id in working:
            continue
        if _still_there(record):
            orphans.append((path, record))
        else:
            path.unlink(missing_ok=True)
    for _, record in orphans:
        _signal(record.pgid, signal.SIGTERM)
    deadline: float = time.monotonic() + grace_s
    while time.monotonic() < deadline and any(_still_there(record) for _, record in orphans):
        time.sleep(_POLL_S)
    for path, record in orphans:
        killed: bool = _still_there(record)
        if killed:
            _signal(record.pgid, signal.SIGKILL)
        path.unlink(missing_ok=True)
        log.warning(
            "orphan_group_stopped",
            pgid=record.pgid,
            run_id=record.run_id,
            kind=record.kind,
            started=record.started,
            signal="SIGKILL" if killed else "SIGTERM",
        )
    return tuple(record for _, record in orphans)
