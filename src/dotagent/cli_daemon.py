"""``dotx daemon``, ``dotx open``, ``dotx url`` and ``dotx service``: run Dot in the background and open its UI."""

import asyncio
import hmac
import os
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from pydantic import ValidationError

from dotagent.cli_commands import (
    Command,
    DaemonCommand,
    OpenCommand,
    ServiceInstallCommand,
    ServicePermissionsCommand,
    ServiceRestartCommand,
    ServiceStatusCommand,
    ServiceUninstallCommand,
    UrlCommand,
)
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import ConfigError, InvalidValueError, ServiceError
from dotagent.core.paths import dot_home, log_dir, token_path
from dotagent.core.runenv import started_by_a_run
from dotagent.daemon_failure import DaemonFailure, daemon_failure_in, failure_text
from dotagent.desktop.perms import Pane, open_settings, settings_url
from dotagent.dot_app import app_path, ensure_app
from dotagent.launchd import (
    LABEL,
    RESTART_LABEL,
    RESTART_POLL_S,
    RESTART_WAIT_LIMIT_S,
    JobState,
    LaunchAgent,
    Removal,
    RestartJob,
    ServiceStatus,
    build_launch_agent,
    build_restart_job,
    cancel_restart_job,
    inspect_job,
    inspect_restart_job,
    install_job,
    install_restart_job,
    launch_agent_path,
    render_launch_agent,
    render_restart_job,
    restart_job,
    restart_job_path,
    service_status,
    uninstall_job,
)
from dotagent.server.daemon import run_daemon
from dotagent.server.guard import PROOF_PATH, daemon_proof, read_token
from dotagent.server.links import base_url, health_url, ui_url
from dotagent.server.views import HealthView, PermissionsView, ProofView

_OPEN: str = "/usr/bin/open"
_PBCOPY: str = "/usr/bin/pbcopy"
_PROBE_TIMEOUT_S: float = 3.0
# The daemon answers once macOS has been asked; the dialogs themselves wait for the user.
_ASK_TIMEOUT_S: float = 15.0
_POLL_S: float = 0.5
# A start can include the servers of [[mcp]], the first of which may have to fetch its package.
_START_WAIT_S: float = 60.0
_BODY_CHARS: int = 300
_NONCE_BYTES: int = 32

_NO_DAEMON_FIX: str = "start it with 'dotx daemon' (in the foreground) or 'dotx service install' (at every login)"


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def _listener_fix(port: int) -> str:
    return f"see what listens on the port with 'lsof -nP -iTCP:{port} -sTCP:LISTEN'"


def _own_log() -> Path:
    return log_dir() / "dotx.log"


def _plist_path() -> Path:
    return launch_agent_path(Path.home())


def _token_of(path: Path) -> str:
    if not path.exists():
        raise ConfigError(
            "there is no API token yet; the daemon creates it when it starts",
            {"path": str(path), "fix": _NO_DAEMON_FIX},
        )
    return read_token(path)


async def probe_daemon(url: str) -> HealthView | None:
    """The answer of the health check at ``url``, or ``None`` when nothing listens there."""
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S, trust_env=False) as client:
        try:
            response: httpx.Response = await client.get(url)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return None
        except httpx.HTTPError as exc:
            raise ServiceError(
                "the health check of the daemon failed on the way", {"url": url, "error": repr(exc)}
            ) from exc
    try:
        return HealthView.model_validate_json(response.content)
    except ValidationError as exc:
        raise ServiceError(
            "something other than the Dot daemon answers at the address",
            {"url": url, "status": response.status_code, "body": _clip(response.text, _BODY_CHARS)},
        ) from exc


async def wait_for_daemon(url: str, seconds: float) -> HealthView | None:
    """The first answer of the health check within ``seconds``, or ``None`` when there was none."""
    deadline: float = time.monotonic() + seconds
    while True:
        health: HealthView | None = await probe_daemon(url)
        if health is not None or time.monotonic() >= deadline:
            return health
        await asyncio.sleep(_POLL_S)


async def prove_daemon(client: httpx.AsyncClient, host: str, port: int, token: str) -> None:
    """Make sure that what answers at the daemon's address holds ``token`` before anything sends it there: it must
    answer a fresh nonce with :func:`daemon_proof`, which a program that took the port cannot compute."""
    nonce: str = secrets.token_hex(_NONCE_BYTES)
    url: str = f"{base_url(host, port)}{PROOF_PATH}"
    try:
        response: httpx.Response = await client.get(url, params={"nonce": nonce})
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise ServiceError(
            "the Dot daemon does not answer", {"url": base_url(host, port), "fix": _NO_DAEMON_FIX}
        ) from exc
    except httpx.HTTPError as exc:
        raise ServiceError(
            "asking the daemon for its proof failed on the way", {"url": url, "error": repr(exc)}
        ) from exc
    try:
        answer: ProofView = ProofView.model_validate_json(response.content)
    except ValidationError as exc:
        raise ServiceError(
            "something other than the Dot daemon answers at the address; it was sent no token",
            {
                "url": url,
                "status": response.status_code,
                "body": _clip(response.text, _BODY_CHARS),
                "fix": f"{_listener_fix(port)}; a Dot daemon of an older version answers so too, and "
                "'dotx service restart' restarts it",
            },
        ) from exc
    if not hmac.compare_digest(answer.proof.encode("utf-8"), daemon_proof(token, nonce).encode("ascii")):
        raise ServiceError(
            "what answers at the address does not hold the token of this installation; it was sent nothing",
            {
                "url": url,
                "fix": f"{_listener_fix(port)}; a Dot daemon started with another DOT_HOME holds another token",
            },
        )


async def reachable_ui_url(config: AppConfig) -> str:
    """The address that opens the UI already signed in, once what answers at the daemon's address has proven that it
    holds the token (:func:`prove_daemon`): a page that another program served there would read the token in it."""
    host: str = config.server.host
    port: int = config.server.port
    token: str = await asyncio.to_thread(_token_of, token_path())
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S, trust_env=False) as client:
        await prove_daemon(client, host, port, token)
    return ui_url(host, port, token)


async def open_in_browser(address: str) -> None:
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        _OPEN, address, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise ServiceError(
            "the default browser could not be opened",
            {
                "command": _OPEN,
                "returncode": process.returncode,
                "stderr": _clip(stderr.decode("utf-8", "replace"), _BODY_CHARS),
            },
        )


async def cmd_daemon(command: DaemonCommand) -> int:
    config: AppConfig = load_user_config()
    sys.stderr.write(
        f"Dot daemon on {base_url(config.server.host, config.server.port)}; log: {_own_log()}. "
        "Open the UI with 'dotx open' from another terminal; stop with Ctrl-C.\n"
    )
    return await run_daemon(config)


async def cmd_open(command: OpenCommand) -> int:
    await open_in_browser(await reachable_ui_url(load_user_config()))
    sys.stdout.write("Opened the Dot UI in your default browser.\n")
    return 0


async def cmd_url(command: UrlCommand) -> int:
    """The signed-in address on this Mac, then one per name of ``[server] reach`` for the phone on your own network."""
    config: AppConfig = load_user_config()
    address: str = await reachable_ui_url(config)
    sys.stdout.write(f"{address}\n")
    for name in config.server.reach:
        sys.stdout.write(f"https://{name}/{address.rsplit('/', 1)[1]}\n")
    return 0


def _job_line(job: JobState | None) -> str:
    if job is None:
        return "not loaded"
    pid: str = "" if job.pid is None else f", pid {job.pid}"
    runs: str = "" if job.runs is None else f", started {job.runs} time(s)"
    last: str = "" if job.last_exit is None else f", last exit: {job.last_exit}"
    return f"{job.state}{pid}{runs}{last}"


def _duration(seconds: float) -> str:
    """``seconds`` in the words the service commands use: hours from one hour on, minutes below."""
    hours: float = seconds / 3600
    if hours >= 1:
        return f"{hours:g} hour{'' if hours == 1 else 's'}"
    return f"{seconds / 60:g} minutes"


def _restart_line(waiting: JobState | None) -> str:
    """What ``dotx service status`` says about the restart that waits for the runs to end: ``waiting`` is its job in
    launchd, ``None`` when none is loaded. A job with no process left (its program failed before it unloaded it) waits
    for nothing, and the next ``--when-idle`` replaces it."""
    if waiting is None:
        return "no restart waits for the runs to end ('dotx service restart --when-idle' asks for one)"
    if waiting.pid is None:
        return (
            f"a restart was asked for but its program has ended without restarting ({_job_line(waiting)}; see "
            "dotx.log); 'dotx service restart --when-idle' asks for a new one"
        )
    return (
        f"the daemon is restarted once no run works ({_job_line(waiting)}); "
        f"it gives up after {_duration(RESTART_WAIT_LIMIT_S)}"
    )


async def cmd_service_status(command: ServiceStatusCommand) -> int:
    config: AppConfig = load_user_config()
    url: str = health_url(config.server.host, config.server.port)
    status: ServiceStatus = await service_status(_plist_path(), os.getuid())
    waiting: JobState | None = await inspect_restart_job(os.getuid())
    health: HealthView | None = await probe_daemon(url)
    sys.stdout.write(f"job      {LABEL}\n")
    sys.stdout.write(f"file     {status.plist}{'' if status.installed else '  (not installed)'}\n")
    sys.stdout.write(f"launchd  {_job_line(status.job)}\n")
    sys.stdout.write(f"restart  {_restart_line(waiting)}\n")
    if health is None:
        sys.stdout.write(f"daemon   does not answer at {base_url(config.server.host, config.server.port)}\n")
        failure: DaemonFailure | None = await asyncio.to_thread(daemon_failure_in, _own_log())
        if failure is not None:
            sys.stdout.write(f"failure  the daemon ended with: {failure_text(failure)}\n")
    else:
        sys.stdout.write(
            f"daemon   answers at {base_url(config.server.host, config.server.port)} (version {health.version})\n"
        )
    sys.stdout.write(f"logs     {log_dir()}\n")
    return 0 if status.job is not None and health is not None else 1


async def _unanswered(url: str, uid: int, began: datetime) -> ServiceError:
    """The error for a job that is loaded while the daemon does not answer at ``url`` within :data:`_START_WAIT_S` of
    ``began``."""
    after: ServiceStatus = await service_status(_plist_path(), uid)
    ended: DaemonFailure | None = await asyncio.to_thread(daemon_failure_in, _own_log())
    # A failure from before ``began`` is not why the daemon of this start does not answer.
    reason: str = (
        "the daemon does not answer"
        if ended is None or ended.at < began
        else f"the daemon ended with an error: {ended.error}"
    )
    return ServiceError(
        f"the launchd job is loaded but {reason}",
        {
            "url": url,
            "waited_s": _START_WAIT_S,
            "job": None if after.job is None else after.job.model_dump(mode="json"),
            "logs": str(log_dir()),
            "fix": (
                "read daemon.err.log and dotx.log in the log directory; 'dotx service status' shows the job. "
                "When Dot lives under Desktop, Documents or Downloads, the first start waits for the answer to "
                "the dialog in which macOS asks whether Dot may use that folder"
            ),
        },
    )


def _refused_in_a_run(what: str) -> None:
    """Refuse ``what``, which restarts the daemon at once, in a command a run's tool started
    (:mod:`dotagent.core.runenv`): the restart would cut that run off, and the end of the run would kill the command
    before it loads the daemon again. Such a run asks for ``dotx service restart --when-idle``."""
    if started_by_a_run(os.environ):
        raise ServiceError(
            f"a run cannot {what}: the daemon would stop under it, cutting the run off",
            {"fix": "'dotx service restart --when-idle' restarts the daemon once the run has ended"},
        )


async def cmd_service_install(command: ServiceInstallCommand) -> int:
    _refused_in_a_run("install the service, which restarts the daemon at once")
    config: AppConfig = load_user_config()
    url: str = health_url(config.server.host, config.server.port)
    uid: int = os.getuid()
    plist: Path = _plist_path()
    began: datetime = datetime.now()
    before: ServiceStatus = await service_status(plist, uid)
    if before.job is None and await probe_daemon(url) is not None:
        raise ServiceError(
            "a Dot daemon that is not the service already answers at the address",
            {"url": url, "fix": "stop it (Ctrl-C in its terminal), then install the service again"},
        )
    launcher: Path = await asyncio.to_thread(ensure_app, dot_home())
    agent: LaunchAgent = build_launch_agent(str(launcher), sys.executable, dot_home(), log_dir(), os.environ)
    replaced: bool = await install_job(plist, render_launch_agent(agent), uid)
    sys.stdout.write(f"{'Replaced' if replaced else 'Installed'} the launchd job {LABEL}: {plist}\n")
    health: HealthView | None = await wait_for_daemon(url, _START_WAIT_S)
    if health is None:
        raise await _unanswered(url, uid, began)
    sys.stdout.write(
        f"The daemon answers at {base_url(config.server.host, config.server.port)} (version {health.version}). "
        "It starts at every login and again after a crash.\n"
        "Open the UI with 'dotx open'. Run 'dotx service restart' after changing the configuration.\n"
        f"macOS asks for Dot, the app {app_path(dot_home())} that runs the daemon, and grants Accessibility and "
        "Screen Recording to it: allow Dot in System Settings > Privacy & Security, or desktop actions fail. "
        "'dotx service permissions' leads you through them.\n"
    )
    return 0


def _not_installed(uid: int) -> ServiceError:
    return ServiceError(
        f"the service is not installed: launchd has no job {LABEL}",
        {"target": f"gui/{uid}/{LABEL}", "fix": "install it with 'dotx service install'"},
    )


async def restart_service(plist: Path, uid: int, url: str) -> HealthView:
    """Restart the daemon now (its job unloaded and loaded again from ``plist``, :func:`restart_job`, so the daemon ends
    as at a stop) and wait until a new process answers the health check at ``url``: the answer of the process that ran
    before does not count.

    Raises :class:`ServiceError` when launchd has no job of the service (it is not installed), when launchctl refuses
    the restart, and when no new process answers within :data:`_START_WAIT_S`.
    """
    before: JobState | None = await inspect_job(uid)
    if before is None:
        raise _not_installed(uid)
    began: datetime = datetime.now()
    await restart_job(plist, uid)
    deadline: float = time.monotonic() + _START_WAIT_S
    while True:
        job: JobState | None = await inspect_job(uid)
        if job is not None and job.pid is not None and job.pid != before.pid:
            health: HealthView | None = await probe_daemon(url)
            if health is not None:
                return health
        if time.monotonic() >= deadline:
            raise await _unanswered(url, uid, began)
        await asyncio.sleep(_POLL_S)


async def cmd_service_restart(command: ServiceRestartCommand) -> int:
    _refused_in_a_run("restart the daemon at once")
    config: AppConfig = load_user_config()
    url: str = health_url(config.server.host, config.server.port)
    health: HealthView = await restart_service(_plist_path(), os.getuid(), url)
    sys.stdout.write(
        f"Restarted the daemon of the launchd job {LABEL}: it answers at "
        f"{base_url(config.server.host, config.server.port)} (version {health.version}). "
        "Runs that were at work were cut off by it; 'dotx service restart --when-idle' waits for them to end.\n"
    )
    return 0


async def cmd_service_restart_when_idle(command: ServiceRestartCommand) -> int:
    """Load the one-shot job that restarts the daemon once no run works (:mod:`dotagent.restart_when_idle`) and return
    at once. A restart that is on its way already is left as it is: replacing it could stop it between unloading the
    daemon's job and loading it again, and it restarts the daemon once no run works anyway."""
    uid: int = os.getuid()
    if await inspect_job(uid) is None:
        raise _not_installed(uid)
    plist: Path = restart_job_path(dot_home())
    waiting: JobState | None = await inspect_restart_job(uid)
    if waiting is not None and waiting.pid is not None:
        sys.stdout.write(
            f"A restart is on its way already (the launchd job {RESTART_LABEL}: {plist}): it restarts the daemon once "
            "no run works. Nothing changed.\n"
        )
        return 0
    # A job left loaded with no process (its program failed before it could unload it) is replaced.
    job: RestartJob = build_restart_job(sys.executable, dot_home(), log_dir(), os.environ)
    await install_restart_job(plist, render_restart_job(job), uid)
    sys.stdout.write(
        f"Loaded a restart that waits (the launchd job {RESTART_LABEL}: {plist}).\n"
        f"The daemon is restarted once no run works, the run that asked included; the job looks every "
        f"{RESTART_POLL_S:g} seconds, gives up after {_duration(RESTART_WAIT_LIMIT_S)} without restarting, and "
        "unloads itself either way. 'dotx service status' says whether one waits.\n"
    )
    return 0


async def copy_to_clipboard(text: str) -> None:
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        _PBCOPY, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate(text.encode("utf-8"))
    if process.returncode != 0:
        raise ServiceError(
            "the clipboard could not be filled",
            {
                "command": _PBCOPY,
                "returncode": process.returncode,
                "stderr": _clip(stderr.decode("utf-8", "replace"), _BODY_CHARS),
            },
        )


def _steps(pane: Pane) -> str:
    """What the person does for one pane. macOS may show a dialog that names the program; when it shows none (it
    showed none for Screen Recording to a program that is not an app), the program is added to the list by hand."""
    match pane:
        case "accessibility":
            return (
                "macOS shows a dialog that names the program above: choose 'Open System Settings' and switch the "
                "program on in the list (Touch ID or password). If it is not in the list, press +, then Cmd+Shift+G, "
                "paste with Cmd+V (the path of the program is on your clipboard), press Return and choose Open. "
                "Input events follow this switch. A running service keeps what it had when it started: then run "
                "'dotx service restart' to restart it and this command again for the next permission."
            )
        case "screen_recording":
            return (
                "Switch the program on in the pane that just opened (Touch ID or password). If it is not in the "
                "list, press +, press Cmd+Shift+G, paste with Cmd+V (the path of the program is on your clipboard), "
                "press Return, choose Open, and switch it on. It takes effect only for a program "
                "that starts afterwards: run 'dotx service restart' to restart the service, then "
                "'dotx service permissions' to see all three granted."
            )
        case "input_monitoring" | "automation" | "full_disk_access":
            raise ServiceError("the daemon named a pane that Dot does not use", {"pane": pane})


def _permission_row(name: str, granted: bool) -> str:
    return f"{'ok  ' if granted else 'FAIL'}  {name:<18} {'granted' if granted else 'not granted'}\n"


async def cmd_service_permissions(command: ServicePermissionsCommand) -> int:
    """Ask the running daemon to make macOS prompt for the desktop permissions it lacks, then open the pane of the
    first one that is missing and say what to do there.

    macOS attributes a prompt to the program that asks, and the service is a launchd job with no terminal: asking
    from this command would prompt for the terminal instead.
    """
    config: AppConfig = load_user_config()
    url: str = f"{base_url(config.server.host, config.server.port)}/api/desktop/permissions"
    token: str = await asyncio.to_thread(_token_of, token_path())
    async with httpx.AsyncClient(timeout=_ASK_TIMEOUT_S, trust_env=False) as client:
        await prove_daemon(client, config.server.host, config.server.port, token)
        try:
            response: httpx.Response = await client.post(url, headers={"Authorization": f"Bearer {token}"})
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ServiceError("the Dot daemon does not answer", {"url": url, "fix": _NO_DAEMON_FIX}) from exc
        except httpx.HTTPError as exc:
            raise ServiceError(
                "asking the daemon for its permissions failed on the way", {"url": url, "error": repr(exc)}
            ) from exc
    if response.status_code != 200:
        raise ServiceError(
            "the daemon did not accept the request for permissions",
            {"url": url, "status": response.status_code, "body": _clip(response.text, _BODY_CHARS)},
        )
    view: PermissionsView = PermissionsView.model_validate_json(response.content)
    sys.stdout.write(f"program  {view.program}\n")
    sys.stdout.write(_permission_row("accessibility", view.accessibility))
    sys.stdout.write(_permission_row("input events", view.input_events))
    sys.stdout.write(_permission_row("screen recording", view.screen_recording))
    if not view.panes:
        sys.stdout.write("The service has everything the desktop needs.\n")
        return 0
    pane: Pane = view.panes[0]
    await copy_to_clipboard(view.program)
    await asyncio.to_thread(open_settings, pane)
    sys.stdout.write(f"opened {settings_url(pane)}\n{_steps(pane)}\n")
    return 1


async def cmd_service_uninstall(command: ServiceUninstallCommand) -> int:
    # A restart that waits would find no job to restart: it goes with the service.
    if await cancel_restart_job(restart_job_path(dot_home()), os.getuid()):
        sys.stdout.write(f"Cancelled the restart that waited for the runs to end ({RESTART_LABEL}).\n")
    removal: Removal = await uninstall_job(_plist_path(), os.getuid())
    if not removal.was_loaded and not removal.file_removed:
        sys.stdout.write(f"The launchd job {LABEL} was not installed; nothing to remove.\n")
        return 0
    sys.stdout.write(
        f"Removed the launchd job {LABEL} (stopped: {'yes' if removal.was_loaded else 'it was not running'}, "
        f"file deleted: {'yes' if removal.file_removed else 'no file'}). The data in {dot_home()} is untouched.\n"
    )
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case DaemonCommand():
            return await cmd_daemon(command)
        case OpenCommand():
            return await cmd_open(command)
        case UrlCommand():
            return await cmd_url(command)
        case ServiceInstallCommand():
            return await cmd_service_install(command)
        case ServiceUninstallCommand():
            return await cmd_service_uninstall(command)
        case ServiceStatusCommand():
            return await cmd_service_status(command)
        case ServicePermissionsCommand():
            return await cmd_service_permissions(command)
        case ServiceRestartCommand(when_idle=True):
            return await cmd_service_restart_when_idle(command)
        case ServiceRestartCommand(when_idle=False):
            return await cmd_service_restart(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
