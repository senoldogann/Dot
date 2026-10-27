"""The daemon's heap on macOS (item 730). Its malloc keeps the blocks a process frees, dirty, to hand them out again:
a run's step builds and drops megabytes (the conversation sent to the model, the JSON of the request and the reply,
tool output), and what was freed stayed in the daemon's footprint, which Activity Monitor shows as its memory. With
``MallocSpaceEfficient`` set, malloc gives freed pages back to the system. It is read once, when the process starts,
so the daemon starts itself again with it: the same process, the same id and arguments, the variable set. A macOS that
does not know the variable ignores it, and the processes the daemon starts (its MCP servers, the desktop helper)
inherit it.

Measured on the owner's Mac (macOS 26.6, Python 3.13): a process that built and dropped 120 conversations of 400
messages kept 1.1 GB without it and 9 MB with it; forty runs of fourteen steps in one chat ended at 170 MB without it
and 150 MB with it, in less time.
"""

import os
import sys
from collections.abc import Callable, Mapping, Sequence

SPACE_EFFICIENT_ENV: str = "MallocSpaceEfficient"
_ON: str = "1"

type Exec = Callable[[str, list[str]], object]


def heap_restart(environ: Mapping[str, str], executable: str, argv: Sequence[str]) -> list[str] | None:
    """The arguments to start this process again with, ``executable`` first, when ``environ`` lacks the
    space-efficient switch; ``None`` when it has it. ``argv`` is the process's own (``sys.orig_argv``), the
    interpreter's options and the module or script included."""
    if environ.get(SPACE_EFFICIENT_ENV) == _ON or len(argv) == 0:
        return None
    return [executable, *argv[1:]]


def ensure_space_efficient_heap(exec_process: Exec = os.execv) -> None:
    """Start this process again with malloc's space-efficient mode, unless it runs in it already. Nothing comes back
    from a restart: the process goes on from its start."""
    arguments: list[str] | None = heap_restart(os.environ, sys.executable, sys.orig_argv)
    if arguments is None:
        return
    os.environ[SPACE_EFFICIENT_ENV] = _ON
    sys.stdout.flush()
    sys.stderr.flush()
    exec_process(sys.executable, arguments)
