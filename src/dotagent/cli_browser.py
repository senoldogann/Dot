"""``dotx browser``: let Dot work in the Chrome that is open, or bring the sign-ins of a personal Chrome profile into Dot's
own browser profile."""

import asyncio
import sys
from pathlib import Path

from dotagent.browser.attach import DebugEndpoint, read_debug_endpoint
from dotagent.browser.chrome_import import ImportReport, has_stored_cookies, import_chrome_profile
from dotagent.browser.login import open_profile_for_login
from dotagent.browser.session import BrowserSession
from dotagent.browser.types import Attachment
from dotagent.cli_commands import (
    BrowserAttachCommand,
    BrowserDetachCommand,
    BrowserImportChromeCommand,
    BrowserLoginCommand,
    Command,
)
from dotagent.config import BrowserConfig, load_user_config
from dotagent.config_edit import set_browser_mode
from dotagent.core.errors import BrowserError, InvalidValueError
from dotagent.core.paths import browser_profile_dir, chrome_data_dir, config_path, downloads_dir

_MEGABYTE: int = 1_000_000
_CHECK_THREAD: str = "attach-check"


async def _readable_cookies(config: BrowserConfig, profile: Path) -> int:
    """Open the profile in a hidden Chrome of its own, whatever ``[browser] mode`` says, and count the cookies that
    Chrome can read."""
    session: BrowserSession = BrowserSession(
        config.model_copy(update={"headless": True, "mode": "own"}), profile, downloads_dir()
    )
    try:
        return await session.stored_cookie_count()
    finally:
        await session.aclose()


async def cmd_browser_import_chrome(command: BrowserImportChromeCommand) -> int:
    dot_profile: Path = browser_profile_dir()
    if has_stored_cookies(dot_profile) and not command.replace:
        raise BrowserError(
            "Dot's own profile already holds sign-ins; repeat with --replace to replace them",
            {"profile": str(dot_profile)},
        )
    sys.stdout.write(f"Copying the sign-ins of the Chrome profile '{command.profile}'. Chrome must be quit (Cmd+Q).\n")
    report: ImportReport = await asyncio.to_thread(
        import_chrome_profile, chrome_data_dir(), command.profile, dot_profile
    )
    sys.stdout.write(
        f"Copied {report.cookies} cookies and {report.local_storage_bytes / _MEGABYTE:.1f} MB of local storage from "
        f"{report.source}. Your Chrome profile was only read.\n"
        'Checking that Chrome can read them. macOS may ask whether Chrome may use "Chrome Safe Storage" in your '
        "Keychain: choose Always Allow.\n"
    )
    readable: int = await _readable_cookies(load_user_config().browser, dot_profile)
    if readable == 0:
        raise BrowserError(
            'Chrome could not read the copied cookies; allow Chrome to use "Chrome Safe Storage" in the Keychain and '
            "import again",
            {"profile": str(dot_profile), "copied": report.cookies},
        )
    sys.stdout.write(
        f"Chrome reads {readable} cookies from Dot's profile. The browser tools are signed in the way your Chrome was; "
        "sign-ins that expire need a new import.\n"
    )
    return 0


async def cmd_browser_attach(command: BrowserAttachCommand) -> int:
    """Check that Dot can work in a tab of the Chrome that is open, then switch ``[browser] mode`` to ``attach``."""
    config: BrowserConfig = load_user_config().browser
    data_dir: Path = chrome_data_dir()
    endpoint: DebugEndpoint = await asyncio.to_thread(read_debug_endpoint, data_dir)
    sys.stdout.write(
        f"Chrome is open and allows remote debugging (port {endpoint.port}).\n"
        f"Chrome now asks whether to allow the connection: click Allow within {config.attach_timeout_s:.0f} seconds. "
        "Dot then opens one blank tab in your Chrome, checks that it can work in it, and closes it again. "
        "Your other tabs are not touched.\n"
    )
    session: BrowserSession = BrowserSession(config.model_copy(update={"mode": "attach"}), data_dir, downloads_dir())
    try:
        attachment: Attachment = await session.attachment()
        await session.snapshot(_CHECK_THREAD)
    finally:
        await session.aclose()
    await asyncio.to_thread(set_browser_mode, config_path(), "attach")
    sys.stdout.write(
        f"Worked in a test tab of Chrome {attachment.browser_version} ({attachment.open_tabs} tabs open) and closed it.\n"
        f'Saved mode = "attach" under [browser] in {config_path()}.\n'
        "Restart the Dot service so it uses your Chrome: dotx service install\n"
        "Chrome asks you to click Allow once when the service first opens a page. "
        "To go back to Dot's own Chrome: dotx browser detach\n"
    )
    return 0


async def cmd_browser_detach(command: BrowserDetachCommand) -> int:
    """Switch ``[browser] mode`` back to ``own``."""
    await asyncio.to_thread(set_browser_mode, config_path(), "own")
    sys.stdout.write(
        f'Saved mode = "own" under [browser] in {config_path()}.\n'
        "Restart the Dot service so it uses Dot's own Chrome again: dotx service install\n"
    )
    return 0


async def cmd_browser_login(command: BrowserLoginCommand) -> int:
    sys.stdout.write(
        "Chrome opens with Dot's own profile. Sign in to the sites Dot should be able to use, then quit Chrome "
        "(Cmd+Q). Dot keeps the cookies; it never sees your passwords.\n"
    )
    code: int = await open_profile_for_login(browser_profile_dir(), command.url)
    sys.stdout.write("Chrome closed; the sign-ins are saved in Dot's profile.\n")
    return code


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case BrowserLoginCommand():
            return await cmd_browser_login(command)
        case BrowserImportChromeCommand():
            return await cmd_browser_import_chrome(command)
        case BrowserAttachCommand():
            return await cmd_browser_attach(command)
        case BrowserDetachCommand():
            return await cmd_browser_detach(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
