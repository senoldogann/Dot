"""The launchd job that keeps the Dot daemon running in the login session of the user.

``build_launch_agent``, ``render_launch_agent`` and ``parse_job`` are pure; the rest talks to ``launchctl`` and the
file system. The job is a LaunchAgent of the graphical session, because the daemon needs the screen, the keyboard and
a browser window; it starts at every login and starts again after a crash, never after a clean stop.
"""

import asyncio
import os
import plistlib
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict

from dotagent.core.errors import ServiceError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel

log = get_logger(__name__)

LABEL: str = "dev.dotagent.dot"
# What launchd gives a job is only /usr/bin:/bin:/usr/sbin:/sbin; shell commands of the agent get the login shell's own.
SERVICE_PATH: str = "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

_LAUNCHCTL: str = "/bin/launchctl"
# ``bootout`` waits until the job has exited, which may take as long as the job's exit timeout.
_LAUNCHCTL_TIMEOUT_S: float = 60.0
# The code with which ``launchctl print`` reports that the service is not loaded.
_NOT_LOADED_CODE: int = 113
_UNLOAD_WAIT_S: float = 20.0
_UNLOAD_POLL_S: float = 0.25
_PLIST_MODE: int = 0o644
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
    """What the job runs and where it keeps its files; ``render_launch_agent`` turns it into the property list."""

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
    return f"{domain_target(uid)}/{LABEL}"


def build_launch_agent(python: str, home: Path, logs: Path, environ: Mapping[str, str]) -> LaunchAgent:
    """The job that runs the daemon with ``python``. ``DOT_HOME`` and ``DOT_LOG_DIR`` are handed on when set here."""
    environment: dict[str, str] = {"PATH": SERVICE_PATH}
    if environ.get("DOT_HOME", "") != "":
        environment["DOT_HOME"] = str(home)
    if environ.get("DOT_LOG_DIR", "") != "":
        environment["DOT_LOG_DIR"] = str(logs)
    return LaunchAgent(
        python=python,
        working_directory=str(home),
        stdout_path=str(logs / "daemon.out.log"),
        stderr_path=str(logs / "daemon.err.log"),
        environment=environment,
    )


def agent_plist(agent: LaunchAgent) -> AgentPlist:
    """The property list of the job.

    ``Interactive`` keeps the browser and the desktop actions the daemon starts at full speed; ``Aqua`` keeps the job out
    of sessions that have no screen. ``-P`` keeps the working directory, Dot's home, where a run's commands may make
    files, off the path Python imports from: the job runs the Dot that is installed.
    """
    return AgentPlist(
        Label=LABEL,
        ProgramArguments=[agent.python, "-P", "-m", "dotagent", "daemon"],
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


async def inspect_job(uid: int) -> JobState | None:
    """What launchd keeps about the job, or ``None`` when the job is not loaded. Changes nothing."""
    result: LaunchctlResult = await run_launchctl(["print", service_target(uid)])
    if result.returncode == _NOT_LOADED_CODE:
        return None
    if result.returncode != 0:
        raise _failure(result, "launchctl cannot describe the job")
    return parse_job(result.stdout)


async def _wait_until_unloaded(uid: int) -> None:
    deadline: float = time.monotonic() + _UNLOAD_WAIT_S
    while await inspect_job(uid) is not None:
        if time.monotonic() >= deadline:
            raise ServiceError(
                "launchd still lists the job after it was told to unload it",
                {"target": service_target(uid), "waited_s": _UNLOAD_WAIT_S},
            )
        await asyncio.sleep(_UNLOAD_POLL_S)


def _write_plist(path: Path, content: bytes) -> None:
    """Replace the property list in one step; launchd refuses a file that others may write to."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging: Path = path.with_name(f".{path.name}.tmp")
    staging.write_bytes(content)
    os.chmod(staging, _PLIST_MODE)
    os.replace(staging, path)


def _remove_file(path: Path) -> bool:
    if not path.exists():
        return False
    path.unlink()
    return True


async def install_job(plist: Path, content: bytes, uid: int) -> bool:
    """Write the property list and load the job, which starts it. A job that is loaded already is replaced.

    Returns whether there was a job to replace.
    """
    replaced: bool = await inspect_job(uid) is not None
    await asyncio.to_thread(_write_plist, plist, content)
    if replaced:
        await _checked(["bootout", service_target(uid)], "launchctl cannot unload the job that is loaded")
        await _wait_until_unloaded(uid)
    await _checked(["bootstrap", domain_target(uid), str(plist)], "launchctl cannot load the job")
    log.info("service_installed", plist=str(plist), replaced=replaced)
    return replaced


async def uninstall_job(plist: Path, uid: int) -> Removal:
    """Stop and unload the job, then delete its property list."""
    was_loaded: bool = await inspect_job(uid) is not None
    if was_loaded:
        await _checked(["bootout", service_target(uid)], "launchctl cannot unload the job")
        await _wait_until_unloaded(uid)
    file_removed: bool = await asyncio.to_thread(_remove_file, plist)
    log.info("service_uninstalled", plist=str(plist), was_loaded=was_loaded, file_removed=file_removed)
    return Removal(was_loaded=was_loaded, file_removed=file_removed)


async def service_status(plist: Path, uid: int) -> ServiceStatus:
    return ServiceStatus(plist=str(plist), installed=await asyncio.to_thread(plist.exists), job=await inspect_job(uid))
