"""Settings › Browser opens Dot's own profile in a plain Chrome window to sign in to sites (item 344), as
``dotx browser login`` does: Dot's own Chrome, when it is open, is closed first once the owner says so, and the card
says the window is open until the owner quits it. A program stands in for Chrome here (it notes how it was opened and
waits until it is stopped), so no window opens on the Mac; Dot's own Chrome is the real one, hidden. In Chrome wide and
on a phone, light and dark, and in WebKit as an iPhone.
"""

import asyncio
import contextlib
import os
import signal
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

import dotagent.browser.login as login
from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "sign-in-token"
# What stands in for Chrome: it writes its arguments and then its pid beside itself, the pid whole in one rename, so a
# pid that is there says the arguments are too; then it waits to be stopped. (It wrote the pid first, and a test that
# read the arguments as soon as the pid was there now and then found none.)
_STAND_IN: str = """#!/bin/sh
here="$(dirname "$0")"
printf '%s\\n' "$@" > "$here/args"
echo "$$" > "$here/pid.part"
mv "$here/pid.part" "$here/pid"
exec sleep 600
"""
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
_STARTED_S: float = 10.0
_POLL_S: float = 0.05
_OPENED: str = "sessionStorage.setItem('dot.opened', '1')"
# Whether the fades have ended (a page fades in, item 85, also with reduced motion): the endless ones aside.
_STILL: str = (
    "() => document.getAnimations().every((a) => a.effect.getTiming().iterations === Infinity || "
    "a.playState !== 'running')"
)


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[Runtime, str]]:
    """A daemon on the Dot home ``home``, its own Chrome hidden: the runtime, and the address it serves at."""
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    manager: RunManager = RunManager(runtime)
    try:
        stopping: asyncio.Event = asyncio.Event()
        listener = bind_listener("127.0.0.1", 0)
        port: int = listener.getsockname()[1]
        context: ServerContext = ServerContext(
            runtime=runtime,
            manager=manager,
            scheduler=None,
            token=TOKEN,
            host="127.0.0.1",
            port=port,
            started_at=time.time(),
            stopping=stopping,
        )
        server: DotServer = DotServer(serving_config(create_app(context)), stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(_POLL_S)
        try:
            yield runtime, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


def _make_home(tmp_path: Path) -> tuple[Path, Path]:
    """A Dot home whose browser is Dot's own Chrome, hidden, and the program that stands in for a plain Chrome."""
    home: Path = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
    stand_in: Path = tmp_path / "chrome" / "Google Chrome"
    stand_in.parent.mkdir()
    stand_in.write_text(_STAND_IN, encoding="utf-8")
    stand_in.chmod(0o755)
    return home, stand_in


async def _started(stand_in: Path) -> int:
    """The pid of the stand-in once it has started."""
    noted: Path = stand_in.parent / "pid"
    deadline: float = time.monotonic() + _STARTED_S
    while not await asyncio.to_thread(noted.exists):
        assert time.monotonic() < deadline, "the stand-in for Chrome did not start"
        await asyncio.sleep(_POLL_S)
    text: str = await asyncio.to_thread(noted.read_text, encoding="utf-8")
    await asyncio.to_thread(noted.unlink)
    return int(text.strip())


async def _shoot(page: Page, path: Path) -> None:
    """A screenshot once the page's fades have ended."""
    await page.wait_for_function(_STILL)
    await page.screenshot(path=path)


def _card(page: Page) -> Locator:
    return page.locator("section.card").filter(has=page.get_by_text("Sign in to sites in Dot's Chrome"))


async def _open_card(page: Page, address: str, port: int) -> Locator:
    # As a page opened before, without the opening's fade.
    await page.add_init_script(_OPENED)
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    await page.goto(f"{address}/#/settings/browser")
    card: Locator = _card(page)
    await expect(card).to_be_visible()
    return card


async def test_the_browser_card_opens_dots_profile_to_sign_in_and_follows_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, stand_in = await asyncio.to_thread(_make_home, tmp_path)
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "users-chrome"))
    monkeypatch.setattr(login, "_CHROME_BINARY", stand_in)
    async with _served(home) as (runtime, address), async_playwright() as playwright:
        port: int = int(address.rpartition(":")[2])
        # Dot's own Chrome is open, as after a run that browsed.
        await runtime.browser.snapshot("signing-in", True)
        assert runtime.browser.connected
        chromium: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        webkit: Browser = await playwright.webkit.launch(headless=True)
        windows: list[int] = []
        try:
            page: Page = await chromium.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
            card: Locator = await _open_card(page, address, port)
            await card.scroll_into_view_if_needed()
            await _shoot(page, tmp_path / "card-own.png")
            # Dot's Chrome is open: the owner is asked first, and Cancel leaves it open.
            await card.get_by_role("button", name="Sign in…").click()
            dialog: Locator = page.get_by_role("dialog")
            await expect(dialog).to_contain_text("Close Dot's Chrome?")
            await _shoot(page, tmp_path / "card-ask.png")
            await dialog.get_by_role("button", name="Cancel").click()
            await expect(dialog).to_have_count(0)
            assert runtime.browser.connected
            await card.get_by_role("button", name="Sign in…").click()
            await page.get_by_role("dialog").get_by_role("button", name="Close and open").click()
            signing: Locator = card.locator(".signing-in")
            await expect(signing).to_contain_text("Chrome is open on Dot's profile")
            await expect(card.get_by_role("button", name="Sign in…")).to_be_disabled()
            windows.append(await _started(stand_in))
            assert not runtime.browser.connected
            arguments: list[str] = (await asyncio.to_thread((stand_in.parent / "args").read_text)).splitlines()
            assert f"--user-data-dir={runtime.browser.profile_dir}" in arguments
            # The card says so in every look, until the owner quits that Chrome.
            for width, scheme in _SHOTS:
                shown: Page = await chromium.new_page(
                    viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                )
                shown_card: Locator = await _open_card(shown, address, port)
                await expect(shown_card.locator(".signing-in")).to_be_visible()
                await shown_card.locator(".signing-in").scroll_into_view_if_needed()
                await _shoot(shown, tmp_path / f"signing-{width}-{scheme}.png")
                await shown.close()
            for scheme in ("light", "dark"):
                phone_context = await webkit.new_context(
                    **playwright.devices["iPhone 17 Pro Max"], color_scheme=scheme, reduced_motion="reduce"
                )
                phone: Page = await phone_context.new_page()
                phone_card: Locator = await _open_card(phone, address, port)
                await expect(phone_card.locator(".signing-in")).to_be_visible()
                await phone_card.locator(".signing-in").scroll_into_view_if_needed()
                await _shoot(phone, tmp_path / f"signing-iphone-{scheme}.png")
                await phone_context.close()
            os.kill(windows[0], signal.SIGTERM)
            await expect(signing).to_be_hidden()
            await expect(page.get_by_text("Chrome closed; the sign-ins are kept in Dot's profile.")).to_be_visible()
            await expect(card.get_by_role("button", name="Sign in…")).to_be_enabled()
            # With Dot's Chrome closed nothing needs asking: the window opens at once.
            await card.get_by_role("button", name="Sign in…").click()
            await expect(signing).to_be_visible()
            await expect(page.get_by_role("dialog")).to_have_count(0)
            windows.append(await _started(stand_in))
            os.kill(windows[1], signal.SIGTERM)
            await expect(signing).to_be_hidden()
        finally:
            await chromium.close()
            await webkit.close()
            for pid in windows:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGTERM)
