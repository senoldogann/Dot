"""A stream that is never silent without being noticed (item 1522): the page reads a run's stream with ``fetch`` and
sees the daemon's keepalive, so a connection that stays open and passes nothing is dropped once it has said nothing for
35 seconds and made again after the last event it read (``Last-Event-ID`` and ``after``), and no history is read again.
A real daemon in a throw-away home, a TCP stand-in for the phone's network that leaves connections open and silent, and
the page's clock moved on in WebKit as the owner's iPhone draws it."""

import pytest
import pytest_asyncio
from dock_scenario import read_login, scene
from playwright.async_api import Browser, BrowserContext, Page, Request, async_playwright, expect
from pwa_scene import Network, phone

from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login()


async def test_a_connection_left_open_and_silent_is_dropped_after_35_seconds_and_goes_on_from_the_last_event(
    login_env: dict[str, str],
) -> None:
    network: Network = Network(0)
    await network.start()
    async with scene(login_env, "work", advertise=network.port) as working, async_playwright() as playwright:
        network.target = working.port
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            context: BrowserContext = await phone(browser, playwright, workers="block")
            page: Page = await context.new_page()
            await page.clock.install()
            streams: list[Request] = []
            page.on(
                "request",
                lambda request: (
                    streams.append(request) if "/runs/" in request.url and "/stream" in request.url else None
                ),
            )
            await page.goto(ui_url("127.0.0.1", network.port, working.token))
            await page.goto(f"{network.address}/#/runs/{working.run.id}")
            await expect(page.locator(".capsule")).to_contain_text("Waiting on 2 helpers", timeout=20_000)
            await page.wait_for_timeout(1500)
            # The run's own stream is open after the events the page read; it is made one that passes nothing, as a
            # connection does that a phone's network left behind.
            live: list[Request] = [r for r in streams if "stored=true" not in r.url]
            assert len(live) == 1, [r.url for r in streams]
            last: int = int(live[0].url.rpartition("after=")[2].split("&")[0])
            assert last > 0
            assert live[0].headers.get("last-event-id") == str(last)
            network.forget()
            # 34 seconds of silence is not a dead stream; 36 is.
            await page.clock.fast_forward(34_000)
            await page.wait_for_timeout(500)
            assert len([r for r in streams if "stored=true" not in r.url]) == 1
            await page.clock.fast_forward(2_000)
            # The stream is made again after the wait of a first break (half a second on the page's clock).
            for _ in range(20):
                if len([r for r in streams if "stored=true" not in r.url]) >= 2:
                    break
                await page.clock.run_for(1_000)
                await page.wait_for_timeout(300)
            again: list[Request] = [r for r in streams if "stored=true" not in r.url]
            assert len(again) >= 2, [r.url for r in streams]
            # It goes on from the last event it read: the same id in the address and in the header, no history again.
            assert f"after={last}" in again[1].url, again[1].url
            assert again[1].headers.get("last-event-id") == str(last), again[1].headers
            assert not [r for r in streams[len(streams) - len(again) + 1 :] if "after=0" in r.url]
            await context.close()
        finally:
            await browser.close()
            network.close()
