"""A phone's reads on a bad link (item 1521): a read that is lost is tried again (three tries in all), no try is spent
while the phone has no link at all, the same read asked twice while it is on its way is one request, a read is cancelled
when the chat is left, and a chat that was away through a long run reads its newest 400 events and says that some steps
are not shown. A real daemon in a throw-away home; the bad link is a route of the browser that aborts or holds a
request, and ``context.set_offline`` where the phone has no link at all (Chromium: Playwright's WebKit cannot be sent
offline).
"""

import asyncio
import re
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from dock_scenario import Scene, read_login, scene
from playwright.async_api import Browser, BrowserContext, Page, Request, Route, async_playwright, expect
from pwa_scene import open_chat, phone, until_kept

from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_ANSWER: str = "Done."


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def resting(login_env: dict[str, str]) -> AsyncIterator[Scene]:
    """A daemon with one chat whose run is done."""
    async with scene(login_env, "rest") as found:
        yield found


async def _connected(context: BrowserContext, scene_: Scene) -> Page:
    """A page signed in at the daemon, on the home: the daemon has answered it once, so it reads as a phone does."""
    page: Page = await context.new_page()
    await page.goto(ui_url("127.0.0.1", scene_.port, scene_.token))
    await expect(page.locator(".composer textarea").first).to_be_visible(timeout=15_000)
    await page.evaluate("() => import('/js/api.js').then((api) => api.api.get('/status'))")
    return page


async def test_a_read_that_is_lost_is_tried_again_three_times_in_all(resting: Scene) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            context: BrowserContext = await phone(browser, playwright, workers="block")
            page: Page = await _connected(context, resting)
            asked: list[str] = []

            async def lose_two(route: Route) -> None:
                asked.append(route.request.url)
                if len(asked) <= 2:
                    await route.abort("failed")
                else:
                    await route.continue_()

            await page.route(f"**/api/runs/{resting.run.id}", lose_two)
            await page.evaluate(f"location.hash = '#/runs/{resting.run.id}'")
            await expect(page.get_by_role("article", name="Answer").get_by_text(_ANSWER)).to_be_visible(timeout=15_000)
            # The first try and two more: the third one is answered.
            assert len(asked) == 3, asked
            await context.close()
        finally:
            await browser.close()


async def test_a_link_that_carries_nothing_as_dot_opens_gives_up_after_the_time_a_read_has(resting: Scene) -> None:
    """The proof Dot gives before the first read, and that read, are made before Dot has answered the page once. On a
    phone they give up after 10 seconds like every read, where they waited for the browser's own timeout and the page
    for them: the screen of a Dot out of reach, which tries again by itself, is there in a little more than 10."""
    async with async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            context: BrowserContext = await phone(browser, playwright, workers="block")
            held: list[str] = []

            async def carry_nothing(route: Route) -> None:
                held.append(route.request.url)

            await context.route("**/api/**", carry_nothing)
            page: Page = await context.new_page()
            await page.goto(ui_url("127.0.0.1", resting.port, resting.token))
            await expect(page.get_by_text("This page tries again by itself")).to_be_visible(timeout=15_000)
            assert held, "the page asked Dot for nothing"
            await context.close()
        finally:
            await browser.close()


async def test_a_read_that_is_refused_is_asked_once(resting: Scene) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            context: BrowserContext = await phone(browser, playwright, workers="block")
            page: Page = await _connected(context, resting)
            asked: list[str] = []

            async def refuse(route: Route) -> None:
                asked.append(route.request.url)
                await route.fulfill(status=404, json={"message": "there is no such run", "context": {}})

            await page.route("**/api/runs/run_none", refuse)
            failed: object = await page.evaluate(
                "() => import('/js/api.js').then("
                "(m) => m.api.get('/runs/run_none').then(() => 'answered', (e) => e.status))"
            )
            assert failed == 404
            assert len(asked) == 1, asked
            await context.close()
        finally:
            await browser.close()


async def test_no_try_is_spent_while_the_phone_has_no_link_and_the_read_goes_once_when_it_is_back(
    resting: Scene,
) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            channel=resting.runtime.config.browser.channel, headless=True
        )
        try:
            context: BrowserContext = await phone(browser, playwright, workers="block")
            page: Page = await _connected(context, resting)
            asked: list[str] = []

            def note(request: Request) -> None:
                if request.url.endswith(f"/api/runs/{resting.run.id}"):
                    asked.append(request.url)

            page.on("request", note)
            await context.set_offline(True)
            await page.evaluate(f"location.hash = '#/runs/{resting.run.id}'")
            await asyncio.sleep(3.0)
            # Nothing was asked of a daemon that cannot be reached, and nothing says the read failed.
            assert asked == [], asked
            assert await page.get_by_role("article", name="Answer").count() == 0
            await context.set_offline(False)
            await expect(page.get_by_role("article", name="Answer").get_by_text(_ANSWER)).to_be_visible(timeout=15_000)
            assert len(asked) == 1, asked
            await context.close()
        finally:
            await browser.close()


async def test_the_same_read_asked_twice_is_one_request_and_a_read_is_cancelled_when_its_page_leaves(
    resting: Scene,
) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            context: BrowserContext = await phone(browser, playwright, workers="block")
            page: Page = await _connected(context, resting)
            held: list[Route] = []
            arrived: asyncio.Event = asyncio.Event()

            async def hold(route: Route) -> None:
                held.append(route)
                arrived.set()

            await page.route("**/api/agents", hold)
            # Two callers, one request; each has an answer of its own to change.
            await page.evaluate(
                """() => {
                  window.__both = import('/js/api.js').then(
                    ({ api }) => Promise.all([api.get('/agents'), api.get('/agents')]));
                }"""
            )
            async with asyncio.timeout(10):
                await arrived.wait()
            await asyncio.sleep(0.5)
            assert len(held) == 1, f"{len(held)} requests for one read"
            await held[0].continue_()
            copies: object = await page.evaluate(
                "() => window.__both.then(([a, b]) => [Array.isArray(a), Array.isArray(b), a !== b])"
            )
            assert copies == [True, True, True], copies

            # A read whose caller leaves is cancelled: the request is dropped, not left to run on.
            held.clear()
            arrived.clear()
            dropped: list[str] = []
            page.on("requestfailed", lambda request: dropped.append(request.url))
            await page.evaluate(
                """() => import('/js/api.js').then(({ api }) => {
                  window.__leave = new AbortController();
                  window.__read = api.get('/agents', { signal: window.__leave.signal })
                    .then(() => 'answered', (e) => e.name);
                })"""
            )
            async with asyncio.timeout(10):
                await arrived.wait()
            assert len(held) == 1
            await page.evaluate("() => window.__leave.abort()")
            assert await page.evaluate("() => window.__read") == "AbortError"
            await asyncio.sleep(0.5)
            assert any(url.endswith("/api/agents") for url in dropped), dropped
            await context.close()
        finally:
            await browser.close()


async def test_a_chat_that_was_away_through_a_long_run_reads_the_end_of_it_and_says_that_steps_are_not_shown(
    login_env: dict[str, str],
) -> None:
    async with scene(login_env, "work") as working, async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            context: BrowserContext = await phone(browser, playwright, workers="block")
            asked: list[str] = []
            page: Page = await open_chat(context, working.address, working.token, working.run.id)
            await expect(page.locator(".capsule")).to_contain_text(re.compile(r"Working|Waiting on"), timeout=20_000)
            await until_kept(page, chats=1, lists=2)
            await page.close()

            # The phone was away: the run went on and stored far more than 500 events.
            for number in range(700):
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
            newest: int = (await working.runtime.store.list_events(working.run.id, 0, 5000))[-1].id

            page = await context.new_page()
            streamed: asyncio.Event = asyncio.Event()

            def heard(request: Request) -> None:
                if "/api/runs/" in request.url:
                    asked.append(request.url)
                    if "/stream" in request.url:
                        streamed.set()

            page.on("request", heard)
            await page.goto(f"{working.address}/#/runs/{working.run.id}")
            note = page.locator(".steps-not-shown")
            await expect(note).to_be_visible(timeout=20_000)
            await expect(note).to_contain_text("Some steps are not shown")
            tails: list[str] = [url for url in asked if "tail=400" in url]
            assert len(tails) == 1, asked
            # The stream goes on after the last event read, so no history is read again. The page opens it once it
            # has drawn the tail it read, which on a loaded Mac comes after the note is seen.
            async with asyncio.timeout(20.0):
                await streamed.wait()
            streams: list[str] = [url for url in asked if "/stream" in url]
            assert streams and f"after={newest}" in streams[-1], (newest, streams)
            await context.close()
        finally:
            await browser.close()
