"""The macOS sandbox the code of ``code_run`` runs in (item 1500), and the probe that says whether it can start here.

This is not the sandbox of the run's shell commands (:mod:`dotagent.policy.sandbox`): that one allows everything but
Dot's own files, because a command is a program of the owner's. The code of a ``code_run`` is text a model wrote, which
may have been shaped by a page it read, so its profile is the other way round: ``(deny default)``, and only what Python
needs to start is allowed back.

- the interpreter, which is run by the path it really has, and the folders of its own installation, to read; no other
  file of the Mac, not the owner's, not Dot's home, not ``~/Library``, not the keychain, and the clipboard and every
  Mach service are closed with them;
- the run's ``tools`` folder (``dot_tools``, the launcher), to read, and its ``scratch`` folder, the one place the code
  reads and writes (its working folder, and where it finds a picture a tool gave it);
- no network (``socket`` makes a socket and every connect, bind and send is refused), no ``fork``, no program started,
  no signal to another process, no executable page of its own: what it could do beyond that it asks of the daemon, over
  the bridge, and the run's gate decides.

The kernel holds the code to this whatever path, link, letter case or library it uses, the profile matches the path a
file really has. What it does not hold: memory (macOS has no limit the kernel enforces for it, ``RLIMIT_AS`` is refused
by this kernel), which :mod:`dotagent.codemode.runner` watches from outside and answers with a kill.

``code_run`` fails closed: the only words that start the code are :func:`launch_words`, which start the sandbox first.
A Mac where the probe cannot start Python in it runs nothing (:func:`code_mode_ready`), and the reason is told.
"""

import asyncio
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from dotagent.codemode.watch import memory_is_watchable
from dotagent.core.types import FrozenModel
from dotagent.policy.sandbox import SANDBOX_EXEC

# The profile's own version of the interpreter's folders is read whole: its prefix must be deep enough not to be a
# broad folder of the Mac (``/``, ``/usr``, ``/opt/homebrew``, a home folder): ``/Users/<name>/.local/share/uv/python/
# cpython-...`` and Homebrew's and python.org's framework folders are deeper.
_MIN_PREFIX_PARTS: int = 5
# How long a probe that failed stands before it is made again: a Mac that could not start it a minute ago may now.
_FAILED_STANDS_S: float = 300.0
_PROBE_TIMEOUT_S: float = 20.0
# The system's own folders Python's loader reads, and the libraries it maps, whatever the interpreter.
_SYSTEM_READS: tuple[str, ...] = ("/usr/lib", "/System/Library", "/usr/share/locale")
# The names of a path's parents a process looks at while it resolves one, and the zone files that make a local time.
_PARENT_NAMES: tuple[str, ...] = ("/var", "/etc", "/tmp", "/System/Volumes/Data", "/System/Cryptexes/OS")
_TIME_ZONE: tuple[str, ...] = ("/private/var/db/timezone", "/usr/share/zoneinfo")
_LOCAL_TIME: str = "/private/etc/localtime"
_DEVICES: tuple[str, ...] = ("/dev/null", "/dev/urandom", "/dev/random")
# What the interpreter and the libraries ask of the kernel as they start, and the counts a program sizes itself by. No
# name of the Mac (``kern.hostname``) and no user: ``os.uname()``, ``socket.gethostname()`` and ``pwd`` stay refused.
_SYSCTLS: tuple[str, ...] = (
    "security.mac.lockdown_mode_state",
    "kern.bootargs",
    "kern.osproductversion",
    "kern.osvariant_status",
    "kern.iossupportversion",
    "hw.pagesize_compat",
    "hw.pagesize",
    "hw.ephemeral_storage",
    "hw.ncpu",
    "hw.activecpu",
    "hw.physicalcpu",
    "hw.logicalcpu",
)
_SYSCTL_PREFIXES: tuple[str, ...] = ("hw.optional.",)


class Interpreter(FrozenModel):
    """The Python the code runs on: the base interpreter of the daemon's own (never a virtual environment's, whose
    packages are Dot's), by the path it really has, and the folder of its installation."""

    executable: Path
    prefix: Path
    version: str


@dataclass(frozen=True)
class Layout:
    """The folders of one run's code, by their real paths: ``root`` is the workspace they are under, ``run`` the run's
    own folder in it, and below that ``tools`` (read only to the code: ``dot_tools`` and the launcher) and ``scratch``
    (the only place it writes)."""

    root: Path
    run: Path
    tools: Path
    scratch: Path


class Probe(FrozenModel):
    """Whether the code can run here: ``ok``, and when not, ``why``. ``python`` and ``macos`` say where it was tried."""

    ok: bool
    why: str
    python: str
    macos: str


def current_interpreter() -> Interpreter:
    """The base interpreter of this process: the executable a virtual environment links to, and the installation's
    folder, both by their real paths."""
    executable: Path = Path(os.path.realpath(getattr(sys, "_base_executable", sys.executable)))
    prefix: Path = Path(os.path.realpath(sys.base_prefix))
    return Interpreter(executable=executable, prefix=prefix, version=platform.python_version())


def layout_for(workspace: Path, run_id: str) -> Layout:
    """Where the code of the run ``run_id`` has its folders: ``<workspace>/code/<run id>``."""
    root: Path = Path(os.path.realpath(workspace))
    run: Path = root / "code" / run_id
    return Layout(root=root, run=run, tools=run / "tools", scratch=run / "scratch")


def _quoted(text: str | Path) -> str:
    """``text`` as a string of the profile's language."""
    escaped: str = str(text).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _names(kind: str, values: tuple[str | Path, ...]) -> str:
    return " ".join(f"({kind} {_quoted(value)})" for value in values)


def prefix_problem(interpreter: Interpreter, layout: Layout, keep_out: tuple[Path, ...] = ()) -> str | None:
    """Why the interpreter's folder must not be read whole, or ``None``: it is too broad a folder of the Mac, or it
    holds the run's folders, the owner's home folder or one of ``keep_out`` (Dot's home, its workspace)."""
    prefix: Path = interpreter.prefix
    if len(prefix.parts) < _MIN_PREFIX_PARTS:
        return f"the folder of the Python installation, {prefix}, is too broad to be read whole"
    for inside in (layout.run, Path.home(), *keep_out):
        resolved: Path = Path(os.path.realpath(inside))
        if resolved == prefix or prefix in resolved.parents:
            return f"the folder of the Python installation, {prefix}, holds {resolved}, which the code must not read"
    return None


def sandbox_profile(interpreter: Interpreter, layout: Layout) -> str:
    """The profile of the code: everything refused but what is named here. The paths are real paths (the profile matches
    the path a file has, not the link a process names it by); see the module's words for what each part is for."""
    rules: list[str] = [
        "(version 1)",
        "(deny default)",
        f"(allow process-exec (literal {_quoted(interpreter.executable)}))",
        f"(allow file-read* (subpath {_quoted(interpreter.prefix)}))",
        f"(allow file-map-executable (subpath {_quoted(interpreter.prefix)}) {_names('subpath', _SYSTEM_READS[:2])})",
        f"(allow file-read* {_names('subpath', _SYSTEM_READS)})",
        '(allow file-read-data file-read-metadata (literal "/"))',
        f"(allow file-read-metadata {_names('literal', _PARENT_NAMES)})",
        f"(allow file-read* (literal {_quoted(_LOCAL_TIME)}) {_names('subpath', _TIME_ZONE)})",
        f"(allow file-read* file-write* (literal {_quoted(_DEVICES[0])}))",
        f"(allow file-read* {_names('literal', _DEVICES[1:])})",
        f"(allow sysctl-read {_names('sysctl-name', _SYSCTLS)} {_names('sysctl-name-prefix', _SYSCTL_PREFIXES)})",
        f"(allow file-read* (subpath {_quoted(layout.tools)}))",
        f"(allow file-read* file-write* (subpath {_quoted(layout.scratch)}))",
    ]
    return "\n".join(rules)


def launch_words(interpreter: Interpreter, layout: Layout, script: Path, arguments: tuple[str, ...]) -> tuple[str, ...]:
    """The words that start ``script`` on the interpreter in the sandbox of ``layout``: the sandbox first, then the
    interpreter in isolated mode (``-I``: no environment variable, no user site, no folder of the script on its path),
    without ``site`` (``-S``: nothing but the standard library, ``sys.path`` is set by the launcher), unbuffered, in
    UTF-8. These are the only words that ever start the code, so that no way of starting it leaves the sandbox out."""
    return (
        SANDBOX_EXEC,
        "-p",
        sandbox_profile(interpreter, layout),
        str(interpreter.executable),
        "-I",
        "-S",
        "-u",
        "-X",
        "utf8",
        str(script),
        *arguments,
    )


def probe_script() -> str:
    """The program the probe runs in the sandbox: it tries each thing and prints how it went, one word for each, and the
    daemon reads the words. What the code needs must work, and everything else must be refused by the sandbox itself
    (``PermissionError``): another error says nothing of the sandbox, and counts against it."""
    return """\
import os, socket
done = []
def attempt(name, action):
    try:
        action()
        answer = "worked"
    except PermissionError:
        answer = "denied"
    except OSError as error:
        answer = f"other-{error.errno}"
    done.append(f"{name}={answer}")
def forking():
    if os.fork() == 0:
        os._exit(0)
here = os.getcwd()
tools = os.environ["DOT_PROBE_TOOLS"]
outside = os.environ["DOT_PROBE_OUTSIDE"]
attempt("scratch-write", lambda: open(os.path.join(here, "probe.txt"), "w").write("x"))
attempt("tools-read", lambda: os.listdir(tools))
attempt("tools-write", lambda: open(os.path.join(tools, "planted.txt"), "w").write("x"))
attempt("file-outside", lambda: open("/etc/hosts").read())
attempt("write-outside", lambda: open(outside, "w").write("x"))
attempt("users", lambda: os.listdir("/Users"))
attempt("network", lambda: socket.create_connection(("127.0.0.1", 9), timeout=2))
attempt("fork", forking)
attempt("spawn", lambda: os.posix_spawn("/bin/echo", ["echo"], {}))
print("probe " + " ".join(done), flush=True)
# Last, and said on a line of its own: where an exec of another program is allowed, this process becomes that program
# and the line is never written, which reads as the thing not refused.
done.clear()
attempt("exec", lambda: os.execv("/usr/bin/true", ["true"]))
print("probe " + " ".join(done))
"""


# What the probe must have seen of each thing: the code can use its two folders, and nothing else.
_EXPECTED: dict[str, str] = {
    "exec": "denied",
    "scratch-write": "worked",
    "tools-read": "worked",
    "tools-write": "denied",
    "file-outside": "denied",
    "write-outside": "denied",
    "users": "denied",
    "network": "denied",
    "fork": "denied",
    "spawn": "denied",
}


def _judge(output: str) -> str | None:
    """Why the output of the probe does not show a sandbox that holds, or ``None`` when it does."""
    lines: list[str] = [text.removeprefix("probe ") for text in output.splitlines() if text.startswith("probe ")]
    seen: dict[str, str] = dict(word.split("=", 1) for line in lines for word in line.split() if "=" in word)
    wrong: list[str] = [
        f"{name}: {seen.get(name, 'did not run')}" for name, wanted in _EXPECTED.items() if seen.get(name) != wanted
    ]
    return None if not wrong else "the sandbox does not hold (" + ", ".join(wrong) + ")"


def probe_blocking(interpreter: Interpreter, keep_out: tuple[Path, ...] = ()) -> Probe:
    """Start the interpreter in a sandbox of a throwaway folder and read what it could do. Blocking: a few hundred
    milliseconds."""
    macos: str = platform.mac_ver()[0]
    python: str = str(interpreter.executable)

    def probe(ok: bool, why: str) -> Probe:
        return Probe(ok=ok, why=why, python=python, macos=macos)

    if sys.platform != "darwin":
        return probe(False, "this is not macOS")
    if not os.access(SANDBOX_EXEC, os.X_OK):
        return probe(False, f"{SANDBOX_EXEC} is not there")
    if not memory_is_watchable():
        return probe(False, "the memory of the code cannot be watched on this Mac, so it could not be limited")
    root: Path = Path(os.path.realpath(tempfile.mkdtemp(prefix="dot-code-probe-")))
    try:
        layout: Layout = Layout(root=root, run=root, tools=root / "tools", scratch=root / "scratch")
        layout.tools.mkdir()
        layout.scratch.mkdir()
        script: Path = layout.tools / "probe.py"
        script.write_text(probe_script(), encoding="utf-8")
        broad: str | None = prefix_problem(interpreter, layout, keep_out)
        if broad is not None:
            return probe(False, broad)
        words: tuple[str, ...] = launch_words(interpreter, layout, script, ())
        environment: dict[str, str] = {
            "DOT_PROBE_TOOLS": str(layout.tools),
            "DOT_PROBE_OUTSIDE": str(root / "outside.txt"),
            "LANG": "en_US.UTF-8",
        }
        try:
            done: subprocess.CompletedProcess[str] = subprocess.run(
                words,
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_S,
                cwd=layout.scratch,
                env=environment,
                stdin=subprocess.DEVNULL,
                check=False,
                start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return probe(False, f"the sandbox did not start the interpreter: {type(error).__name__}: {error}")
        if done.returncode != 0:
            tail: str = " ".join(done.stderr.split())[-300:]
            return probe(False, f"the sandbox did not start the interpreter (exit {done.returncode}): {tail}")
        problem: str | None = _judge(done.stdout)
        return probe(problem is None, problem or "the sandbox holds")
    finally:
        shutil.rmtree(root, ignore_errors=True)


_probes: dict[str, tuple[float, Probe]] = {}
_probes_lock: threading.Lock = threading.Lock()


def _known(key: str) -> Probe | None:
    with _probes_lock:
        kept: tuple[float, Probe] | None = _probes.get(key)
    if kept is None:
        return None
    made_at, answer = kept
    if not answer.ok and time.monotonic() - made_at > _FAILED_STANDS_S:
        return None
    return answer


def forget_probes() -> None:
    """Make the next :func:`code_mode_ready` probe again (a test, or a Mac that was mended)."""
    with _probes_lock:
        _probes.clear()


async def code_mode_ready(interpreter: Interpreter | None = None, keep_out: tuple[Path, ...] = ()) -> Probe:
    """Whether the code can run on this Mac, probed once and kept (a failure for a few minutes, a success for as long as
    the daemon runs): the answer ``code_run`` goes by and the row of ``dotx doctor`` shows."""
    python: Interpreter = current_interpreter() if interpreter is None else interpreter
    key: str = str(python.executable)
    known: Probe | None = _known(key)
    if known is not None:
        return known
    answer: Probe = await asyncio.to_thread(probe_blocking, python, keep_out)
    with _probes_lock:
        _probes[key] = (time.monotonic(), answer)
    return answer
