"""Notifications to the owner: a banner of macOS Notification Center."""

from collections.abc import Awaitable, Callable

from dotagent.core.errors import DesktopError
from dotagent.desktop.script import run_osascript

# Shows a notification with a title and a text.
type Notifier = Callable[[str, str], Awaitable[None]]

_NOTIFY_TIMEOUT_S: float = 15.0
_SCRIPT: str = "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run"


async def show_notification(title: str, message: str) -> None:
    """Show a macOS notification through ``osascript``. The title and the text reach the script as its values, after
    ``--`` (:func:`run_osascript`), never as its words: a title a run gave that starts with ``-e`` was read by
    ``osascript`` as one more line of the script, and a ``property`` line runs a command as the script is compiled (the
    code review of 2026-11-19, item 1240). Raises ``DesktopError`` when it fails or does not end in time: its message
    is the notification's, and the runner's own, which speaks of an application's Automation dialog a banner never waits
    on, is kept as the ``reason`` of its context."""
    try:
        await run_osascript("AppleScript", _SCRIPT, (title, message), (), _NOTIFY_TIMEOUT_S, None, ())
    except DesktopError as error:
        raise DesktopError("the notification could not be shown", {"reason": error.message, **error.context}) from error
