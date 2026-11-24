"""What the daemon sees of the code's process from outside (item 1500): how much memory it holds and how much it left in
its scratch folder.

macOS has no limit the kernel enforces on a process's memory (``RLIMIT_AS`` and ``RLIMIT_DATA`` are refused by this
kernel; measured on macOS 26), so the memory of the code is read here, many times a second, and the runner kills the
process group when it holds more than ``[code] max_mb``. A single allocation larger than what is read between two looks
can go over before it is seen, which :mod:`dotagent.codemode.runner` says as it is: the limit is a watch, not a wall.
"""

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

_LIBPROC: str = "/usr/lib/libproc.dylib"
# ``proc_pidinfo``'s flavour for the task's counters (``PROC_PIDTASKINFO`` in ``<sys/proc_info.h>``).
_PIDTASKINFO: int = 4


class _TaskInfo(ctypes.Structure):
    """``struct proc_taskinfo``: the counters of a process's task, of which the resident size is the one read."""

    _fields_ = (
        ("virtual_size", ctypes.c_uint64),
        ("resident_size", ctypes.c_uint64),
        ("total_user", ctypes.c_uint64),
        ("total_system", ctypes.c_uint64),
        ("threads_user", ctypes.c_uint64),
        ("threads_system", ctypes.c_uint64),
        ("policy", ctypes.c_int32),
        ("faults", ctypes.c_int32),
        ("pageins", ctypes.c_int32),
        ("cow_faults", ctypes.c_int32),
        ("messages_sent", ctypes.c_int32),
        ("messages_received", ctypes.c_int32),
        ("syscalls_mach", ctypes.c_int32),
        ("syscalls_unix", ctypes.c_int32),
        ("context_switches", ctypes.c_int32),
        ("threads", ctypes.c_int32),
        ("running", ctypes.c_int32),
        ("priority", ctypes.c_int32),
    )


_library: ctypes.CDLL | None = None
_library_tried: bool = False


def _proc() -> ctypes.CDLL | None:
    global _library, _library_tried
    if not _library_tried:
        _library_tried = True
        if sys.platform == "darwin":
            try:
                _library = ctypes.CDLL(_LIBPROC)
            except OSError:
                _library = None
    return _library


def resident_bytes(pid: int) -> int | None:
    """The resident memory of the process ``pid`` in bytes, or ``None`` when it cannot be read (the process is gone, or
    this is not a Mac). A look takes about two microseconds."""
    library: ctypes.CDLL | None = _proc()
    if library is None:
        return None
    info: _TaskInfo = _TaskInfo()
    size: int = library.proc_pidinfo(pid, _PIDTASKINFO, 0, ctypes.byref(info), ctypes.sizeof(info))
    return int(info.resident_size) if size == ctypes.sizeof(info) else None


def memory_is_watchable() -> bool:
    """Whether the memory of another process can be read here: the probe of code mode needs it, since a code mode that
    cannot watch the memory of the code it starts has no limit on it. A process of this one's own is not the question
    (a process may read itself where it may not read another): a short-lived one is started and read. Blocking."""
    child: subprocess.Popen[bytes] = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", "import time; time.sleep(5)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            if resident_bytes(child.pid) is not None:
                return True
            time.sleep(0.01)
        return False
    finally:
        child.kill()
        child.wait()


def scratch_usage(folder: Path, most_entries: int) -> tuple[int, int]:
    """What is in ``folder``: the bytes its files take on the disk and how many entries it holds, counting stops once
    ``most_entries`` is passed (a folder of a million files is not walked). Links are counted as the links they are and
    not followed. Blocking."""
    size: int = 0
    entries: int = 0
    pending: list[str] = [str(folder)]
    while pending:
        try:
            with os.scandir(pending.pop()) as listing:
                for entry in listing:
                    entries += 1
                    if entries > most_entries:
                        return size, entries
                    status: os.stat_result = entry.stat(follow_symlinks=False)
                    size += status.st_blocks * 512
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(entry.path)
        except OSError:
            continue
    return size, entries
