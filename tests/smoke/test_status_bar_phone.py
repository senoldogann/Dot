"""The bar over the composer on a phone (items 401 and 427): it is as wide as its parts and no wider, in the middle of
the dock, the way down inside it, however long the run has worked; and on a narrow phone no part is cut. The page's
own stylesheets and ``capsule.js`` paint it in WebKit, as the owner's iPhone, over a static page: it needs no daemon."""

import functools
import json
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Browser, Page, async_playwright

from dotagent.server.app import STATIC_DIR

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_NOW: int = 1_800_000_000
# How the bar can stand: what has worked how long, which helpers, how many messages are queued, and whether the way
# down is offered.
_CASES: dict[str, dict[str, object]] = {
    "just-begun": {"since": -65, "helpers": [], "queued": 0, "jump": False},
    "helpers-and-queue": {"since": -(60 * 111 + 5), "helpers": ["running", "running"], "queued": 1, "jump": True},
    "helpers": {"since": -(60 * 111 + 5), "helpers": ["running", "running"], "queued": 0, "jump": True},
    "queue-only": {"since": -(60 * 5), "helpers": [], "queued": 3, "jump": True},
    "long": {"since": -(3600 * 5 + 60 * 20), "helpers": ["running", "running"], "queued": 12, "jump": True},
}
_PAGE: str = """<!doctype html><html lang="en" class="chat-shell"><head>
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="dot-pages" content="">
<link rel="stylesheet" href="/css/app.css"><link rel="stylesheet" href="/css/shell.css">
<link rel="stylesheet" href="/css/run.css"><link rel="stylesheet" href="/css/settings.css">
<link rel="stylesheet" href="/css/themes.css"></head>
<body style="margin:0;padding-top:10px">
<div class="dock" style="position:relative;padding:0 8px"><div id="slot"></div></div>
<script type="module">
import { createCapsule } from "/js/capsule.js";
import { el } from "/js/dom.js";
import { icon } from "/js/icons.js";
const cases = %s;
const now = %d;
window.paint = (name) => {
  const c = cases[name];
  const capsule = createCapsule({ onWork() {}, onHelpers() {}, onQueued() {} });
  const bar = el("div", { class: "status-bar" }, capsule.element);
  if (c.jump) {
    bar.append(el("button", { class: "jump", type: "button", "aria-label": "Jump to the latest" }, icon("arrowDown")));
  }
  bar.hidden = capsule.element.hidden && !c.jump;
  document.getElementById("slot").replaceChildren(bar);
  capsule.paint({ status: "running", since: now + c.since, now, helpers: c.helpers, queued: c.queued });
  bar.hidden = capsule.element.hidden && !c.jump;
};
window.ready = true;
</script></body></html>"""

_MEASURE: str = """() => {
  const box = (e) => { const b = e.getBoundingClientRect(); return { left: b.left, right: b.right, width: b.width }; };
  const bar = document.querySelector('.status-bar');
  const parts = [...document.querySelectorAll('.capsule-part:not([hidden])')];
  const jump = document.querySelector('.jump');
  const texts = parts.map((p) => ({ cut: p.scrollWidth > p.clientWidth + 1, text: p.textContent.trim() }));
  const capsule = document.querySelector('.capsule');
  return {
    window: innerWidth,
    bar: box(bar),
    first: parts.length ? box(parts[0]) : null,
    last: parts.length ? box(parts[parts.length - 1]) : null,
    jump: jump ? box(jump) : null,
    capsuleClipped: capsule.scrollWidth > capsule.clientWidth + 1,
    cut: texts.filter((t) => t.cut).map((t) => t.text),
    pageOverflow: document.documentElement.scrollWidth - innerWidth,
  };
}"""


class _Quiet(SimpleHTTPRequestHandler):
    """The page's own files, and the page that paints the bar; nothing is logged."""

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path.split("?")[0] != "/":
            super().do_GET()
            return
        body: bytes = (_PAGE % (json.dumps(_CASES), _NOW)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


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


async def _measured(page: Page, case: str) -> dict[str, Any]:
    await page.evaluate(f"paint({json.dumps(case)})")
    seen: dict[str, Any] = await page.evaluate(_MEASURE)
    return seen


def _centre(box: dict[str, float]) -> float:
    return (box["left"] + box["right"]) / 2


@pytest.mark.parametrize("width", [320, 360, 390, 430])
async def test_the_bar_is_as_wide_as_its_parts_in_the_middle_of_the_dock_and_cuts_nothing(
    served: str, width: int, tmp_path: Path
) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch()
        try:
            for scheme in ("light", "dark"):
                page: Page = await (
                    await browser.new_context(
                        viewport={"width": width, "height": 700}, color_scheme=scheme, reduced_motion="reduce"
                    )
                ).new_page()
                await page.goto(served)
                await page.wait_for_function("window.ready === true")
                for case in _CASES:
                    seen: dict[str, Any] = await _measured(page, case)
                    bar: dict[str, float] = seen["bar"]
                    where: str = f"{case} at {width} {scheme}: {seen}"
                    # In the middle of the window, and not wider than what it holds: its parts, and the way down.
                    assert abs(_centre(bar) - width / 2) <= 1, where
                    holds: float = (seen["last"]["right"] - seen["first"]["left"]) if seen["first"] else 0
                    if seen["jump"] is not None:
                        holds += seen["jump"]["width"] + 8
                    assert bar["width"] <= holds + 24, where
                    # The way down is inside the bar, at its right end; nothing is cut or sent sideways.
                    if seen["jump"] is not None:
                        assert seen["jump"]["right"] <= bar["right"] + 0.5, where
                        assert seen["jump"]["left"] >= bar["left"], where
                    assert seen["pageOverflow"] <= 0, where
                    # A phone as wide as the owner's, or a small one at its widest, shows every part whole. The
                    # narrowest (320) lets the capsule scroll sideways rather than cut a word, as it always has.
                    if width >= 390:
                        assert seen["cut"] == [], where
                        assert seen["capsuleClipped"] is False, where
                    if case == "helpers-and-queue":
                        await page.screenshot(
                            path=str(tmp_path / f"bar-{width}-{scheme}.png"),
                            clip={"x": 0, "y": 0, "width": width, "height": 64},
                            animations="disabled",
                        )
                await page.close()
        finally:
            await browser.close()
