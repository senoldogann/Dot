"""The first code that runs inside the sandbox of a ``code_run`` (item 1500).

Standard library only: it runs as ``python -I -S -u -X utf8 launcher.py`` under a ``(deny default)`` profile that reads
nothing of Dot's, and the daemon copies it into the run's folder before every start. It sets ``sys.path`` itself (the
interpreter's own folders, then the run's ``tools`` folder where ``dot_tools`` is), lowers the limits the kernel holds
the process to, attaches the bridge, and runs the code the model wrote, as ``__main__``. Whatever the code prints goes
to the captured output; the bridge is the other two descriptors, which only ``dot_tools`` writes.

The arguments: the request and the reply descriptors of the bridge, the scratch folder, the tools folder, the file the
code is in, and the limits as JSON (``cpu_s``, ``fsize_bytes``, ``nofile``).
"""

import importlib
import io
import json
import linecache
import os
import resource
import sys
import threading
import time
import traceback
from types import TracebackType

# The name the code's own source is known by in a traceback: the lines of it are shown, as for a file.
_NAME: str = "<code>"
# A code that never reached its end: the exit status the daemon reads as an error with a traceback.
_FAILED: int = 1
_BROKEN_BRIDGE: int = 70
_ORPHANED: int = 71
_NO_LIMITS: int = 72


def _lower(which: int, soft: int, hard: int | None = None) -> None:
    """Lower a limit of the process, never raise one. A limit the system will not take is an error, not a limit left
    off: the code is not run with fewer guards than it is promised."""
    _, current_hard = resource.getrlimit(which)
    wanted_hard: int = soft if hard is None else hard
    if current_hard != resource.RLIM_INFINITY:
        wanted_hard = min(wanted_hard, current_hard)
    wanted_soft: int = min(soft, wanted_hard)
    resource.setrlimit(which, (wanted_soft, wanted_hard))


def _set_limits(limits: dict[str, int]) -> None:
    cpu: int = limits["cpu_s"]
    _lower(resource.RLIMIT_CPU, cpu, cpu + 2)
    _lower(resource.RLIMIT_FSIZE, limits["fsize_bytes"])
    _lower(resource.RLIMIT_NOFILE, limits["nofile"])
    # One process of the user's own at most: a fork asks for a second, and the profile refuses it as well.
    _lower(resource.RLIMIT_NPROC, 1)
    _lower(resource.RLIMIT_CORE, 0)


def _leave_with_the_daemon() -> None:
    """End this process when the daemon that started it is gone (it is then the child of launchd): a code that sleeps
    would else be left running with nothing to answer to."""
    daemon: int = os.getppid()

    def watch() -> None:
        while True:
            time.sleep(1.0)
            if os.getppid() != daemon:
                os._exit(_ORPHANED)

    threading.Thread(target=watch, daemon=True).start()


def _show(kind: type[BaseException], error: BaseException, trace: TracebackType | None) -> None:
    """The traceback of the code, from its own first frame: the launcher's frames are not the code's."""
    traceback.print_exception(kind, error, trace)


def main(arguments: list[str]) -> int:
    request_fd, reply_fd = int(arguments[0]), int(arguments[1])
    scratch, tools, code_file = arguments[2], arguments[3], arguments[4]
    limits: dict[str, int] = json.loads(arguments[5])
    os.chdir(scratch)
    try:
        _set_limits(limits)
    except (ValueError, OSError) as error:
        print(f"a limit of the process could not be set ({error}), so the code was not run", file=sys.stderr)
        return _NO_LIMITS
    _leave_with_the_daemon()
    sys.path.append(tools)
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="backslashreplace")
    try:
        importlib.import_module("dot_tools._bridge").attach(request_fd, reply_fd)
    except Exception:  # noqa: BLE001 - nothing of the code ran yet: said as it is, and the run ends
        traceback.print_exc()
        return _BROKEN_BRIDGE
    with open(code_file, encoding="utf-8") as handle:
        source: str = handle.read()
    linecache.cache[_NAME] = (len(source), None, source.splitlines(True), _NAME)
    scope: dict[str, object] = {"__name__": "__main__", "__file__": _NAME}
    try:
        compiled = compile(source, _NAME, "exec")
        exec(compiled, scope)
    except SystemExit as leaving:
        code: object = leaving.code
        if code is None or code == 0:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return _FAILED
    except SyntaxError as error:
        _show(type(error), error, None)
        return _FAILED
    except BaseException as error:  # noqa: BLE001 - whatever the code raised is its own result, shown whole
        trace: TracebackType | None = error.__traceback__
        _show(type(error), error, None if trace is None else trace.tb_next)
        return _FAILED
    return 0


if __name__ == "__main__":
    status: int = main(sys.argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    # Not a return: a thread the code started and left would hold the process open after its end.
    os._exit(status)
