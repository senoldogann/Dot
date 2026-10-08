"""Moving a running browser session between Dot's own Chrome and the Chrome the user has open.

``dotx browser attach`` does the same for a daemon that is not running (it writes the setting and asks for a restart);
here the running session changes over without a restart, and the setting is written so the next start agrees.
"""

import asyncio
from pathlib import Path

from dotagent.browser.attach import read_debug_endpoint
from dotagent.browser.session import BrowserSession
from dotagent.config import BrowserConfig, BrowserMode
from dotagent.core.errors import BrowserError
from dotagent.core.paths import browser_profile_dir, chrome_data_dir

CHECK_THREAD: str = "attach-check"


def browser_data_dir(mode: BrowserMode) -> Path:
    """The directory the browser connector works from in ``mode``: Dot's own profile, or the data directory of the
    Chrome the user has open (where it finds the debugging port)."""
    match mode:
        case "own":
            return browser_profile_dir()
        case "attach":
            return chrome_data_dir()


async def adopt_mode(session: BrowserSession, mode: BrowserMode) -> None:
    """Make ``session`` work in the Chrome that ``mode`` names. Attaching is proven at once with one blank tab in the
    Chrome of the user (who clicks Allow when Chrome asks) that is closed again; when the proof fails the session goes
    back to the Chrome it had, and the error says what went wrong. Dot's own Chrome is not started for the proof: it
    starts with the first call that needs it."""
    previous: BrowserConfig = session.config
    previous_dir: Path = session.profile_dir
    target_dir: Path = browser_data_dir(mode)
    if mode == "attach":
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
