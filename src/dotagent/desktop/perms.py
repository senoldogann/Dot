"""Privacy permissions the desktop needs, checked without ever showing a prompt, plus the Settings panes that grant them."""

import subprocess
import sys
from pathlib import Path
from typing import Literal

import ApplicationServices as AS
import Quartz

from dotagent.core.errors import DesktopError, DesktopPermissionError
from dotagent.desktop.types import Permissions

type Pane = Literal["accessibility", "screen_recording", "input_monitoring", "automation", "full_disk_access"]

_SETTINGS: str = "x-apple.systempreferences:com.apple.preference.security?"
_PANES: dict[Pane, str] = {
    "accessibility": "Privacy_Accessibility",
    "screen_recording": "Privacy_ScreenCapture",
    "input_monitoring": "Privacy_ListenEvent",
    "automation": "Privacy_Automation",
    "full_disk_access": "Privacy_AllFiles",
}
_PANE_TITLES: dict[Pane, str] = {
    "accessibility": "Accessibility",
    "screen_recording": "Screen & System Audio Recording",
    "input_monitoring": "Input Monitoring",
    "automation": "Automation",
    "full_disk_access": "Full Disk Access",
}
_OPEN_TIMEOUT_S: float = 10.0


def check_permissions() -> Permissions:
    """The permissions granted to the responsible process (the app that launched Dot); never prompts."""
    return Permissions(
        accessibility=bool(AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: False})),
        screen_recording=bool(Quartz.CGPreflightScreenCaptureAccess()),
        post_event=bool(Quartz.CGPreflightPostEventAccess()),
    )


def program_of_this_process() -> Path:
    """The program macOS sees running this process. A launchd job has no terminal, so its permissions are granted to
    this program (the real file behind the virtual environment's link), not to an app."""
    return Path(sys.executable).resolve()


def request_permissions() -> Permissions:
    """Ask macOS to show its permission prompts for what is missing (nothing appears for what is granted)."""
    AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: True})
    Quartz.CGRequestScreenCaptureAccess()
    Quartz.CGRequestPostEventAccess()
    return check_permissions()


def settings_url(pane: Pane) -> str:
    return _SETTINGS + _PANES[pane]


def open_settings(pane: Pane) -> None:
    """Show a pane of System Settings > Privacy & Security."""
    url: str = settings_url(pane)
    outcome: subprocess.CompletedProcess[str] = subprocess.run(
        ["open", url], capture_output=True, text=True, timeout=_OPEN_TIMEOUT_S, check=False
    )
    if outcome.returncode != 0:
        raise DesktopError(
            "cannot open System Settings", {"url": url, "code": outcome.returncode, "stderr": outcome.stderr.strip()}
        )


def missing_panes(permissions: Permissions) -> tuple[Pane, ...]:
    panes: list[Pane] = []
    if not (permissions.accessibility and permissions.post_event):
        panes.append("accessibility")
    if not permissions.screen_recording:
        panes.append("screen_recording")
    return tuple(panes)


def grant_hint(pane: Pane) -> str:
    return (
        f"Open System Settings > Privacy & Security > {_PANE_TITLES[pane]} and switch on the app that runs Dot "
        "(Terminal, iTerm, Claude, or the Dot service), then start it again. `dotx desktop setup` opens the pane; "
        "for the service use `dotx service permissions`."
    )


def require_accessibility(permissions: Permissions) -> None:
    if not (permissions.accessibility and permissions.post_event):
        raise DesktopPermissionError(
            "macOS has not granted Accessibility to the app that runs Dot, so it can neither read nor control "
            "other applications. " + grant_hint("accessibility"),
            {"accessibility": permissions.accessibility, "post_event": permissions.post_event},
        )


def require_screen_recording(permissions: Permissions) -> None:
    if not permissions.screen_recording:
        raise DesktopPermissionError(
            "macOS has not granted Screen Recording to the app that runs Dot, so it cannot take screenshots. "
            + grant_hint("screen_recording"),
            {"screen_recording": permissions.screen_recording},
        )
