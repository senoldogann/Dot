"""Privacy permissions the desktop needs, checked without ever showing a prompt, plus the Settings panes that grant
them."""

import ctypes
import os
import subprocess
from functools import cache
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
_OPEN: str = "/usr/bin/open"
_OPEN_TIMEOUT_S: float = 10.0
_LIBSYSTEM: str = "/usr/lib/libSystem.B.dylib"
# PROC_PIDPATHINFO_MAXSIZE, the room proc_pidpath needs.
_PID_PATH_BYTES: int = 4096


def _accessibility_granted() -> bool:
    return bool(AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: False}))


def check_permissions() -> Permissions:
    """The permissions granted to the responsible process (the app that launched Dot); never prompts."""
    return Permissions(
        accessibility=_accessibility_granted(),
        screen_recording=bool(Quartz.CGPreflightScreenCaptureAccess()),
        post_event=bool(Quartz.CGPreflightPostEventAccess()),
    )


@cache
def _libsystem() -> ctypes.CDLL:
    library: ctypes.CDLL = ctypes.CDLL(_LIBSYSTEM, use_errno=True)
    library.responsibility_get_pid_responsible_for_pid.argtypes = (ctypes.c_int,)
    library.responsibility_get_pid_responsible_for_pid.restype = ctypes.c_int
    library.proc_pidpath.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32)
    library.proc_pidpath.restype = ctypes.c_int
    return library


def app_of(program: Path) -> Path:
    """The app whose program ``program`` is (``X.app/Contents/MacOS/x`` is ``X.app``), else ``program`` itself."""
    contents: Path = program.parent.parent
    if program.parent.name == "MacOS" and contents.name == "Contents" and contents.parent.suffix == ".app":
        return contents.parent
    return program


def responsible_program() -> Path:
    """The program macOS holds responsible for this process: what its privacy questions and the lists of System Settings
    name, and what a permission is granted to. It is the app this process was started from (Terminal, iTerm, Claude;
    Dot.app for the service, item 288), as its bundle, or a program started on its own. macOS tells it through
    ``responsibility_get_pid_responsible_for_pid``, which it uses for its own questions and has no public name."""
    library: ctypes.CDLL = _libsystem()
    pid: int = library.responsibility_get_pid_responsible_for_pid(os.getpid())
    if pid <= 0:
        raise DesktopError("macOS did not say which program it holds responsible for Dot", {"pid": os.getpid()})
    buffer: ctypes.Array[ctypes.c_char] = ctypes.create_string_buffer(_PID_PATH_BYTES)
    length: int = library.proc_pidpath(pid, buffer, _PID_PATH_BYTES)
    if length <= 0:
        raise DesktopError(
            "macOS did not give the path of the program it holds responsible for Dot",
            {"pid": pid, "errno": ctypes.get_errno()},
        )
    return app_of(Path(buffer.value.decode("utf-8")))


def program_name(program: Path) -> str:
    """The name System Settings shows for ``program``: an app's without ``.app``, else the program's file name."""
    return program.stem if program.suffix == ".app" else program.name


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
        [_OPEN, url], capture_output=True, text=True, timeout=_OPEN_TIMEOUT_S, check=False
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
    """What to switch on, and where, naming the program macOS asks for (``responsible_program``)."""
    return (
        f"Open System Settings > Privacy & Security > {_PANE_TITLES[pane]} and switch on "
        f"{program_name(responsible_program())}, then start it again. Settings › System › Permissions in the web UI "
        "asks macOS, opens the pane and starts the service again; `dotx desktop setup` opens the pane in a terminal, "
        "`dotx service permissions` for the service."
    )


def require_accessibility() -> None:
    """Raise unless Accessibility and input events are granted. Only these two are read (item 343): the check of
    Screen Recording takes as long again, 6 ms, and reading or controlling an application needs no screenshot."""
    accessibility: bool = _accessibility_granted()
    post_event: bool = bool(Quartz.CGPreflightPostEventAccess())
    if not (accessibility and post_event):
        raise DesktopPermissionError(
            "macOS has not granted Accessibility to the app that runs Dot, so it can neither read nor control "
            "other applications. " + grant_hint("accessibility"),
            {"accessibility": accessibility, "post_event": post_event},
        )


def require_screen_recording() -> None:
    """Raise unless Screen Recording is granted; only that is read."""
    screen_recording: bool = bool(Quartz.CGPreflightScreenCaptureAccess())
    if not screen_recording:
        raise DesktopPermissionError(
            "macOS has not granted Screen Recording to the app that runs Dot, so it cannot take screenshots. "
            + grant_hint("screen_recording"),
            {"screen_recording": screen_recording},
        )
