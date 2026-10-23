"""Applications: who is running, who is in front, launching, and bringing one to the front.

NSWorkspace.runningApplications goes stale outside a main run loop, so the list comes from ``lsappinfo`` and the
front application from the accessibility system-wide element; both are live sources.
"""

import os
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

_OPEN: str = "/usr/bin/open"
_LSAPPINFO: str = "/usr/bin/lsappinfo"
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
    tool: str = Path(arguments[0]).name
    try:
        outcome: subprocess.CompletedProcess[str] = subprocess.run(
            arguments, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise DesktopError(
            f"{tool} did not finish within {_COMMAND_TIMEOUT_S:g} s", {"command": " ".join(arguments)}
        ) from error
    if outcome.returncode != 0:
        raise DesktopError(
            f"{tool} failed",
            {"command": " ".join(arguments), "code": outcome.returncode, "stderr": outcome.stderr.strip()},
        )
    return outcome.stdout


def parse_lsappinfo(text: str) -> tuple[AppInfo, ...]:
    """The applications in the output of ``lsappinfo list``; one that has no pid yet is still launching and is left
    out."""
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
    return tuple(replace(app, frontmost=app.pid == front) for app in parse_lsappinfo(_stdout([_LSAPPINFO, "list"])))


def bundle_ids() -> dict[int, str | None]:
    """The bundle id of every registered application by its pid (one ``lsappinfo`` call), for what must know an
    application by what it is and not by the name it shows, which is the Mac's language (item 555)."""
    return {app.pid: app.bundle_id for app in parse_lsappinfo(_stdout([_LSAPPINFO, "list"]))}


def _names_of(app: AppInfo) -> tuple[str, ...]:
    stem: str = Path(app.bundle_path).stem if app.bundle_path else ""
    return tuple(fold_search(name) for name in (app.name, stem, app.bundle_id or "") if name)


def find_app(query: str, apps: Sequence[AppInfo], preferred: frozenset[int]) -> AppInfo | None:
    """The one application ``query`` names (pid, bundle id, name or file name), or ``None``. Several candidates raise,
    unless exactly one of them is ``preferred``: the Chromes Dot's browser works in (item 343), one of which "Google
    Chrome" means while the owner's Chrome and Dot's own both run."""
    wanted: str = query.strip()
    if wanted.isdigit():
        return next((app for app in apps if app.pid == int(wanted)), None)
    if "/" in wanted:
        # A path names the bundle that sits at it, which is what `desktop_open_app` opens (item 418); several copies of
        # one application may sit at the same bundle, and then Dot's own stands as it does for a name.
        at_path: str = os.path.realpath(wanted)
        there: list[AppInfo] = [app for app in apps if app.bundle_path and os.path.realpath(app.bundle_path) == at_path]
        chosen_there: list[AppInfo] = [app for app in there if app.pid in preferred]
        if len(there) == 1:
            return there[0]
        if len(chosen_there) == 1:
            return chosen_there[0]
        if there:
            raise DesktopError(
                f"{query!r} names several applications; use the pid or the bundle id",
                {"candidates": ", ".join(f"{app.name} (pid {app.pid})" for app in there)},
            )
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
        chosen: list[AppInfo] = [app for app in regular or matches if app.pid in preferred]
        if len(chosen) == 1:
            return chosen[0]
        if matches:
            raise DesktopError(
                f"{query!r} names several applications; use the pid or the bundle id",
                {"candidates": ", ".join(f"{app.name} (pid {app.pid})" for app in matches)},
            )
    return None


def require_app(query: str, apps: Sequence[AppInfo], preferred: frozenset[int]) -> AppInfo:
    app: AppInfo | None = find_app(query, apps, preferred)
    if app is None:
        running: str = ", ".join(sorted({a.name for a in apps if a.kind == "regular"}))
        raise DesktopError(
            f"no running application matches {query!r}; running applications: {running}. desktop_open_app starts one.",
            {"query": query},
        )
    return app


def names_a_bundle(query: str) -> bool:
    """Whether ``query`` names an application by the path of its bundle: a ``/`` in it, or ``.app`` at its end, whatever
    its letter case, as the Mac's file systems read it and ``open`` reads a bare ``x.app`` from the daemon's folder."""
    return "/" in query or query.casefold().endswith(".app")


def bundle_path(query: str) -> Path | None:
    """Where the bundle ``query`` names is: the path itself, or where Launch Services resolves the name or bundle id it
    gives instead; ``None`` when no application of this Mac is that name or id. A run may register a bundle it made, so
    a name or an id is no more proof that the owner installed it than a path is."""
    if names_a_bundle(query):
        return Path(query)
    workspace: AppKit.NSWorkspace = AppKit.NSWorkspace.sharedWorkspace()
    if _BUNDLE_ID_SHAPE.match(query):
        url = workspace.URLForApplicationWithBundleIdentifier_(query)
        path: str = url.path() if url is not None else ""
        return Path(path) if path else None
    found: str | None = workspace.fullPathForApplication_(query)
    return Path(found) if found else None


def open_arguments(query: str) -> list[str]:
    """The arguments ``open`` takes for ``query``: a bundle path, a bundle id or an application name. A path to anything
    but a bundle (a binary such as ``.../bin/node``) is refused here: Launch Services would fail it six times over."""
    if names_a_bundle(query):
        if not query.casefold().endswith(".app"):
            raise DesktopError(
                f"{query!r} is a file path, not an application; name the application (Terminal), its bundle id "
                "(com.apple.Terminal) or its bundle (/Applications/Terminal.app)",
                {"query": query},
            )
        return [query]
    if _BUNDLE_ID_SHAPE.match(query):
        return ["-b", query]
    return ["-a", query]


def launch(query: str) -> None:
    """Ask Launch Services to open an application by bundle id, path or name; it comes to the front. A bundle that
    resolves is opened by the path it resolved to, so what starts is the application the check reached and not another
    put in a link's place meanwhile (item 418)."""
    bundle: Path | None = bundle_path(query)
    arguments: list[str] = open_arguments(query)
    if bundle is not None and bundle.name.casefold().endswith(".app"):
        arguments = [os.path.realpath(bundle)]
    for attempt in range(1, _LAUNCH_ATTEMPTS + 1):
        outcome: subprocess.CompletedProcess[str] = subprocess.run(
            [_OPEN, *arguments], capture_output=True, text=True, timeout=_COMMAND_TIMEOUT_S, check=False
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


def open_document(path: Path) -> None:
    """Ask Launch Services to open the file at ``path`` in the application the Mac opens it with, as a double click in
    the Finder does."""
    outcome: subprocess.CompletedProcess[str] = subprocess.run(
        [_OPEN, str(path)], capture_output=True, text=True, timeout=_COMMAND_TIMEOUT_S, check=False
    )
    if outcome.returncode != 0:
        raise DesktopError(
            "the Mac could not open the file",
            {"path": str(path), "code": outcome.returncode, "stderr": outcome.stderr.strip()},
        )


def open_app(query: str, timeout_s: float, preferred: frozenset[int], bundle: str | None) -> AppInfo:
    """The running application ``query`` names, started first when it is not running yet: ``bundle``, the path a check
    of ``query`` came to, is what starts, so an application put in its place meanwhile is not (item 418)."""
    running: AppInfo | None = find_app(query, list_apps(), preferred)
    if running is not None:
        return running
    launch(bundle or query)
    deadline: float = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        started: AppInfo | None = find_app(query, list_apps(), preferred)
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
