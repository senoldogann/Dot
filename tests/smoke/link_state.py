"""How a page's link to the daemon stands, for the tests of a broken and mended connection. The bar shows no pill of the
connection since item 2043: a lasting break is told by the line under the bar ("Mac asleep or unreachable", the
`.link-line`), and a live stream shows nothing, so that the page's own state (`getSnapshot().stream` of `state.js`, the
module instance the page runs) says when it is live again."""

import asyncio
import time

from playwright.async_api import Locator, Page, expect

_STREAM: str = "() => import('/js/state.js').then(({ getSnapshot }) => getSnapshot().stream)"
# How often the page's state is read while a test waits on it (Dot's CSP refuses `wait_for_function`).
_POLL_S: float = 0.1


def link_line(page: Page) -> Locator:
    """The line under the bar while the daemon cannot be reached, shown once the break lasted (item 2043)."""
    return page.locator(".link-line:not([hidden])").filter(has_text="Mac asleep or unreachable")


async def stream(page: Page) -> str:
    """How the page's stream of the daemon's lifecycle stands: "connecting", "live" or "reconnecting"."""
    state: str = await page.evaluate(_STREAM)
    return state


async def until_live(page: Page, timeout_ms: float) -> None:
    """Wait until the page's stream is live, and its line says nothing of a break."""
    deadline: float = time.monotonic() + timeout_ms / 1000
    while (state := await stream(page)) != "live":
        assert time.monotonic() < deadline, f"the page's stream is {state!r} after {timeout_ms} ms"
        await asyncio.sleep(_POLL_S)
    await expect(link_line(page)).to_have_count(0)


async def until_broken(page: Page, timeout_ms: float) -> None:
    """Wait until the page's stream is broken and its line says that the daemon cannot be reached."""
    await expect(link_line(page)).to_be_visible(timeout=timeout_ms)
    assert await stream(page) == "reconnecting"
