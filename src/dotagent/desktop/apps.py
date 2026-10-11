"""Applications: who is running, who is in front, launching, and bringing one to the front.

NSWorkspace.runningApplications goes stale outside a main run loop, so the list comes from ``lsappinfo`` and the
front application from the accessibility system-wide element; both are live sources.
"""

import re
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import cache
from pathlib import Path

import AppKit
import ApplicationServices as AS

from dotagent.core.errors import DesktopError
from dotagent.core.logs import get_logger
from dotagent.core.text import fold_search
from dotagent.desktop import ax
from dotagent.desktop.types import AppInfo, AppKind

_log = get_logger("dotagent.desktop.apps")

_COMMAND_TIMEOUT_S: float = 30.0
_POLL_S: float = 0.03
_LAUNCH_ATTEMPTS: int = 6
_LAUNCH_RETRY_S: float = 0.5
_AX_FRONT_TIMEOUT_S: float = 1.0
_NO_VALUE: int = -25212
_HEADER: re.Pattern[str] = re.compile(r'^\s*\d+\)\s+"(?P<name>.*)"\s+ASN:', re.MULTILINE)
_BUNDLE_ID: re.Pattern[str] = re.compile(r'^\s*bundleID="(?P<value>[^"]*)"', re.MULTILINE)
_BUNDLE_PATH: re.Pattern[str] = re.compile(r'^\s*bundle path="(?P<value>[^"]*)"', re.MULTILINE)
_PID_AND_TYPE: re.Pattern[str] = re.compile(r'^\s*pid = (?P<pid>\d+)\b[^\n]*?\btype="(?P<type>\w+)"', re.MULTILINE)
_BUNDLE_ID_SHAPE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_KINDS: dict[str, AppKind] = {"Foreground": "regular", "UIElement": "accessory", "BackgroundOnly": "background"}
_LAUNCH_SERVICES_BUSY: str = "-600"  # procNotFound: the application is still shutting down


@cache
def ensure_window_server() -> None:
    """Connect this process to the window server; without it the accessibility system-wide element answers -25204."""
    if not AppKit.NSApplicationLoad():
        raise DesktopError("cannot connect to the window server: NSApplicationLoad returned false", {})


def _stdout(arguments: Sequence[str]) -> str:
    try:
        outcome: subprocess.CompletedProcess[str] = subprocess.run(
            arguments, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise DesktopError(
            f"{arguments[0]} did not finish within {_COMMAND_TIMEOUT_S:g} s", {"command": " ".join(arguments)}
        ) from error
    if outcome.returncode != 0:
        raise DesktopError(
            f"{arguments[0]} failed",
            {"command": " ".join(arguments), "code": outcome.returncode, "stderr": outcome.stderr.strip()},
        )
    return outcome.stdout


def parse_lsappinfo(text: str) -> tuple[AppInfo, ...]:
    """The applications in the output of ``lsappinfo list``; one that has no pid yet is still launching and is left out."""
    headers: list[re.Match[str]] = list(_HEADER.finditer(text))
    apps: list[AppInfo] = []
    for index, header in enumerate(headers):
        end: int = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        block: str = text[header.end() : end]
        process: re.Match[str] | None = _PID_AND_TYPE.search(block)
        if process is None:
            continue
        kind: AppKind | None = _KINDS.get(process["type"])
        if kind is None:
            raise DesktopError(
                "lsappinfo reported an application type this code does not know",
                {"type": process["type"], "application": header["name"]},
            )
        bundle_id: re.Match[str] | None = _BUNDLE_ID.search(block)
        bundle_path: re.Match[str] | None = _BUNDLE_PATH.search(block)
        apps.append(
            AppInfo(
                pid=int(process["pid"]),
                name=header["name"],
                bundle_id=bundle_id["value"] if bundle_id is not None else None,
                bundle_path=bundle_path["value"] if bundle_path is not None else "",
                kind=kind,
                frontmost=False,
            )
        )
    return tuple(apps)


def frontmost_pid() -> int | None:
    """The application that has the keyboard focus right now, or ``None`` while focus is changing hands."""
    ensure_window_server()
    system = AS.AXUIElementCreateSystemWide()
    code, app = AS.AXUIElementCopyAttributeValue(system, "AXFocusedApplication", None)
    if app is None:  # note: macOS answers -25212 together with a valid element, so only a missing element counts
        if int(code) in (0, _NO_VALUE):
            return None
        raise ax.failure(int(code), "cannot find the focused application", 0, "AXFocusedApplication")
    pid_code, pid = AS.AXUIElementGetPid(app, None)
    if int(pid_code) != 0:
        raise ax.failure(int(pid_code), "cannot read the pid of the focused application", 0, "AXUIElementGetPid")
    return int(pid)


def list_apps() -> tuple[AppInfo, ...]:
    """Every registered application (about 40 ms); the one with the keyboard focus is marked ``frontmost``."""
    front: int | None = frontmost_pid()
    return tuple(replace(app, frontmost=app.pid == front) for app in parse_lsappinfo(_stdout(["lsappinfo", "list"])))


def _names_of(app: AppInfo) -> tuple[str, ...]:
    stem: str = Path(app.bundle_path).stem if app.bundle_path else ""
    return tuple(fold_search(name) for name in (app.name, stem, app.bundle_id or "") if name)


def find_app(query: str, apps: Sequence[AppInfo]) -> AppInfo | None:
    """The one application ``query`` names (pid, bundle id, name or file name), or ``None``; several candidates raise."""
    wanted: str = query.strip()
    if wanted.isdigit():
        return next((app for app in apps if app.pid == int(wanted)), None)
    folded: str = fold_search(wanted)
    tiers: tuple[Callable[[str], bool], ...] = (
        lambda name: name == folded,
        lambda name: name.startswith(folded),
        lambda name: folded in name,
    )
    for matches_name in tiers:
        matches: list[AppInfo] = [app for app in apps if any(matches_name(name) for name in _names_of(app))]
        regular: list[AppInfo] = [app for app in matches if app.kind == "regular"]
        if len(matches) == 1:
            return matches[0]
        if len(regular) == 1:
            return regular[0]
        if matches:
            raise DesktopError(
                f"{query!r} names several applications; use the pid or the bundle id",
                {"candidates": ", ".join(f"{app.name} (pid {app.pid})" for app in matches)},
            )
    return None


def require_app(query: str, apps: Sequence[AppInfo]) -> AppInfo:
    app: AppInfo | None = find_app(query, apps)
    if app is None:
        running: str = ", ".join(sorted({a.name for a in apps if a.kind == "regular"}))
        raise DesktopError(
            f"no running application matches {query!r}; running applications: {running}. desktop_open_app starts one.",
            {"query": query},
        )
    return app


def open_arguments(query: str) -> list[str]:
    """The arguments ``open`` takes for ``query``: a bundle path, a bundle id or an application name. A path to anything
    but a bundle (a binary such as ``.../bin/node``) is refused here: Launch Services would fail it six times over."""
    if "/" in query and not query.endswith(".app"):
        raise DesktopError(
            f"{query!r} is a file path, not an application; name the application (Terminal), its bundle id "
            "(com.apple.Terminal) or its bundle (/Applications/Terminal.app)",
            {"query": query},
        )
    if query.endswith(".app"):
        return [query]
    if _BUNDLE_ID_SHAPE.match(query):
        return ["-b", query]
    return ["-a", query]


def launch(query: str) -> None:
    """Ask Launch Services to open an application by bundle id, path or name; it comes to the front."""
    arguments: list[str] = open_arguments(query)
    for attempt in range(1, _LAUNCH_ATTEMPTS + 1):
        outcome: subprocess.CompletedProcess[str] = subprocess.run(
            ["open", *arguments], capture_output=True, text=True, timeout=_COMMAND_TIMEOUT_S, check=False
        )
        if outcome.returncode == 0:
            return
        error: DesktopError = DesktopError(
            "cannot open the application",
            {"query": query, "code": outcome.returncode, "stderr": outcome.stderr.strip()},
        )
        if _LAUNCH_SERVICES_BUSY not in outcome.stderr or attempt == _LAUNCH_ATTEMPTS:
            raise error
        _log.warning("launch_retry", query=query, attempt=attempt, stderr=outcome.stderr.strip())
        time.sleep(_LAUNCH_RETRY_S)  # right after a quit Launch Services still sees the old process
    raise DesktopError("cannot open the application", {"query": query})


def open_app(query: str, timeout_s: float) -> AppInfo:
    """The running application ``query`` names, started first when it is not running yet."""
    running: AppInfo | None = find_app(query, list_apps())
    if running is not None:
        return running
    launch(query)
    deadline: float = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        started: AppInfo | None = find_app(query, list_apps())
        if started is not None:
            return started
        time.sleep(_POLL_S)
    raise DesktopError(f"{query!r} did not appear within {timeout_s:g} s of opening it", {"query": query})


def _wait_for_front(pid: int, timeout_s: float) -> bool:
    deadline: float = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if frontmost_pid() == pid:
            return True
        time.sleep(_POLL_S)
    return frontmost_pid() == pid


def _raise_through_accessibility(app: AppInfo) -> None:
    """Ask the application to come to the front; the caller verifies the result, so the status codes are not needed."""
    element = ax.app_element(app.pid, _AX_FRONT_TIMEOUT_S)
    AS.AXUIElementSetAttributeValue(element, "AXFrontmost", True)
    _, window = AS.AXUIElementCopyAttributeValue(element, "AXMainWindow", None)
    if window is not None:
        AS.AXUIElementPerformAction(window, "AXRaise")


def activate(app: AppInfo, timeout_s: float) -> None:
    """Bring ``app`` to the front: first through accessibility, then through Launch Services, verifying each time."""
    if frontmost_pid() == app.pid:
        return
    _raise_through_accessibility(app)
    if _wait_for_front(app.pid, timeout_s / 2):
        return
    launch(app.bundle_id or app.bundle_path or app.name)
    if _wait_for_front(app.pid, timeout_s):
        return
    raise DesktopError(
        f"{app.name} did not come to the front",
        {"pid": app.pid, "bundle_id": app.bundle_id or "", "frontmost_pid": frontmost_pid() or 0},
    )
