"""A sign-in made in an ordinary Chrome window reaches the automated browser through the shared profile, and the import
finds the cookie database that a real Chrome writes.

The window and the automated sessions both run on the mock keychain, so no Keychain item is touched; the test fails when
the two disagree about the key, because the automated Chrome then cannot read the cookie the window saved.

Dot's own Chrome starts without Playwright's --disable-extensions, so the extensions installed in its profile load.
"""

import asyncio
import os
import signal
import socket
import time
from pathlib import Path

import httpx
import pytest
from playwright.async_api import BrowserContext, async_playwright

from dotagent.browser.chrome_import import ImportReport, has_stored_cookies, import_chrome_profile
from dotagent.browser.keychain import KeychainMode, read_keychain_mode
from dotagent.browser.login import chrome_arguments
from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.cli_browser import _readable_cookies
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config

pytestmark = pytest.mark.browser

# Whose interface the browser never opens: the daemon's default address.
SERVER: ServerConfig = load_default_config().server
_READY_TIMEOUT_S: float = 20.0
_EXIT_TIMEOUT_S: float = 20.0
_POLL_S: float = 0.2
# How often a test looks for the lock Chrome takes on its profile as it starts.
_LOCK_POLL_S: float = 0.005


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def wait_until_listening(port: int) -> None:
    deadline: float = time.monotonic() + _READY_TIMEOUT_S
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await client.get(f"http://127.0.0.1:{port}/json/version")
                return
            except httpx.TransportError:
                if time.monotonic() > deadline:
                    raise
                await asyncio.sleep(_POLL_S)


async def save_cookie_in_ordinary_chrome(profile: Path, url: str) -> None:
    """Start Chrome the way ``dotx browser login`` does (hidden, with a debugging port), set one cookie, quit it."""
    port: int = free_port()
    keychain: KeychainMode = read_keychain_mode(profile)
    command: list[str] = [
        *chrome_arguments(profile, None, keychain),
        "--headless=new",
        f"--remote-debugging-port={port}",
    ]
    chrome: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        await wait_until_listening(port)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            await browser.contexts[0].add_cookies(
                [{"name": "dot_session", "value": "signed-in", "url": url, "expires": time.time() + 3600}]
            )
            quit_chrome = await browser.new_browser_cdp_session()
            await quit_chrome.send("Browser.close")
        await asyncio.wait_for(chrome.wait(), _EXIT_TIMEOUT_S)
    finally:
        if chrome.returncode is None:
            chrome.kill()


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    directory: Path = tmp_path / "profile"
    directory.mkdir(mode=0o700)
    return directory


@pytest.fixture
def headless_config(tmp_path: Path) -> BrowserConfig:
    settings: Path = tmp_path / "config.toml"
    settings.write_text("[browser]\nheadless = true\n", encoding="utf-8")
    return load_config(settings).browser


@pytest.fixture
def attach_config(tmp_path: Path) -> BrowserConfig:
    settings: Path = tmp_path / "attach.toml"
    settings.write_text('[browser]\nmode = "attach"\n', encoding="utf-8")
    return load_config(settings).browser


async def test_a_cookie_saved_in_the_sign_in_window_is_read_by_the_automated_browser(
    profile: Path, headless_config: BrowserConfig, tmp_path: Path
) -> None:
    await save_cookie_in_ordinary_chrome(profile, "http://127.0.0.1:9")
    session: BrowserSession = BrowserSession(headless_config, SERVER, profile, tmp_path / "downloads", no_runs_at_work)
    try:
        assert await session.stored_cookie_count() == 1
    finally:
        await session.aclose()


async def test_the_check_of_an_import_runs_in_dots_own_chrome_whatever_the_browser_mode(
    profile: Path, attach_config: BrowserConfig
) -> None:
    await save_cookie_in_ordinary_chrome(profile, "http://127.0.0.1:9")
    assert await _readable_cookies(attach_config, SERVER, profile) == 1


async def test_the_sign_ins_of_a_profile_that_a_real_chrome_wrote_are_imported(profile: Path, tmp_path: Path) -> None:
    await save_cookie_in_ordinary_chrome(profile, "http://127.0.0.1:9")
    dot_profile: Path = tmp_path / "dot"
    report: ImportReport = await asyncio.to_thread(import_chrome_profile, profile, "Default", dot_profile)
    assert report.cookies == 1
    assert has_stored_cookies(dot_profile)
    assert read_keychain_mode(dot_profile) == "system"


async def _chrome_starting(profile: Path) -> None:
    """Return once a Chrome has taken the lock on ``profile``: it is starting."""
    while True:
        if (profile / "SingletonLock").is_symlink():
            return
        await asyncio.sleep(_LOCK_POLL_S)


async def test_a_launch_given_up_while_chrome_starts_leaves_the_profile_free(
    profile: Path, headless_config: BrowserConfig, tmp_path: Path
) -> None:
    """Item 144: a call given up while Dot's own Chrome starts (its run stopped) leaves no Chrome holding the profile:
    the one the launch started is closed once it is up, and the next call starts Chrome again."""
    session: BrowserSession = BrowserSession(headless_config, SERVER, profile, tmp_path / "downloads", no_runs_at_work)
    try:
        opening: asyncio.Task[int] = asyncio.create_task(session.stored_cookie_count())
        async with asyncio.timeout(_READY_TIMEOUT_S):
            await _chrome_starting(profile)
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await opening
        assert profile_holder_pid(profile) is None
        async with asyncio.timeout(_READY_TIMEOUT_S):
            assert await session.stored_cookie_count() == 0
    finally:
        await session.aclose()


async def test_a_close_of_chrome_that_fails_still_stops_the_driver_and_frees_the_profile(
    profile: Path, headless_config: BrowserConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 176: when closing Dot's own Chrome fails with an error that is not Playwright's, the error is raised and the
    Playwright driver is stopped all the same, which ends the Chrome it started, so nothing is left holding the
    profile."""
    session: BrowserSession = BrowserSession(headless_config, SERVER, profile, tmp_path / "downloads", no_runs_at_work)
    try:
        assert await session.stored_cookie_count() == 0
        assert profile_holder_pid(profile) is not None
        context: BrowserContext | None = session._context
        assert context is not None

        async def failing_close() -> None:
            raise RuntimeError("closing Chrome failed")

        monkeypatch.setattr(context, "close", failing_close)
        with pytest.raises(RuntimeError, match="closing Chrome failed"):
            await session.aclose()
        deadline: float = time.monotonic() + _EXIT_TIMEOUT_S
        while profile_holder_pid(profile) is not None:
            assert time.monotonic() < deadline, "a Chrome still holds the profile after the driver was to stop"
            await asyncio.sleep(_LOCK_POLL_S)
    finally:
        # A failing run must not leave the Chrome behind either.
        holder: int | None = profile_holder_pid(profile)
        if holder is not None:
            os.kill(holder, signal.SIGKILL)


async def test_dots_own_chrome_starts_without_the_switch_that_turns_extensions_off(
    profile: Path, headless_config: BrowserConfig, tmp_path: Path
) -> None:
    """Item 264: Playwright starts Chrome with --disable-extensions, which keeps the extensions installed in Dot's
    profile from loading; Dot's own Chrome is started without it, and still without the bar of automated software."""
    session: BrowserSession = BrowserSession(headless_config, SERVER, profile, tmp_path / "downloads", no_runs_at_work)
    try:
        assert await session.stored_cookie_count() == 0
        holder: int | None = profile_holder_pid(profile)
        assert holder is not None
        listing: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            "/bin/ps", "-ww", "-o", "args=", "-p", str(holder), stdout=asyncio.subprocess.PIPE
        )
        output, _ = await listing.communicate()
        switches: list[str] = output.decode().split()
        assert "--remote-debugging-pipe" in switches, switches
        assert "--disable-extensions" not in switches
        assert "--enable-automation" not in switches
    finally:
        await session.aclose()
