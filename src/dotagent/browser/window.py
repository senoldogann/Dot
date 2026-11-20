"""The front of the Mac around Dot's own Chrome (ROADMAP items 97 and 118). macOS brings a newly started application to
the front, and Chrome takes it again whenever a page opens a tab of its own; with ``[browser] raise_window`` off, the
application that had the front gets it back right after, so the owner's work is not interrupted while the run's page
shows the page under "Dot's computer". The tabs Dot opens itself are created in the background and take nothing.
The applications are asked through AppKit, loaded by the functions that ask, so that the browser session loads where it
is not, as on the Linux that runs a cloud agent's unit tests (item 1270)."""

import time
from pathlib import Path

from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.core.errors import DesktopError
from dotagent.core.logs import get_logger
from dotagent.desktop.perms import check_permissions
from dotagent.desktop.types import AppInfo

_log = get_logger(__name__)
# How long Chrome's window is given to take the front after the launch, and how often that is looked at.
_TAKEN_S: float = 2.0
_POLL_S: float = 0.1
# How long the earlier application is given to come back.
_RETURN_S: float = 2.0


def focused_app() -> AppInfo | None:
    """The application with the focus now, ``None`` while none has it (or the desktop cannot be asked)."""
    from dotagent.desktop import apps

    try:
        pid: int | None = apps.frontmost_pid()
        return None if pid is None else next((app for app in apps.list_apps() if app.pid == pid), None)
    except DesktopError as error:
        _log.warning("focused_app_unknown", error=error.message, context=error.context)
        return None


def chrome_to_watch(profile: Path) -> int | None:
    """The process of the Chrome that runs on ``profile``, whose taking of the front is given back, or ``None`` when the
    front cannot be read and given back without accessibility, which this process lacks (said once, in the log)."""
    if not check_permissions().accessibility:
        _log.info("front_not_kept", reason="accessibility is not granted to the program that runs Dot")
        return None
    return profile_holder_pid(profile)


def front_pid() -> int | None:
    """The process of the application with the front now (one accessibility call), ``None`` while none has it or the
    desktop cannot be asked."""
    from dotagent.desktop import apps

    try:
        return apps.frontmost_pid()
    except DesktopError as error:
        _log.warning("front_unknown", error=error.message, context=error.context)
        return None


def _taken(away_from: int, by: int | None) -> bool:
    """Whether the front leaves ``away_from`` within :data:`_TAKEN_S`, for ``by`` when that is given."""
    from dotagent.desktop import apps

    deadline: float = time.monotonic() + _TAKEN_S
    while time.monotonic() < deadline:
        front: int | None = apps.frontmost_pid()
        if front is not None and front != away_from and (by is None or front == by):
            return True
        time.sleep(_POLL_S)
    return False


def give_focus_back(app: AppInfo) -> None:
    """Once Chrome took the front from ``app`` (within :data:`_TAKEN_S`), bring ``app`` back; a Chrome that never took
    it leaves nothing to do. A desktop that refuses is logged, since the run goes on either way."""
    from dotagent.desktop import apps

    try:
        if _taken(app.pid, None):
            apps.activate(app, _RETURN_S)
            _log.info("focus_returned", app=app.name, pid=app.pid)
    except DesktopError as error:
        _log.warning("focus_not_returned", app=app.name, error=error.message, context=error.context)


def return_front(pid: int, chrome_pid: int) -> None:
    """Once the Chrome of ``chrome_pid`` took the front (within :data:`_TAKEN_S`), give it back to the application of
    ``pid``; a front that went anywhere else is the owner's doing and stays. An application that is gone, or a desktop
    that refuses, leaves the front where it is."""
    from dotagent.desktop import apps

    try:
        if not _taken(pid, chrome_pid):
            return
        app: AppInfo | None = next((candidate for candidate in apps.list_apps() if candidate.pid == pid), None)
        if app is None:
            return
        apps.activate(app, _RETURN_S)
        _log.info("focus_returned", app=app.name, pid=app.pid)
    except DesktopError as error:
        _log.warning("focus_not_returned", pid=pid, error=error.message, context=error.context)
