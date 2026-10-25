"""What stands in a chat's place while its run is read (items 502 and 643): three dots of Dot's yellow that rise in
turn, and nothing else, shown only once the read has taken 300 ms, so a chat that opens at once flashes nothing; a
screen reader is told the chat is opening; with reduced motion the dots stand still. The page's own stylesheets and
``panels.js`` draw it in Chrome, over a static page: it needs no daemon."""

import functools
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.server.app import STATIC_DIR

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PAGE: bytes = b"""<!doctype html><html lang="en"><head>
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="dot-pages" content="">
<link rel="stylesheet" href="/css/app.css"><link rel="stylesheet" href="/css/shell.css">
<link rel="stylesheet" href="/css/run.css"><link rel="stylesheet" href="/css/themes.css"></head>
<body style="margin:0"><main id="slot"></main>
<script type="module">
import { chatLoading } from "/js/panels.js";
window.show = () => document.getElementById("slot").replaceChildren(chatLoading());
window.ready = true;
</script></body></html>"""

_LOOK: str = """() => {
  const loading = document.querySelector('.chat-loading');
  const dots = [...loading.querySelectorAll('.chat-loading-dots > span')];
  return {
    opacity: Number(getComputedStyle(loading).opacity),
    dots: dots.length,
    moving: dots.map((dot) => getComputedStyle(dot).animationName),
    words: loading.textContent.trim(),
  };
}"""


class _Quiet(SimpleHTTPRequestHandler):
    """The page's own files, and the page that draws the loader; nothing is logged."""

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path.split("?")[0] != "/":
            super().do_GET()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_PAGE)))
        self.end_headers()
        self.wfile.write(_PAGE)


@pytest.fixture(scope="module")
def served() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_Quiet, directory=str(STATIC_DIR))
    )
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


async def _shown(browser: Browser, served: str, motion: str) -> Page:
    page: Page = await browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion=motion)  # type: ignore[arg-type]
    await page.goto(served)
    await page.wait_for_function("window.ready === true")
    await page.evaluate("show()")
    return page


async def test_three_dots_rise_in_turn_once_a_moment_has_passed_and_nothing_else_is_drawn(served: str) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page: Page = await _shown(browser, served, "no-preference")
            early: dict[str, Any] = await page.evaluate(_LOOK)
            # Nothing in the first moment: a chat that opens at once flashes nothing.
            assert early["opacity"] < 0.05, early
            await expect(page.get_by_role("status", name="Opening this chat")).to_be_attached()
            await page.wait_for_function(
                "Number(getComputedStyle(document.querySelector('.chat-loading')).opacity) > 0.99"
            )
            late: dict[str, Any] = await page.evaluate(_LOOK)
            assert late["dots"] == 3 and late["moving"] == ["chat-dot"] * 3, late
            # No words, no mark, no lines of a conversation to come: the three dots alone.
            assert late["words"] == "", late
            await page.close()

            still: Page = await _shown(browser, served, "reduce")
            await still.wait_for_function(
                "Number(getComputedStyle(document.querySelector('.chat-loading')).opacity) > 0.99"
            )
            seen: dict[str, Any] = await still.evaluate(_LOOK)
            assert seen["dots"] == 3 and seen["moving"] == ["none"] * 3, seen
        finally:
            await browser.close()
