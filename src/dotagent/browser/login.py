"""Signing in to sites for Dot: the dedicated profile opens in an ordinary Chrome window, not an automated one.

Sites such as Google refuse sign-ins in browsers that report being automated, so the profile is opened without
Playwright here; the cookies it saves are used later by the automated sessions, which only works when the window and the
sessions encrypt cookies with the same keychain (see ``keychain``).
"""

import asyncio
from pathlib import Path

from dotagent.browser.keychain import KeychainMode, keychain_switches, read_keychain_mode
from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.browser.profiles import HIDE_CRASH_BUBBLE, keep_session_cookies
from dotagent.browser.safety import normalize_url
from dotagent.core.errors import BrowserError

CHROME_APP: Path = Path("/Applications/Google Chrome.app")
_CHROME_BINARY: Path = CHROME_APP / "Contents" / "MacOS" / "Google Chrome"


def chrome_arguments(profile: Path, url: str | None, keychain: KeychainMode) -> list[str]:
    """The command line that opens ``profile`` in Chrome, on ``url`` when one is given."""
    arguments: list[str] = [str(_CHROME_BINARY), f"--user-data-dir={profile}", "--no-first-run"]
    arguments.extend(("--no-default-browser-check", HIDE_CRASH_BUBBLE))
    arguments.extend(keychain_switches(keychain))
    if url is not None:
        arguments.append(normalize_url(url))
    return arguments


async def open_profile_for_login(profile: Path, url: str | None) -> int:
    """Open the profile in a normal Chrome window and wait until Chrome quits; returns its exit code."""
    keychain: KeychainMode = await _ready_profile(profile)
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        *chrome_arguments(profile, url, keychain)
    )
    return await process.wait()


async def open_sign_in_window(profile: Path) -> asyncio.subprocess.Process:
    """Open the profile in a normal Chrome window for Settings › Browser (item 344), which runs until the owner quits
    it. It runs in a session of its own, so that it stays open while the daemon stops or restarts (launchd ends the
    daemon's process group with it, and a Chrome killed so may lose the sign-ins it had not written yet); what Chrome
    writes goes nowhere."""
    keychain: KeychainMode = await _ready_profile(profile)
    return await asyncio.create_subprocess_exec(
        *chrome_arguments(profile, None, keychain),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )


async def _ready_profile(profile: Path) -> KeychainMode:
    """The keychain mode of ``profile``, made when missing, once Chrome is known to be there and nothing holds it; the
    profile then keeps the session cookies of its last run (item 453), as the automated sessions' does."""
    if not await asyncio.to_thread(_CHROME_BINARY.exists):
        raise BrowserError("Google Chrome is not installed", {"expected": str(_CHROME_BINARY)})
    holder: int | None = await asyncio.to_thread(profile_holder_pid, profile)
    if holder is not None:
        raise BrowserError(
            "the Dot browser profile is already open in another process; quit that Chrome (or stop the Dot service) "
            "and try again",
            {"profile": str(profile), "pid": holder},
        )
    await asyncio.to_thread(lambda: profile.mkdir(mode=0o700, parents=True, exist_ok=True))
    await asyncio.to_thread(keep_session_cookies, profile)
    return await asyncio.to_thread(read_keychain_mode, profile)
