"""Signing in to sites for Dot: the dedicated profile opens in an ordinary Chrome window, not an automated one.

Sites such as Google refuse sign-ins in browsers that report being automated, so the profile is opened without
Playwright here; the cookies it saves are used later by the automated sessions, which only works when the window and the
sessions encrypt cookies with the same keychain (see ``keychain``).
"""

import asyncio
from pathlib import Path

from dotagent.browser.keychain import KeychainMode, keychain_switches, read_keychain_mode
from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.browser.safety import normalize_url
from dotagent.core.errors import BrowserError

CHROME_APP: Path = Path("/Applications/Google Chrome.app")
_CHROME_BINARY: Path = CHROME_APP / "Contents" / "MacOS" / "Google Chrome"


def chrome_arguments(profile: Path, url: str | None, keychain: KeychainMode) -> list[str]:
    """The command line that opens ``profile`` in Chrome, on ``url`` when one is given."""
    arguments: list[str] = [str(_CHROME_BINARY), f"--user-data-dir={profile}", "--no-first-run"]
    arguments.append("--no-default-browser-check")
    arguments.extend(keychain_switches(keychain))
    if url is not None:
        arguments.append(normalize_url(url))
    return arguments


async def open_profile_for_login(profile: Path, url: str | None) -> int:
    """Open the profile in a normal Chrome window and wait until Chrome quits; returns its exit code."""
    process: asyncio.subprocess.Process = await start_login_chrome(profile, url, None)
    return await process.wait()


async def start_login_chrome(profile: Path, url: str | None, output: int | None) -> asyncio.subprocess.Process:
    """Open the profile in a normal Chrome window, which runs until the owner quits it. Chrome writes what it says to
    ``output`` (``asyncio.subprocess.DEVNULL``, or ``None`` for where this process writes, a terminal)."""
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
    keychain: KeychainMode = await asyncio.to_thread(read_keychain_mode, profile)
    return await asyncio.create_subprocess_exec(*chrome_arguments(profile, url, keychain), stdout=output, stderr=output)
