"""A page too heavy for the snapshot gives a snapshot of a part of it, in a real headless Chrome (item 743).

The page is a navigation and a list of thousands of items; the snapshot of the whole is made to time out as Playwright's
does on Slack or LinkedIn, and the part is read for real: the script that names it, the locator, and the refs that
work afterwards.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from dotagent.browser.driver import capture_page
from dotagent.browser.login import CHROME_APP
from dotagent.browser.snapshot import render_lines

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_ITEMS: int = 4000
_PAGE: str = (
    "<!doctype html><title>Heavy</title><nav><a href='#home'>Home</a></nav><main><ul>"
    + "".join(f"<li><a href='#i{n}'>Item {n}</a></li>" for n in range(_ITEMS))
    + "</ul></main>"
)


@pytest_asyncio.fixture(loop_scope="module")
async def page() -> AsyncIterator[Page]:
    executable = CHROME_APP / "Contents" / "MacOS" / "Google Chrome"
    if not executable.exists():
        pytest.skip("Google Chrome is not installed")
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(executable_path=str(executable), headless=True)
        opened: Page = await browser.new_page(viewport={"width": 1000, "height": 700})
        await opened.set_content(_PAGE)
        yield opened
        await browser.close()


async def test_a_page_whose_whole_snapshot_times_out_gives_a_part_with_refs_that_work(page: Page) -> None:
    whole = page.aria_snapshot
    wholes: list[int] = []

    async def heavy(**options: float) -> str:
        wholes.append(1)
        raise PlaywrightTimeoutError("Page.aria_snapshot: Timeout 8000ms exceeded.")

    page.aria_snapshot = heavy  # type: ignore[method-assign,assignment]
    try:
        read = await capture_page(page, 8000, True)
    finally:
        page.aria_snapshot = whole  # type: ignore[method-assign]
    assert wholes == [1]
    assert read.partial.startswith("Partial view: the page was too heavy to read whole in 8 s")
    text: str = render_lines(read.lines)
    assert "Item" in text and len(read.lines) < _ITEMS
    # The refs of the part are the ones the page answers to.
    ref: str = next(line.ref for line in read.lines if line.ref is not None and "Item" in line.text)
    assert await page.locator(f"aria-ref={ref}").count() == 1
    # A page the snapshot can read is read whole, with no note.
    assert (await capture_page(page, 60_000, True)).partial == ""
