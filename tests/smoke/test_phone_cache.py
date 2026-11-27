"""The web app on the phone keeps what it read (item 1520) and opens with it drawn when there is no link: a chat read
before is drawn from IndexedDB with its answer, its list and its composer, with no daemon to ask. A real daemon in a
throw-away home. Two phones: WebKit as the owner's iPhone draws it, with a daemon that is gone (the link is there, the
Mac is not: connections are refused), and Chromium with a phone's screen and a touch, sent offline with
``context.set_offline`` (the phone with no link at all; Playwright's WebKit cannot be sent offline, it fails every
navigation, a service worker's copy included).
"""

import asyncio
import re
from typing import Literal

import pytest
import pytest_asyncio
from dock_scenario import GOAL, Scene, read_login, scene
from playwright.async_api import Browser, BrowserContext, Locator, Page, Route, async_playwright, expect
from pwa_scene import keep_shell, kept, open_chat, phone, until_kept, until_true

from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_SIZES: tuple[tuple[int, int], ...] = ((390, 844), (320, 640))
_ANSWER: str = "Done."
Engine = Literal["webkit", "chromium"]

# Whether the data cache holds the list ``key`` (the themes, the agents, the assistant: what the page reads beside
# the status).
_HOLDS_LIST: str = """async (key) => {
  const db = await new Promise((resolve, reject) => {
    const open = indexedDB.open("dot-cache");
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
  const found = await new Promise((resolve) => {
    const request = db.transaction("lists").objectStore("lists").get(key);
    request.onsuccess = () => resolve(request.result !== undefined);
    request.onerror = () => resolve(false);
  });
  db.close();
  return found;
}"""

# Let the cache go of the list ``key``, as a list that was never read on this phone is not there.
_FORGET_LIST: str = """async (key) => {
  const db = await new Promise((resolve, reject) => {
    const open = indexedDB.open("dot-cache");
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
  await new Promise((resolve, reject) => {
    const request = db.transaction("lists", "readwrite").objectStore("lists").delete(key);
    request.onsuccess = () => resolve(undefined);
    request.onerror = () => reject(request.error);
  });
  db.close();
}"""


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


async def test_a_list_read_beside_the_status_that_has_no_copy_does_not_keep_the_page_from_its_chats(
    login_env: dict[str, str],
) -> None:
    """The page opens when Dot's status is read: the themes, the agents' looks and Dot's name are read beside it, and
    one of them that cannot be read (here the themes, of which the phone holds no copy, with no link at all) leaves the
    page with its defaults. It used to leave it on the screen of a Dot out of reach, the chats it held unseen."""
    async with scene(login_env, "rest") as resting, async_playwright() as playwright:
        browser: Browser = await _launch(playwright, "chromium", resting)
        try:
            context: BrowserContext = await phone(browser, playwright, *_SIZES[0])
            page: Page = await open_chat(context, resting.address, resting.token, resting.run.id)
            await expect(_answer(page)).to_be_visible(timeout=15_000)
            await until_kept(page, chats=1, lists=2)
            # The themes are read when the page opens, and kept once the page knows its key is Dot's: the opening
            # after the first holds them. The phone then lets go of them, as one that never read them is without.
            await keep_shell(page)
            await until_true(page, _HOLDS_LIST, "themes")
            await page.evaluate(_FORGET_LIST, "themes")
            assert await page.evaluate(_HOLDS_LIST, "themes") is False
            await page.close()

            await context.set_offline(True)
            again: Page = await context.new_page()
            await again.goto(f"{resting.address}/#/runs/{resting.run.id}")
            await expect(_answer(again)).to_be_visible(timeout=15_000)
            await expect(again.locator(".composer textarea").first).to_be_visible()
            assert await again.get_by_text("Dot cannot reach").count() == 0
            assert await again.get_by_text("This page tries again by itself").count() == 0
            await context.close()
        finally:
            await browser.close()


async def test_a_chat_the_phone_keeps_only_the_newest_events_of_says_that_steps_are_not_shown(
    login_env: dict[str, str],
) -> None:
    """The phone keeps the newest 400 events of a chat: one drawn from such a copy says that the first steps are not
    shown, instead of leaving them out without a word."""
    async with scene(login_env, "work") as working, async_playwright() as playwright:
        for number in range(250):
            await working.runtime.store.add_event(
                working.run.id,
                "tool_call",
                {"call_id": f"long-{number}", "tool": "shell", "args": {"command": "ls"}},
            )
            await working.runtime.store.add_event(
                working.run.id,
                "tool_result",
                {"call_id": f"long-{number}", "tool": "shell", "ok": True, "result": "a\nb", "seconds": 0.1},
            )
        browser: Browser = await _launch(playwright, "chromium", working)
        try:
            context: BrowserContext = await phone(browser, playwright, *_SIZES[0])
            page: Page = await open_chat(context, working.address, working.token, working.run.id)
            await expect(page.locator(".capsule")).to_contain_text(re.compile(r"Working|Waiting on"), timeout=20_000)
            # Read whole from Dot, the chat shows no such note.
            assert await page.locator(".steps-not-shown").count() == 0
            await until_kept(page, chats=1, lists=2)
            await keep_shell(page)
            await page.close()

            await context.set_offline(True)
            again: Page = await context.new_page()
            await again.goto(f"{working.address}/#/runs/{working.run.id}")
            note = again.locator(".steps-not-shown")
            await expect(note).to_be_visible(timeout=20_000)
            await expect(note).to_contain_text("this phone keeps only the newest part of it")
            await context.close()
        finally:
            await browser.close()


async def test_a_read_beside_the_status_that_dot_answers_with_an_error_fails_the_opening_as_it_always_did(
    login_env: dict[str, str],
) -> None:
    """Only a Dot that cannot be reached leaves the opening with its defaults: an error of Dot's (here a 500 on the
    themes) is not swallowed, whatever the page holds of the chats."""
    async with scene(login_env, "rest") as resting, async_playwright() as playwright:
        browser: Browser = await _launch(playwright, "chromium", resting)
        try:
            context: BrowserContext = await phone(browser, playwright, *_SIZES[0], workers="block")

            async def broken(route: Route) -> None:
                await route.fulfill(
                    status=500, content_type="application/json", body='{"message": "the themes folder is broken"}'
                )

            await context.route("**/api/themes", broken)
            page: Page = await open_chat(context, resting.address, resting.token, resting.run.id)
            await expect(page.get_by_text("Dot is open, but it could not answer just now")).to_be_visible(
                timeout=15_000
            )
            await context.close()
        finally:
            await browser.close()
