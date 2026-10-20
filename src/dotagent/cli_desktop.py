"""``dotx desktop``: the permissions of computer use, and the applications, windows and screenshots it sees."""

import asyncio
import contextlib
import secrets
import sys
from collections.abc import AsyncIterator
from pathlib import Path

from dotagent.cli_commands import (
    Command,
    DesktopAppsCommand,
    DesktopCheckCommand,
    DesktopScreenshotCommand,
    DesktopSetupCommand,
    DesktopSnapshotCommand,
)
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import InvalidValueError
from dotagent.core.paths import (
    failsafe_path,
)
from dotagent.desktop.perms import (
    Pane,
    check_permissions,
    missing_panes,
    open_settings,
    request_permissions,
    settings_url,
)
from dotagent.desktop.render import render_apps, render_snapshot
from dotagent.desktop.session import DesktopSession, no_browser_chrome
from dotagent.desktop.types import AppView, Permissions, ScreenshotView, SnapshotView
from dotagent.desktop.views import ViewRequest
from dotagent.tools.desktop import snapshot_budget

_NONCE_BYTES: int = 8
_EFFORT_HELP: str = "reasoning level; the levels a model offers are listed by 'dotx models'"
_APPROVALS_HELP: str = (
    "who answers what the policy would ask: supervised (you, and before every change too), ask (you; edits and "
    "routine work run), auto (a review model) or yolo (nobody, but money, credentials, your secrets, messages and "
    "posts in your name wait for you in every mode, whatever your rules say). A change of Dot's own files asks you in "
    "every mode but yolo"
)
_GOAL_MODE_HELP: str = (
    "a checker sends the run back to work until the goal is really reached (default: [agent] goal_mode)"
)
_CLI_THREAD: str = "cli"


@contextlib.asynccontextmanager
async def _desktop_session(config: AppConfig) -> AsyncIterator[DesktopSession]:
    """A desktop session, closed again afterwards."""
    session: DesktopSession = DesktopSession(config.desktop, failsafe_path(), no_browser_chrome)
    try:
        yield session
    finally:
        await session.close()


async def cmd_desktop_check(command: DesktopCheckCommand) -> int:
    permissions: Permissions = check_permissions()
    rows: tuple[tuple[str, bool], ...] = (
        ("accessibility", permissions.accessibility),
        ("input events", permissions.post_event),
        ("screen recording", permissions.screen_recording),
    )
    for name, granted in rows:
        sys.stdout.write(f"{'ok  ' if granted else 'FAIL'}  {name:<18} {'granted' if granted else 'not granted'}\n")
    if missing_panes(permissions):
        sys.stdout.write("Run `dotx desktop setup` to be asked for what is missing.\n")
        return 1
    return 0


async def cmd_desktop_setup(command: DesktopSetupCommand) -> int:
    missing: tuple[Pane, ...] = missing_panes(check_permissions())
    if not missing:
        sys.stdout.write("Accessibility, input events and screen recording are all granted.\n")
        return 0
    await asyncio.to_thread(request_permissions)
    for pane in missing:
        await asyncio.to_thread(open_settings, pane)
        sys.stdout.write(f"opened {settings_url(pane)}\n")
    sys.stdout.write(
        "Switch on the app that runs Dot (Terminal, iTerm, Claude or the Dot service) in each pane, "
        "then start Dot again.\n"
    )
    return 0


async def cmd_desktop_apps(command: DesktopAppsCommand) -> int:
    async with _desktop_session(load_user_config()) as session:
        views: tuple[AppView, ...] = await session.list_apps()
    sys.stdout.write(render_apps(views, secrets.token_hex(_NONCE_BYTES)) + "\n")
    return 0


async def cmd_desktop_snapshot(command: DesktopSnapshotCommand) -> int:
    config: AppConfig = load_user_config()
    # As much of the window as one tool result of the agent's carries, when the model does not lower it.
    budget: int = snapshot_budget(config, config.agent.tool_output_chars)
    request: ViewRequest = ViewRequest(scope=command.scope, ref=None, query=command.query, full=True, max_chars=budget)
    async with _desktop_session(config) as session:
        view: SnapshotView = await session.snapshot(_CLI_THREAD, command.app, request)
    sys.stdout.write(render_snapshot(view, secrets.token_hex(_NONCE_BYTES)) + "\n")
    return 0


def _write_screenshot(path_text: str, image: bytes) -> Path:
    target: Path = Path(path_text).expanduser()
    target.write_bytes(image)
    return target


async def cmd_desktop_screenshot(command: DesktopScreenshotCommand) -> int:
    async with _desktop_session(load_user_config()) as session:
        shot: ScreenshotView = await session.screenshot()
    target: Path = await asyncio.to_thread(_write_screenshot, command.path, shot.image)
    sys.stdout.write(f"{target} ({shot.width}x{shot.height} px, {len(shot.image)} bytes)\n")
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case DesktopCheckCommand():
            return await cmd_desktop_check(command)
        case DesktopSetupCommand():
            return await cmd_desktop_setup(command)
        case DesktopAppsCommand():
            return await cmd_desktop_apps(command)
        case DesktopSnapshotCommand():
            return await cmd_desktop_snapshot(command)
        case DesktopScreenshotCommand():
            return await cmd_desktop_screenshot(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
