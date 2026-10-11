"""The focus around the start of Dot's own Chrome (ROADMAP item 97). macOS brings a newly started application to the
front; with ``[browser] raise_window`` off, the application that had the focus gets it back right after Chrome's
window appeared, so the owner's work is not interrupted while the run's page shows the page under "Dot's computer"."""

import time

from dotagent.core.errors import DesktopError
from dotagent.core.logs import get_logger
from dotagent.desktop.apps import activate, frontmost_pid, list_apps
from dotagent.desktop.types import AppInfo

_log = get_logger(__name__)
# How long Chrome's window is given to take the front after the launch, and how often that is looked at.
_TAKEN_S: float = 2.0
_POLL_S: float = 0.1
# How long the earlier application is given to come back.
_RETURN_S: float = 2.0


def focused_app() -> AppInfo | None:
    """The application with the focus now, ``None`` while none has it (or the desktop cannot be asked)."""
    try:
        pid: int | None = frontmost_pid()
        return None if pid is None else next((app for app in list_apps() if app.pid == pid), None)
    except DesktopError as error:
        _log.warning("focused_app_unknown", error=error.message, context=error.context)
        return None


def give_focus_back(app: AppInfo) -> None:
    """Once Chrome took the front from ``app`` (within :data:`_TAKEN_S`), bring ``app`` back; a Chrome that never took
    it leaves nothing to do. A desktop that refuses is logged, since the run goes on either way."""
    deadline: float = time.monotonic() + _TAKEN_S
    try:
        while time.monotonic() < deadline:
            front: int | None = frontmost_pid()
            if front is not None and front != app.pid:
                break
            time.sleep(_POLL_S)
        else:
            return
        activate(app, _RETURN_S)
        _log.info("focus_returned", app=app.name, pid=app.pid)
    except DesktopError as error:
        _log.warning("focus_not_returned", app=app.name, error=error.message, context=error.context)
