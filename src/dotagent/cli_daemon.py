"""``dotx daemon``, ``dotx open``, ``dotx url`` and ``dotx service``: run Dot in the background and open its UI."""

import asyncio
import os
import sys
import time
from pathlib import Path

import httpx
from pydantic import ValidationError

from dotagent.cli_commands import (
    Command,
    DaemonCommand,
    OpenCommand,
    ServiceInstallCommand,
    ServicePermissionsCommand,
    ServiceStatusCommand,
    ServiceUninstallCommand,
    UrlCommand,
)
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import ConfigError, InvalidValueError, ServiceError
from dotagent.core.paths import dot_home, log_dir, token_path
from dotagent.desktop.perms import Pane, open_settings, program_of_this_process, settings_url
from dotagent.launchd import (
    LABEL,
    JobState,
    LaunchAgent,
    Removal,
    ServiceStatus,
    build_launch_agent,
    install_job,
    launch_agent_path,
    render_launch_agent,
    service_status,
    uninstall_job,
)
from dotagent.server.daemon import run_daemon
from dotagent.server.guard import read_token
from dotagent.server.links import base_url, health_url, ui_url
from dotagent.server.views import HealthView, PermissionsView

_OPEN: str = "/usr/bin/open"
_PBCOPY: str = "/usr/bin/pbcopy"
_PROBE_TIMEOUT_S: float = 3.0
# The daemon answers once macOS has been asked; the dialogs themselves wait for the user.
_ASK_TIMEOUT_S: float = 15.0
_POLL_S: float = 0.5
# A start can include the servers of [[mcp]], the first of which may have to fetch its package.
_START_WAIT_S: float = 60.0
_BODY_CHARS: int = 300

_NO_DAEMON_FIX: str = "start it with 'dotx daemon' (in the foreground) or 'dotx service install' (at every login)"


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


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


async def reachable_ui_url(config: AppConfig) -> str:
    """The address that opens the UI already signed in, once the daemon is known to answer."""
    host: str = config.server.host
    port: int = config.server.port
    if await probe_daemon(health_url(host, port)) is None:
        raise ServiceError("the Dot daemon does not answer", {"url": base_url(host, port), "fix": _NO_DAEMON_FIX})
    return ui_url(host, port, await asyncio.to_thread(_token_of, token_path()))


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
        f"Dot daemon on {base_url(config.server.host, config.server.port)}; log: {log_dir() / 'dotx.log'}. "
        "Open the UI with 'dotx open' from another terminal; stop with Ctrl-C.\n"
    )
    await run_daemon(config)
    return 0


async def cmd_open(command: OpenCommand) -> int:
    await open_in_browser(await reachable_ui_url(load_user_config()))
    sys.stdout.write("Opened the Dot UI in your default browser.\n")
    return 0


async def cmd_url(command: UrlCommand) -> int:
    sys.stdout.write(f"{await reachable_ui_url(load_user_config())}\n")
    return 0


def _job_line(status: ServiceStatus) -> str:
    job: JobState | None = status.job
    if job is None:
        return "not loaded"
    pid: str = "" if job.pid is None else f", pid {job.pid}"
    runs: str = "" if job.runs is None else f", started {job.runs} time(s)"
    last: str = "" if job.last_exit is None else f", last exit: {job.last_exit}"
    return f"{job.state}{pid}{runs}{last}"


async def cmd_service_status(command: ServiceStatusCommand) -> int:
    config: AppConfig = load_user_config()
    url: str = health_url(config.server.host, config.server.port)
    status: ServiceStatus = await service_status(_plist_path(), os.getuid())
    health: HealthView | None = await probe_daemon(url)
    sys.stdout.write(f"job      {LABEL}\n")
    sys.stdout.write(f"file     {status.plist}{'' if status.installed else '  (not installed)'}\n")
    sys.stdout.write(f"launchd  {_job_line(status)}\n")
    if health is None:
        sys.stdout.write(f"daemon   does not answer at {base_url(config.server.host, config.server.port)}\n")
    else:
        sys.stdout.write(
            f"daemon   answers at {base_url(config.server.host, config.server.port)} (version {health.version})\n"
        )
    sys.stdout.write(f"logs     {log_dir()}\n")
    return 0 if status.job is not None and health is not None else 1


async def cmd_service_install(command: ServiceInstallCommand) -> int:
    config: AppConfig = load_user_config()
    url: str = health_url(config.server.host, config.server.port)
    uid: int = os.getuid()
    plist: Path = _plist_path()
    before: ServiceStatus = await service_status(plist, uid)
    if before.job is None and await probe_daemon(url) is not None:
        raise ServiceError(
            "a Dot daemon that is not the service already answers at the address",
            {"url": url, "fix": "stop it (Ctrl-C in its terminal), then install the service again"},
        )
    agent: LaunchAgent = build_launch_agent(sys.executable, dot_home(), log_dir(), os.environ)
    replaced: bool = await install_job(plist, render_launch_agent(agent), uid)
    sys.stdout.write(f"{'Replaced' if replaced else 'Installed'} the launchd job {LABEL}: {plist}\n")
    health: HealthView | None = await wait_for_daemon(url, _START_WAIT_S)
    if health is None:
        after: ServiceStatus = await service_status(plist, uid)
        raise ServiceError(
            "the launchd job is loaded but the daemon does not answer",
            {
                "url": url,
                "waited_s": _START_WAIT_S,
                "job": None if after.job is None else after.job.model_dump(mode="json"),
                "logs": str(log_dir()),
                "fix": (
                    "read daemon.err.log and dotx.log in the log directory; 'dotx service status' shows the job. "
                    "When Dot lives under Desktop, Documents or Downloads, the first start waits for the answer to "
                    "the dialog in which macOS asks whether Python may use that folder"
                ),
            },
        )
    sys.stdout.write(
        f"The daemon answers at {base_url(config.server.host, config.server.port)} (version {health.version}). "
        "It starts at every login and again after a crash.\n"
        "Open the UI with 'dotx open'. Re-run 'dotx service install' after changing the configuration.\n"
        f"macOS grants Accessibility and Screen Recording to the program that runs Dot, so allow this one in "
        f"System Settings > Privacy & Security, or desktop actions fail:\n  {program_of_this_process()}\n"
        "'dotx service permissions' leads you through them.\n"
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
    """What the person does for one pane, as macOS behaves for a program that is not an app: it shows a dialog for
    Accessibility but none for Screen Recording (its log says the service does not allow prompting)."""
    match pane:
        case "accessibility":
            return (
                "macOS shows a dialog that names the program above: choose 'Open System Settings' and switch the "
                "program on in the list (Touch ID or password). If it is not in the list, press +, then Cmd+Shift+G, "
                "paste with Cmd+V (the path of the program is on your clipboard), press Return and choose Open. "
                "Input events follow this switch. A running service keeps what it had when it started: then run "
                "'dotx service install' to restart it and this command again for the next permission."
            )
        case "screen_recording":
            return (
                "macOS shows no dialog for Screen Recording, so add the program by hand in the pane that just opened: "
                "press + (Touch ID or password), press Cmd+Shift+G, paste with Cmd+V (the path of the program is on "
                "your clipboard), press Return, choose Open, and switch it on. It takes effect only for a program "
                "that starts afterwards: run 'dotx service install' to restart the service, then "
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
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
