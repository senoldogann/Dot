"""The web app on the phone keeps what it read (item 1520) and opens with it drawn when there is no link: a chat read
before is drawn from IndexedDB with its answer, its list and its composer, with no daemon to ask. A real daemon in a
throw-away home. Two phones: WebKit as the owner's iPhone draws it, with a daemon that is gone (the link is there, the
Mac is not: connections are refused), and Chromium with a phone's screen and a touch, sent offline with
``context.set_offline`` (the phone with no link at all; Playwright's WebKit cannot be sent offline, it fails every
navigation, a service worker's copy included).
"""

import asyncio
from typing import Literal

import pytest
import pytest_asyncio
from dock_scenario import GOAL, Scene, read_login, scene
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect
from pwa_scene import keep_shell, kept, open_chat, phone, until_kept

from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_SIZES: tuple[tuple[int, int], ...] = ((390, 844), (320, 640))
_ANSWER: str = "Done."
Engine = Literal["webkit", "chromium"]


def _answer(page: Page) -> Locator:
    """The answer of the chat as the conversation draws it (the sidebar's row says it too, hidden on a phone)."""
    return page.get_by_role("article", name="Answer").get_by_text(_ANSWER)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login()


async def _launch(playwright: object, engine: Engine, scene_: Scene) -> Browser:
    if engine == "webkit":
        return await playwright.webkit.launch(headless=True)  # type: ignore[attr-defined]
    return await playwright.chromium.launch(  # type: ignore[attr-defined]
        channel=scene_.runtime.config.browser.channel, headless=True
    )


async def _lose_the_link(engine: Engine, context: BrowserContext, scene_: Scene) -> None:
    """The phone with no link at all (Chromium), or with a link and no Mac (WebKit: the daemon stops serving)."""
    if engine == "chromium":
        await context.set_offline(True)
    else:
        scene_.stop_serving()
        await asyncio.sleep(0.5)


@pytest.mark.parametrize("engine", ["webkit", "chromium"])
async def test_a_chat_read_before_is_drawn_from_the_phone_when_there_is_no_link(
    login_env: dict[str, str], engine: Engine
) -> None:
    async with scene(login_env, "rest") as resting, async_playwright() as playwright:
        browser: Browser = await _launch(playwright, engine, resting)
        try:
            context: BrowserContext = await phone(browser, playwright, *_SIZES[0])
            page: Page = await context.new_page()
            await page.goto(ui_url("127.0.0.1", int(resting.address.rpartition(":")[2]), resting.token))
            await page.goto(f"{resting.address}/#/runs/{resting.run.id}")
            await expect(_answer(page)).to_be_visible(timeout=15_000)
            # The chat and the lists the shell draws are kept on the phone as they are read.
            await until_kept(page, chats=1, lists=2)
            await keep_shell(page)
            await expect(_answer(page)).to_be_visible(timeout=15_000)

            # The link is lost and Dot is opened again, in a page of its own so that nothing of the page before stays:
            # the chat is there, drawn from the copy, and no screen says that Dot cannot be reached in its place.
            await page.close()
            await _lose_the_link(engine, context, resting)
            for width, height in _SIZES:
                again: Page = await context.new_page()
                await again.set_viewport_size({"width": width, "height": height})
                await again.goto(f"{resting.address}/#/runs/{resting.run.id}")
                await expect(_answer(again)).to_be_visible(timeout=15_000)
                await expect(again.get_by_role("heading", name=GOAL)).to_be_visible()
                await expect(again.locator(".composer textarea").first).to_be_visible()
                assert await again.get_by_text("Dot cannot reach").count() == 0
                assert await again.get_by_text("This page tries again by itself").count() == 0
                await again.close()
            await context.close()
        finally:
            await browser.close()


@pytest.mark.parametrize("engine", ["chromium"])
async def test_a_phone_that_forgets_its_key_keeps_nothing_of_its_chats(
    login_env: dict[str, str], engine: Engine
) -> None:
    async with scene(login_env, "rest") as resting, async_playwright() as playwright:
        browser: Browser = await _launch(playwright, engine, resting)
        try:
            context: BrowserContext = await phone(browser, playwright)
            page: Page = await open_chat(context, resting.address, resting.token, resting.run.id)
            await expect(_answer(page)).to_be_visible(timeout=15_000)
            await until_kept(page, chats=1, lists=2)
            # Another key at the same address: what was kept for the first is dropped as the second claims the cache.
            await page.evaluate("localStorage.setItem('dot.token', 'another-key')")
            await page.reload()
            await asyncio.sleep(1.5)
            held: dict[str, int] = await kept(page)
            assert held["chats"] == 0, held
            await context.close()
        finally:
            await browser.close()
