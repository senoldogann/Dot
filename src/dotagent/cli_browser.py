"""``dotx browser``: let Dot work in the Chrome that is open, or bring the sign-ins of a personal Chrome profile into
Dot's own browser profile."""

import asyncio
import sys
from pathlib import Path

from dotagent.browser.attach import DebugEndpoint, read_debug_endpoint
from dotagent.browser.chrome_import import ImportReport, has_stored_cookies, import_chrome_profile, require_importable
from dotagent.browser.login import open_profile_for_login
from dotagent.browser.profiles import profile_dir
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import Attachment
from dotagent.cli_commands import (
    BrowserAttachCommand,
    BrowserDetachCommand,
    BrowserImportChromeCommand,
    BrowserLoginCommand,
    Command,
)
from dotagent.config import AppConfig, BrowserConfig, ServerConfig, load_user_config
from dotagent.config_edit import set_browser_mode
from dotagent.core.errors import BrowserError, InvalidValueError
from dotagent.core.paths import chrome_data_dir, config_path, downloads_dir

_MEGABYTE: int = 1_000_000
_CHECK_THREAD: str = "attach-check"


async def _readable_cookies(config: BrowserConfig, server: ServerConfig, profile: Path) -> int:
    """Open the profile in a hidden Chrome of its own, whatever ``[browser] mode`` says, and count the cookies that
    Chrome can read. It takes no DevTools connections: the port is the daemon's Chrome's."""
    session: BrowserSession = BrowserSession(
        config.model_copy(update={"headless": True, "mode": "own", "debug_port": 0}),
        server,
        profile,
        downloads_dir(),
        no_runs_at_work,
    )
    try:
        return await session.stored_cookie_count()
    finally:
        await session.aclose()


async def cmd_browser_import_chrome(command: BrowserImportChromeCommand) -> int:
    settings: AppConfig = load_user_config()
    # Into the profile Dot's own Chrome works on (item 355).
    dot_profile: Path = profile_dir(settings.browser.profile)
    require_importable(dot_profile)
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
    if report.staging_left is not None:
        sys.stdout.write(
            f"Warning: {report.staging_left} could not be removed. It holds sign-ins and local storage (what Dot's "
            "profile held before, or a plain copy of your Chrome's): delete it.\n"
        )
    readable: int = await _readable_cookies(settings.browser, settings.server, dot_profile)
    if readable == 0:
        raise BrowserError(
            'Chrome could not read the copied cookies; allow Chrome to use "Chrome Safe Storage" in the Keychain and '
            "import again with --replace (Chrome has dropped the cookies it could not read)",
            {"profile": str(dot_profile), "copied": report.cookies},
        )
    sys.stdout.write(
        f"Chrome reads {readable} cookies from Dot's profile. The browser tools are signed in the way your Chrome was; "
        "sign-ins that expire need a new import.\n"
    )
    if readable < report.loadable:
        sys.stdout.write(
            f"Note: {report.loadable} cookies were copied that Chrome can be expected to read, and it reads "
            f"{readable}; the others could not be read with the key (or expired meanwhile).\n"
        )
    return 0


async def cmd_browser_attach(command: BrowserAttachCommand) -> int:
    """Check that Dot can work in a tab of the Chrome that is open, then switch ``[browser] mode`` to ``attach``."""
    settings: AppConfig = load_user_config()
    config: BrowserConfig = settings.browser
    data_dir: Path = chrome_data_dir()
    endpoint: DebugEndpoint = await asyncio.to_thread(read_debug_endpoint, data_dir)
    sys.stdout.write(
        f"Chrome is open and allows remote debugging (port {endpoint.port}).\n"
        f"Chrome now asks whether to allow the connection: click Allow within {config.attach_timeout_s:.0f} seconds. "
        "Dot then opens one blank tab in your Chrome, checks that it can work in it, and closes it again. "
        "Your other tabs are not touched.\n"
    )
    session: BrowserSession = BrowserSession(
        config.model_copy(update={"mode": "attach"}), settings.server, data_dir, downloads_dir(), no_runs_at_work
    )
    try:
        attachment: Attachment = await session.attachment()
        # A blank tab, which nothing reads but this check; it hides as a run's view would.
        await session.snapshot(_CHECK_THREAD, True)
    finally:
        await session.aclose()
    await asyncio.to_thread(set_browser_mode, config_path(), "attach")
    sys.stdout.write(
        f"Worked in a test tab of Chrome {attachment.browser_version} ({attachment.open_tabs} tabs open) and closed "
        "it.\n"
        f'Saved mode = "attach" under [browser] in {config_path()}.\n'
        "Restart the Dot service so it uses your Chrome: dotx service restart\n"
        "Chrome asks you to click Allow once when the service first opens a page. "
        "To go back to Dot's own Chrome: dotx browser detach\n"
    )
    return 0


async def cmd_browser_detach(command: BrowserDetachCommand) -> int:
    """Switch ``[browser] mode`` back to ``own``."""
    await asyncio.to_thread(set_browser_mode, config_path(), "own")
    sys.stdout.write(
        f'Saved mode = "own" under [browser] in {config_path()}.\n'
        "Restart the Dot service so it uses Dot's own Chrome again: dotx service restart\n"
    )
    return 0


async def cmd_browser_login(command: BrowserLoginCommand) -> int:
    sys.stdout.write(
        "Chrome opens with Dot's own profile. Sign in to the sites Dot should be able to use, then quit Chrome "
        "(Cmd+Q). Dot keeps the cookies; it never sees your passwords.\n"
    )
    code: int = await open_profile_for_login(profile_dir(load_user_config().browser.profile), command.url)
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
