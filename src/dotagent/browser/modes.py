"""Moving a running browser session between Dot's own Chrome and the Chrome the user has open.

``dotx browser attach`` does the same for a daemon that is not running (it writes the setting and asks for a restart);
here the running session changes over without a restart, and the setting is written so the next start agrees. A switch
to the Chrome of the user while it is closed opens it first, as a click on its icon does (item 261): the user asked for
their Chrome, and only its remote debugging switch and the Allow it asks for are left to them.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from dotagent.browser.attach import CHROME_NOT_RUNNING, inspect_chrome, read_debug_endpoint
from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.browser.profiles import profile_dir
from dotagent.browser.session import BrowserSession
from dotagent.config import BrowserConfig
from dotagent.core.errors import BrowserError
from dotagent.core.paths import chrome_data_dir, default_chrome_data_dir
from dotagent.store.models import BrowserMode

CHECK_THREAD: str = "attach-check"
# The app whose data directory Dot reads by default (``default_chrome_data_dir``).
CHROME_APP_ID: str = "com.google.Chrome"
# How long a Chrome that Dot opened may take to start, and then to listen for a debugger while its remote debugging is
# on; one that does not listen by then has it off, and the switch says how to turn it on.
START_TIMEOUT_S: float = 30.0
LISTEN_TIMEOUT_S: float = 10.0
_OPEN: str = "/usr/bin/open"
_OPEN_TIMEOUT_S: float = 30.0
_POLL_S: float = 0.25

# Opens the Chrome of the user on a data directory: :func:`open_users_chrome`; the tests start a scratch Chrome.
ChromeOpener = Callable[[Path], Awaitable[None]]


def browser_data_dir(mode: BrowserMode, profile: str) -> Path:
    """The directory the browser connector works from in ``mode``: the folder of Dot's own profile ``profile`` (item
    355), or the data directory of the Chrome the user has open (where it finds the debugging port)."""
    match mode:
        case "own":
            return profile_dir(profile)
        case "attach":
            return chrome_data_dir()


def chrome_app_for(data_dir: Path) -> str | None:
    """The app Dot opens for the Chrome on ``data_dir``: Google Chrome for its own data directory, none for another one
    (``DOT_CHROME_DIR``), whose app Dot does not know."""
    return CHROME_APP_ID if data_dir == default_chrome_data_dir() else None


async def open_users_chrome(data_dir: Path) -> None:
    """Open the Chrome on ``data_dir`` as a click on its icon does; for a data directory whose app Dot does not know,
    the user is asked to open it."""
    app: str | None = chrome_app_for(data_dir)
    if app is None:
        raise BrowserError(CHROME_NOT_RUNNING, {"data_dir": str(data_dir)})
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        _OPEN, "-b", app, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        async with asyncio.timeout(_OPEN_TIMEOUT_S):
            _, error = await process.communicate()
    except TimeoutError as timed_out:
        process.kill()
        await process.wait()
        raise BrowserError(
            "Chrome could not be opened; open it yourself and try again", {"app": app, "waited_s": _OPEN_TIMEOUT_S}
        ) from timed_out
    if process.returncode != 0:
        raise BrowserError(
            "Chrome could not be opened; open it yourself and try again",
            {"app": app, "status": process.returncode, "error": error.decode(errors="replace").strip()},
        )


def _listens(data_dir: Path) -> bool:
    """Whether the Chrome on ``data_dir`` listens for a debugger now. A port file that Chrome is still writing reads as
    not yet: the switch reads it once more after the wait, and that read says what is wrong."""
    try:
        return inspect_chrome(data_dir).remote_debugging
    except BrowserError:
        return False


async def reach_chrome(data_dir: Path, open_chrome: ChromeOpener) -> None:
    """Make sure the Chrome on ``data_dir`` runs before a switch to it: one that is closed is opened with
    ``open_chrome`` and waited for, until it runs and, while its remote debugging is on, listens for a debugger."""
    if await asyncio.to_thread(profile_holder_pid, data_dir) is not None:
        return
    await open_chrome(data_dir)
    deadline: float = time.monotonic() + START_TIMEOUT_S
    while await asyncio.to_thread(profile_holder_pid, data_dir) is None:
        if time.monotonic() > deadline:
            raise BrowserError(
                "Chrome was opened but did not start; try again",
                {"data_dir": str(data_dir), "waited_s": START_TIMEOUT_S},
            )
        await asyncio.sleep(_POLL_S)
    deadline = time.monotonic() + LISTEN_TIMEOUT_S
    while time.monotonic() <= deadline:
        if await asyncio.to_thread(_listens, data_dir):
            return
        await asyncio.sleep(_POLL_S)


async def adopt_mode(session: BrowserSession, mode: BrowserMode, open_chrome: ChromeOpener) -> None:
    """Make ``session`` work in the Chrome that ``mode`` names. The Chrome of the user is opened when it is closed
    (``open_chrome``, :func:`reach_chrome`). Attaching is proven at once with one blank tab in the Chrome of the user
    (who clicks Allow when Chrome asks) that is closed again; when the proof fails the session goes back to the Chrome
    it had, and the error says what went wrong. Dot's own Chrome is not started for the proof: it starts with the first
    call that needs it."""
    previous: BrowserConfig = session.config
    previous_dir: Path = session.profile_dir
    target_dir: Path = browser_data_dir(mode, previous.profile)
    if mode == "attach":
        await reach_chrome(target_dir, open_chrome)
        await asyncio.to_thread(read_debug_endpoint, target_dir)
    await session.switch(previous.model_copy(update={"mode": mode}), target_dir)
    if mode != "attach":
        return
    try:
        await session.attachment()
        await session.snapshot(CHECK_THREAD)
        await session.release_thread(CHECK_THREAD)
    except BrowserError:
        await session.switch(previous, previous_dir)
        raise
