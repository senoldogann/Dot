"""The launchd job that keeps the Dot daemon running in the login session of the user.

``build_launch_agent``, ``render_launch_agent``, ``build_restart_job``, ``render_restart_job`` and ``parse_job`` are
pure; the rest talks to ``launchctl`` and the file system. The job is a LaunchAgent of the graphical session, because
the daemon needs the screen, the keyboard and a browser window; it starts at every login and starts again after a
crash, never after a clean stop.

A second job, the restart that waits for the runs to end (item 441), runs once: it is loaded by ``dotx service restart
--when-idle``, does its work and unloads itself. It never keeps itself alive: a job that launchd starts again each time
it exits would restart the daemon over and over.
"""

import asyncio
import contextlib
import os
import plistlib
import re
import stat
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict

from dotagent.core.errors import ServiceError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.dot_app import tools_path

log = get_logger(__name__)

LABEL: str = "dev.dotagent.dot"
# The one-shot job that restarts the daemon once no run works (item 441), and the module of dotagent it runs.
RESTART_LABEL: str = "dev.dotagent.restart-when-idle"
RESTART_MODULE: str = "dotagent.restart_when_idle"
# How often that job looks at the runs, and how long it waits before it gives up without restarting.
RESTART_POLL_S: float = 5.0
RESTART_WAIT_LIMIT_S: float = 2 * 60 * 60
# How long it waits besides for the night's learning under way (item 815): a step of the harness takes up to
# [harness] step_minutes (90 by default), the reflection and the plugin scout's night less.
RESTART_NIGHT_LIMIT_S: float = 3 * 60 * 60
# What launchd gives a job is only /usr/bin:/bin:/usr/sbin:/sbin; shell commands of the agent get the login shell's own.
SERVICE_PATH: str = "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# The folder of the tools the desktop app carries (ripgrep, uv; item 575), put before SERVICE_PATH in a job's PATH when
# ``dotx service install`` is started with it set, or finds it in Dot's home (``dot_app.tools_path``), so that Dot's
# search finds ripgrep with no Homebrew.
TOOLS_ENV: str = "DOT_APP_TOOLS"

_LAUNCHCTL: str = "/bin/launchctl"
# ``bootout`` waits until the job has exited, which may take as long as the job's exit timeout.
_LAUNCHCTL_TIMEOUT_S: float = 60.0
# The code with which ``launchctl print`` reports that the service is not loaded.
_NOT_LOADED_CODE: int = 113
_UNLOAD_WAIT_S: float = 20.0
_UNLOAD_POLL_S: float = 0.25
_PLIST_MODE: int = 0o644
# The most of a property list that is read back to be restored: the lists of Dot's jobs are a couple of kilobytes.
_PLIST_READ_LIMIT: int = 1 << 20
# A job that fails to start is tried again after this many seconds, not in a tight loop.
_THROTTLE_S: int = 30
# The daemon closes its runs, its schedules and its browser when it is asked to stop; launchd kills it after this.
_EXIT_TIMEOUT_S: int = 30
_OUTPUT_CHARS: int = 600

_FIELD: re.Pattern[str] = re.compile(r"^\t([a-z][a-z ]*) = (.*)$", re.MULTILINE)


class KeepAlive(TypedDict):
    SuccessfulExit: bool


class AgentPlist(TypedDict):
    """The keys of the property list, spelled the way ``launchd.plist(5)`` spells them."""

    Label: str
    ProgramArguments: list[str]
    RunAtLoad: bool
    KeepAlive: KeepAlive
    ThrottleInterval: int
    ProcessType: str
    ExitTimeOut: int
    LimitLoadToSessionType: str
    WorkingDirectory: str
    StandardOutPath: str
    StandardErrorPath: str
    EnvironmentVariables: dict[str, str]


class LaunchAgent(FrozenModel):
    """What the job runs and where it keeps its files; ``render_launch_agent`` turns it into the property list.
    ``launcher`` is the program of Dot.app (``dot_app``), which starts ``python`` with the daemon."""

    launcher: str
    python: str
    working_directory: str
    stdout_path: str
    stderr_path: str
    environment: dict[str, str]


class RestartPlist(TypedDict):
    """The keys of the property list of the restart that waits: a job that runs once, with no ``KeepAlive``."""

    Label: str
    ProgramArguments: list[str]
    RunAtLoad: bool
    AbandonProcessGroup: bool
    ProcessType: str
    LimitLoadToSessionType: str
    WorkingDirectory: str
    StandardOutPath: str
    StandardErrorPath: str
    EnvironmentVariables: dict[str, str]


class RestartJob(FrozenModel):
    """What the restart that waits runs and where it keeps its files; ``render_restart_job`` turns it into the property
    list."""

    python: str
    working_directory: str
    stdout_path: str
    stderr_path: str
    environment: dict[str, str]


class LaunchctlResult(FrozenModel):
    arguments: list[str]
    returncode: int
    stdout: str
    stderr: str


class JobState(FrozenModel):
    """What launchd reports about a job that is loaded."""

    state: str
    pid: int | None
    runs: int | None
    last_exit: str | None


class ServiceStatus(FrozenModel):
    plist: str
    installed: bool
    job: JobState | None


class Removal(FrozenModel):
    was_loaded: bool
    file_removed: bool


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def launch_agent_path(user_home: Path) -> Path:
    return user_home / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def domain_target(uid: int) -> str:
    """The launchd domain of the graphical session of the user."""
    return f"gui/{uid}"


def service_target(uid: int) -> str:
    return job_target(uid, LABEL)


def job_target(uid: int, label: str) -> str:
    """The launchd name of the job ``label`` of the graphical session of the user."""
    return f"{domain_target(uid)}/{label}"


def restart_job_path(home: Path) -> Path:
    """Where the property list of the restart that waits is written: in Dot's home ``home``, never in a folder every
    program may write to."""
    return home / f"{RESTART_LABEL}.plist"


def service_path(environ: Mapping[str, str], home: Path | None = None) -> str:
    """The ``PATH`` of a job: :data:`SERVICE_PATH`, with the folder of the desktop app's tools before it. That folder is
    the one ``environ`` names in :data:`TOOLS_ENV`, which only the desktop app's own steps set; else, with Dot's
    ``home`` given, the one the app keeps in it, when it is there: a ``dotx service install`` typed in a terminal on an
    install of the app, as the README tells, writes the job with ripgrep on its path all the same. A folder is one
    full path (a ``:`` would make it two, a newline would break the property list's line); one found in ``home`` that
    is not is left out."""
    tools: str = environ.get(TOOLS_ENV, "")
    if tools == "" and home is not None:
        kept: str = str(tools_path(home))
        if tools_path(home).is_dir() and kept.startswith("/") and ":" not in kept and "\n" not in kept:
            tools = kept
    if tools == "":
        return SERVICE_PATH
    if not tools.startswith("/") or ":" in tools or "\n" in tools:
        raise ServiceError(f"{TOOLS_ENV} is not one full path", {"value": _clip(tools, _OUTPUT_CHARS)})
    return f"{tools}:{SERVICE_PATH}"


def _job_environment(home: Path, logs: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """What a job of Dot runs with: launchd's own environment has no ``/opt/homebrew``; ``DOT_HOME`` and
    ``DOT_LOG_DIR`` are handed on when ``environ`` sets them."""
    environment: dict[str, str] = {"PATH": service_path(environ, home)}
    if environ.get("DOT_HOME", "") != "":
        environment["DOT_HOME"] = str(home)
    if environ.get("DOT_LOG_DIR", "") != "":
        environment["DOT_LOG_DIR"] = str(logs)
    return environment


def build_launch_agent(launcher: str, python: str, home: Path, logs: Path, environ: Mapping[str, str]) -> LaunchAgent:
    """The job that runs the daemon with ``python`` through Dot.app's program ``launcher``. ``DOT_HOME`` and
    ``DOT_LOG_DIR`` are handed on when set here."""
    environment: dict[str, str] = _job_environment(home, logs, environ)
    return LaunchAgent(
        launcher=launcher,
        python=python,
        working_directory=str(home),
        stdout_path=str(logs / "daemon.out.log"),
        stderr_path=str(logs / "daemon.err.log"),
        environment=environment,
    )


def agent_plist(agent: LaunchAgent) -> AgentPlist:
    """The property list of the job.

    ``Interactive`` keeps the browser and the desktop actions the daemon starts at full speed; ``Aqua`` keeps the job
    out of sessions that have no screen. ``-P`` keeps the working directory, Dot's home, where a run's commands may make
    files, off the path Python imports from: the job runs the Dot that is installed. launchd runs Dot.app's program,
    which starts the daemon's Python and waits for it, so that macOS names Dot for the daemon (item 288).
    """
    return AgentPlist(
        Label=LABEL,
        ProgramArguments=[agent.launcher, agent.python, "-P", "-m", "dotagent", "daemon"],
        RunAtLoad=True,
        KeepAlive=KeepAlive(SuccessfulExit=False),
        ThrottleInterval=_THROTTLE_S,
        ProcessType="Interactive",
        ExitTimeOut=_EXIT_TIMEOUT_S,
        LimitLoadToSessionType="Aqua",
        WorkingDirectory=agent.working_directory,
        StandardOutPath=agent.stdout_path,
        StandardErrorPath=agent.stderr_path,
        EnvironmentVariables=agent.environment,
    )


def render_launch_agent(agent: LaunchAgent) -> bytes:
    return plistlib.dumps(agent_plist(agent), fmt=plistlib.FMT_XML, sort_keys=True)


def build_restart_job(python: str, home: Path, logs: Path, environ: Mapping[str, str]) -> RestartJob:
    """The job that runs :data:`RESTART_MODULE` with ``python``, from Dot's home ``home``. ``DOT_HOME`` and
    ``DOT_LOG_DIR`` are handed on when set here."""
    return RestartJob(
        python=python,
        working_directory=str(home),
        stdout_path=str(logs / "restart-when-idle.out.log"),
        stderr_path=str(logs / "restart-when-idle.err.log"),
        environment=_job_environment(home, logs, environ),
    )


def restart_plist(job: RestartJob) -> RestartPlist:
    """The property list of the restart that waits.

    The job runs once, when it is loaded (``RunAtLoad``), and has no ``KeepAlive``: launchd does not start it again
    when it exits. ``AbandonProcessGroup`` keeps launchd from killing the ``launchctl`` calls the program still has
    under way when it ends. ``-P`` keeps Dot's home, the working directory, off the path Python imports from, as it
    does for the daemon's job.
    """
    return RestartPlist(
        Label=RESTART_LABEL,
        ProgramArguments=[job.python, "-P", "-m", RESTART_MODULE],
        RunAtLoad=True,
        AbandonProcessGroup=True,
        ProcessType="Background",
        LimitLoadToSessionType="Aqua",
        WorkingDirectory=job.working_directory,
        StandardOutPath=job.stdout_path,
        StandardErrorPath=job.stderr_path,
        EnvironmentVariables=job.environment,
    )


def render_restart_job(job: RestartJob) -> bytes:
    return plistlib.dumps(restart_plist(job), fmt=plistlib.FMT_XML, sort_keys=True)


def _int_of(text: str | None) -> int | None:
    return int(text) if text is not None and text.isdigit() else None


def parse_job(text: str) -> JobState:
    """The state of a job from the text of ``launchctl print``, whose own properties stand one tab deep."""
    fields: dict[str, str] = {}
    for match in _FIELD.finditer(text):
        fields.setdefault(str(match.group(1)), str(match.group(2)).strip())
    state: str | None = fields.get("state")
    if state is None:
        raise ServiceError(
            "launchctl described the job without saying what state it is in", {"output": _clip(text, _OUTPUT_CHARS)}
        )
    return JobState(
        state=state,
        pid=_int_of(fields.get("pid")),
        runs=_int_of(fields.get("runs")),
        last_exit=fields.get("last exit code", fields.get("last exit reason")),
    )


async def run_launchctl(arguments: list[str]) -> LaunchctlResult:
    command: list[str] = [_LAUNCHCTL, *arguments]
    try:
        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except OSError as exc:
        raise ServiceError("launchctl cannot be started", {"command": [*command], "error": str(exc)}) from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), _LAUNCHCTL_TIMEOUT_S)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ServiceError(
            "launchctl does not answer", {"command": [*command], "timeout_s": _LAUNCHCTL_TIMEOUT_S}
        ) from exc
    return LaunchctlResult(
        arguments=command,
        returncode=await process.wait(),
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


def _failure(result: LaunchctlResult, message: str) -> ServiceError:
    return ServiceError(
        message,
        {
            "command": [*result.arguments],
            "returncode": result.returncode,
            "stdout": _clip(result.stdout, _OUTPUT_CHARS),
            "stderr": _clip(result.stderr, _OUTPUT_CHARS),
        },
    )


async def _checked(arguments: list[str], message: str) -> LaunchctlResult:
    result: LaunchctlResult = await run_launchctl(arguments)
    if result.returncode != 0:
        raise _failure(result, message)
    return result


def service_runs(job: JobState | None, pids: tuple[int, ...]) -> bool:
    """Whether the job launchd describes (``job``, None when it is not loaded) is one of the processes ``pids``: the
    daemon's own, or its parent, since the job runs Dot.app's launcher, which starts the daemon's Python and waits
    for it (item 409)."""
    return job is not None and job.pid is not None and job.pid in pids


async def _inspect(target: str) -> JobState | None:
    result: LaunchctlResult = await run_launchctl(["print", target])
    if result.returncode == _NOT_LOADED_CODE:
        return None
    if result.returncode != 0:
        raise _failure(result, "launchctl cannot describe the job")
    return parse_job(result.stdout)


async def inspect_job(uid: int) -> JobState | None:
    """What launchd keeps about the job of the daemon, or ``None`` when the job is not loaded. Changes nothing."""
    return await _inspect(service_target(uid))


async def inspect_restart_job(uid: int) -> JobState | None:
    """What launchd keeps about the restart that waits for the runs to end, or ``None`` when none waits. Changes
    nothing."""
    return await _inspect(job_target(uid, RESTART_LABEL))


async def _wait_until_unloaded(target: str) -> None:
    deadline: float = time.monotonic() + _UNLOAD_WAIT_S
    while await _inspect(target) is not None:
        if time.monotonic() >= deadline:
            raise ServiceError(
                "launchd still lists the job after it was told to unload it",
                {"target": target, "waited_s": _UNLOAD_WAIT_S},
            )
        await asyncio.sleep(_UNLOAD_POLL_S)


def _write_plist(path: Path, content: bytes) -> None:
    """Replace the property list in one step; launchd refuses a file that others may write to.

    The new file is made by ``mkstemp`` in the same folder, which makes a name that is not there and so writes through
    no link, and ``os.replace`` puts it at the name, replacing what is there without following it. The restart job's
    list is in Dot's home, a place a command of a run may write, and the staging name used to be a fixed one: a link
    left at it was written through, and the file it led to was given the list's mode (item 441, which the upgrade of the
    desktop app (item 575) runs at every update)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as file:
            os.fchmod(file.fileno(), _PLIST_MODE)
            file.write(content)
        os.replace(staging, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(staging)
        raise


def _remove_file(path: Path) -> bool:
    if not path.exists():
        return False
    path.unlink()
    return True


def _read_file(path: Path) -> bytes | None:
    """The property list that is at ``path`` now, for :func:`_put_back` to restore when the new job does not load:
    ``None`` when there is none, and when what is there is not a plain file. A link left at the name would hand back
    what it leads to (a file the sandbox seals, say) as a plain file of Dot's own, and a FIFO would hold the read for
    ever, so the name is opened with ``O_NOFOLLOW`` and ``O_NONBLOCK`` and what was opened is checked, on the
    descriptor, to be a regular file of a size a property list has. What is not is not read, and the new list replaces
    it at the name, as it replaces any file there."""
    try:
        descriptor: int = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("property_list_not_read", path=str(path), error=str(exc))
        return None
    with contextlib.ExitStack() as opened:
        opened.callback(os.close, descriptor)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            log.warning("property_list_not_a_plain_file", path=str(path))
            return None
        os.set_blocking(descriptor, True)
        with os.fdopen(descriptor, "rb", closefd=False) as file:
            content: bytes = file.read(_PLIST_READ_LIMIT + 1)
    if len(content) > _PLIST_READ_LIMIT:
        log.warning("property_list_too_long", path=str(path), limit=_PLIST_READ_LIMIT)
        return None
    return content


async def _install(label: str, plist: Path, content: bytes, uid: int) -> bool:
    """Load the job ``label`` of the property list ``content``, which starts it. A job that is loaded already is
    unloaded first, and the file is written once launchd has let it go. When the new job does not load, the file that
    was there before is put back (or the new one removed, when there was none) and its job, when it was loaded, is
    loaded again, so a failed step leaves the job and its file as they were.

    Returns whether there was a job to replace.
    """
    target: str = job_target(uid, label)
    replaced: bool = await _inspect(target) is not None
    previous: bytes | None = await asyncio.to_thread(_read_file, plist)
    if replaced:
        await _checked(["bootout", target], "launchctl cannot unload the job that is loaded")
        await _wait_until_unloaded(target)
    try:
        await asyncio.to_thread(_write_plist, plist, content)
        await _checked(["bootstrap", domain_target(uid), str(plist)], "launchctl cannot load the job")
    except (ServiceError, OSError) as exc:
        await _put_back(label, plist, previous, replaced, uid, exc)
        raise
    return replaced


async def install_job(plist: Path, content: bytes, uid: int) -> bool:
    """Load the job of the daemon from the property list ``content`` (see :func:`_install`), which starts the daemon.
    Returns whether there was a job to replace."""
    replaced: bool = await _install(LABEL, plist, content, uid)
    log.info("service_installed", plist=str(plist), replaced=replaced)
    return replaced


async def install_restart_job(plist: Path, content: bytes, uid: int) -> bool:
    """Load the restart that waits for the runs to end from the property list ``content`` (see :func:`_install`),
    which starts it. One that waits already is unloaded first, which ends it, so that there is never more than one.
    Returns whether there was a waiting restart to replace."""
    replaced: bool = await _install(RESTART_LABEL, plist, content, uid)
    log.info("restart_when_idle_loaded", plist=str(plist), replaced=replaced)
    return replaced


async def _put_back(
    label: str, plist: Path, previous: bytes | None, reload: bool, uid: int, failure: Exception
) -> None:
    """Put the property list that was there before ``failure`` back (``previous``; ``None`` removes the new one) and,
    with ``reload``, load its job again. Raises :class:`ServiceError` naming both failures when that job does not load
    either."""
    if previous is None:
        await asyncio.to_thread(_remove_file, plist)
        log.warning("service_install_undone", label=label, plist=str(plist), reloaded=False, error=str(failure))
        return
    await asyncio.to_thread(_write_plist, plist, previous)
    if reload:
        result: LaunchctlResult = await run_launchctl(["bootstrap", domain_target(uid), str(plist)])
        if result.returncode != 0:
            back: ServiceError = _failure(result, "launchctl cannot load the job that was there before")
            raise ServiceError(
                "the new job did not load, and the job that was there before did not load again",
                {"new": str(failure), "before": back.context},
            ) from failure
    log.warning("service_install_undone", label=label, plist=str(plist), reloaded=reload, error=str(failure))


async def restart_job(plist: Path, uid: int) -> None:
    """Start the job of the daemon again now from its property list ``plist``: unloaded first (``bootout`` sends the
    daemon SIGTERM, so it closes its browser and servers and records its runs, and waits until it has exited), then
    loaded, which starts it. Raises :class:`ServiceError` when launchd refuses either step."""
    target: str = service_target(uid)
    await _checked(["bootout", target], "launchctl cannot unload the job to restart it")
    await _wait_until_unloaded(target)
    await _checked(["bootstrap", domain_target(uid), str(plist)], "launchctl cannot load the job again")


async def cancel_restart_job(plist: Path, uid: int) -> bool:
    """Unload the restart that waits for the runs to end, when one does, and delete its property list. Returns whether
    one waited."""
    target: str = job_target(uid, RESTART_LABEL)
    waited: bool = await _inspect(target) is not None
    if waited:
        await _checked(["bootout", target], "launchctl cannot unload the restart that waits")
        await _wait_until_unloaded(target)
    await asyncio.to_thread(_remove_file, plist)
    log.info("restart_when_idle_cancelled", plist=str(plist), waited=waited)
    return waited


async def release_restart_job(plist: Path, uid: int) -> None:
    """End the restart that waits, from inside it: delete its property list, then unload its job, which stops the
    process that calls this (launchd ends a job's processes when it unloads it), so nothing after the call runs."""
    file_removed: bool = await asyncio.to_thread(_remove_file, plist)
    log.info("restart_when_idle_unloading", plist=str(plist), file_removed=file_removed)
    await _checked(["bootout", job_target(uid, RESTART_LABEL)], "launchctl cannot unload the restart job")


async def uninstall_job(plist: Path, uid: int) -> Removal:
    """Stop and unload the job, then delete its property list."""
    was_loaded: bool = await inspect_job(uid) is not None
    if was_loaded:
        await _checked(["bootout", service_target(uid)], "launchctl cannot unload the job")
        await _wait_until_unloaded(service_target(uid))
    file_removed: bool = await asyncio.to_thread(_remove_file, plist)
    log.info("service_uninstalled", plist=str(plist), was_loaded=was_loaded, file_removed=file_removed)
    return Removal(was_loaded=was_loaded, file_removed=file_removed)


async def service_status(plist: Path, uid: int) -> ServiceStatus:
    return ServiceStatus(plist=str(plist), installed=await asyncio.to_thread(plist.exists), job=await inspect_job(uid))
