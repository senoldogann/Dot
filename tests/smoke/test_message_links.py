"""The links of a message as plain words (item 1346).

A link in an answer or in the owner's message has no border, no fill and no icon, is no longer the text's own
colour, is underlined only under the pointer or with the focus, and keeps the focus ring; no link in the panel or the
messages is drawn as a chip. Served by a real server over runs seeded in a real store, in Chrome and in WebKit.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from panel_seed import LOOKS, NONE, computed, open_page, runtime_in, seed, serve
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.runtime import Runtime
from dotagent.store.models import RunRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    async for built in runtime_in(tmp_path_factory):
        yield built


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def address(runtime: Runtime) -> AsyncIterator[str]:
    async for served in serve(runtime):
        yield served


async def test_a_link_in_a_message_has_no_chip_reads_a_shade_brighter_and_is_underlined_only_under_the_pointer(
    runtime: Runtime, address: str
) -> None:
    run: RunRecord = await seed(runtime)
    async with async_playwright() as playwright:
        for engine, width, height, scheme in LOOKS:
            browser: Browser = await getattr(playwright, engine).launch(headless=True)
            try:
                page: Page = await open_page(browser, address, run.id, width, height, scheme, False)
                links: Locator = page.locator(".prose a.msg-link")
                # The owner's two and the answer's three, markdown links and autolinks.
                await expect(links).to_have_count(5)
                assert await page.locator(".link-pill, .link-pill-icon, .link-pill-fallback").count() == 0
                assert await page.locator(".prose a img, .prose a svg").count() == 0
                body: str = (await computed(page.locator(".bubble.say > .prose, .answer > .prose").first, "color"))[
                    "color"
                ]
                for index in range(5):
                    style: dict[str, str] = await computed(
                        links.nth(index),
                        "border-top-width",
                        "border-bottom-width",
                        "border-left-width",
                        "border-right-width",
                        "background-color",
                        "background-image",
                        "box-shadow",
                        "padding-left",
                        "display",
                        "text-decoration-line",
                        "color",
                    )
                    widths: list[str] = [style[f"border-{side}-width"] for side in ("top", "bottom", "left", "right")]
                    assert widths == ["0px"] * 4, style
                    assert style["background-color"] == NONE and style["background-image"] == "none", style
                    assert style["box-shadow"] == "none" and style["padding-left"] == "0px", style
                    assert style["display"] == "inline", style
                    assert style["text-decoration-line"] == "none", style
                    assert style["color"] != body, (style["color"], body)
                # The pointer underlines it, and leaving it takes the line back.
                await links.nth(0).hover()
                assert (await computed(links.nth(0), "text-decoration-line"))["text-decoration-line"] == "underline"
                await page.mouse.move(2, 2)
                assert (await computed(links.nth(0), "text-decoration-line"))["text-decoration-line"] == "none"
                # The keyboard's focus is drawn (Safari's own keyboard skips links by default): the ring and the line.
                if engine == "chromium":
                    await links.nth(0).focus()
                    await page.keyboard.press("Shift+Tab")
                    await page.keyboard.press("Tab")
                    ring: dict[str, str] = await computed(
                        links.nth(0), "outline-style", "outline-width", "text-decoration-line"
                    )
                    assert ring["outline-style"] == "solid" and ring["outline-width"] == "2px", ring
                    assert ring["text-decoration-line"] == "underline", ring
                # It opens in a new window and says where; no icon of a site is fetched for it.
                assert await links.nth(0).get_attribute("target") == "_blank"
                assert "noopener" in (await links.nth(0).get_attribute("rel") or "")
                assert await links.nth(0).get_attribute("title") == "https://example.com/notes"
                # The owner's two come first (an autolink, which names its site, and a markdown link), then Dot's.
                await expect(links).to_have_text(
                    ["example.com", "the board", "#1001 on GitHub", "github.com", "the thesis guide"]
                )
                # What else is a link in the messages and in the panel wears no frame and no fill at rest.
                await page.get_by_role("button", name="Task details").click()
                await expect(page.locator(".inspector")).to_be_visible()
                chips: list[str] = await page.evaluate(
                    """() => [...document.querySelectorAll('.prose a, .inspector a, .timeline a')]
                        .filter((node) => !node.matches('.btn') && node.offsetParent !== null)
                        .map((node) => [node, getComputedStyle(node)])
                        .filter(([, s]) => parseFloat(s.borderTopWidth) > 0 || s.backgroundColor !== 'rgba(0, 0, 0, 0)')
                        .map(([node]) => node.className || node.outerHTML.slice(0, 60))"""
                )
                assert chips == [], chips
                await page.close()
            finally:
                await browser.close()
