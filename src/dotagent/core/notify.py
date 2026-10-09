"""Notifications to the owner: a banner of macOS Notification Center."""

import asyncio
from collections.abc import Awaitable, Callable

from dotagent.core.errors import ToolExecutionError

# Shows a notification with a title and a text.
type Notifier = Callable[[str, str], Awaitable[None]]

_NOTIFY_TIMEOUT_S: float = 15.0


async def show_notification(title: str, message: str) -> None:
    """Show a macOS notification through ``osascript``; the texts are passed as arguments, never as script source.
    Raises ``ToolExecutionError`` when it fails or does not end in time."""
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "/usr/bin/osascript",
        "-e",
        "on run argv",
        "-e",
        "display notification (item 2 of argv) with title (item 1 of argv)",
        "-e",
        "end run",
        title,
        message,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=_NOTIFY_TIMEOUT_S)
    except TimeoutError as error:
        process.kill()
        await process.wait()
        raise ToolExecutionError("the notification did not show in time", {"timeout_s": _NOTIFY_TIMEOUT_S}) from error
    if process.returncode != 0:
        raise ToolExecutionError(
            "the notification could not be shown",
            {"returncode": process.returncode, "stderr": stderr.decode("utf-8", errors="replace")[:300]},
        )
