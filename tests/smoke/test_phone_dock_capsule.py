"""The one capsule over a phone's composer (item 1383), in WebKit as the owner's iPhone draws it, over a static page
that holds the page's own stylesheets and ``dockbar.js``: it needs no daemon. Whatever the run is doing, the capsule is
one row over the composer, its segments at least 44 px tall, none over another, none cut, the page never wider than the
phone, the way down a round button beside it and not a layer over it, and at 320 px, where the row is too narrow for
what it holds, a second line rather than a scroll. Light and dark."""

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

_NONE: dict[str, object] = {
    "status": None,
    "stopping": False,
    "reconnecting": False,
    "plan": None,
    "helpers": {"live": 0, "waitedFor": 0, "letGo": 0},
    "waits": False,
    "loops": {"count": 0, "rhythm": None, "problem": False},
    "queue": {"count": 0, "held": False, "problem": False, "sending": False},
    "running": {"count": 0, "problem": False},
}
_ALL: dict[str, object] = {
    **_NONE,
    "status": "running",
    "plan": {"done": 2, "total": 5},
    "helpers": {"live": 2, "waitedFor": 2, "letGo": 0},
    "waits": True,
    "loops": {"count": 1, "rhythm": "every 10 min", "problem": False},
    "queue": {"count": 1, "held": False, "problem": False, "sending": False},
    "running": {"count": 1, "problem": False},
}
# How the capsule can stand: what has something to say, and whether the way down is offered beside it.
_CASES: dict[str, tuple[dict[str, object], bool]] = {
    "working": ({**_NONE, "status": "running"}, False),
    "waiting": ({**_NONE, "status": "blocked"}, False),
    "reconnecting": ({**_NONE, "status": "running", "reconnecting": True}, True),
    "stopping": ({**_NONE, "status": "running", "stopping": True}, False),
    "helpers": (
        {**_NONE, "status": "running", "helpers": {"live": 2, "waitedFor": 2, "letGo": 0}, "waits": True},
        False,
    ),
    "everything": (_ALL, False),
    "everything-but-a-plan": ({**_ALL, "plan": None}, False),
    "everything-and-the-way-down": (_ALL, True),
    "waiting-and-the-rest": ({**_ALL, "status": "blocked", "plan": None, "helpers": _NONE["helpers"]}, False),
    "many-queued": ({**_ALL, "queue": {**_ALL["queue"], "count": 12}}, False),  # type: ignore[dict-item]
    "chats-own": (
        {
            **_NONE,
            "loops": {"count": 2, "rhythm": None, "problem": False},
            "queue": {"count": 3, "held": True, "problem": False, "sending": False},
            "running": {"count": 1, "problem": False},
        },
        False,
    ),
    "queue-only": ({**_NONE, "queue": {"count": 3, "held": False, "problem": False, "sending": False}}, True),
    "way-down-only": (_NONE, True),
}
_PAGE: str = """<!doctype html><html lang="en" class="chat-shell"><head>
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="dot-pages" content="">
<link rel="stylesheet" href="/css/app.css"><link rel="stylesheet" href="/css/shell.css">
<link rel="stylesheet" href="/css/run.css"><link rel="stylesheet" href="/css/settings.css">
<link rel="stylesheet" href="/css/themes.css"></head>
<body style="margin:0">
<div class="dock" style="position:relative;margin-top:24px;padding-inline:12px"><div id="slot"></div>
<div class="composer" style="height:96px;border:1px solid gray;border-radius:14px"></div></div>
<script type="module">
import { createDockBar } from "/js/dockbar.js";
import { createDockSheets } from "/js/docksheet.js";
const cases = %s;
window.paint = (name) => {
  const [state, end] = cases[name];
  const sheets = createDockSheets();
  const bar = createDockBar({ onWork() {}, onEnd() {}, sheets });
  document.getElementById("slot").replaceChildren(bar.element);
  bar.paint(state);
  bar.setEnd(end, "Jump to latest");
  sheets.dispose();
};
window.ready = true;
</script></body></html>"""

_MEASURE: str = """() => {
  const box = (e) => {
    const b = e.getBoundingClientRect();
    return { left: b.left, right: b.right, top: b.top, bottom: b.bottom, width: b.width, height: b.height };
  };
  const row = document.querySelector('.dockbar');
  const capsule = document.querySelector('.dockbar-capsule');
  const end = document.querySelector('.dock-end');
  const shown = (e) => e.getClientRects().length > 0;
  const segments = [...document.querySelectorAll('.dock-seg')].filter(shown);
  const words = [...document.querySelectorAll('.dock-long, .dock-short')].filter((e) => shown(e));
  return {
    window: innerWidth,
    pageOverflow: document.documentElement.scrollWidth - innerWidth,
    row: shown(row) ? box(row) : null,
    capsule: shown(capsule) ? box(capsule) : null,
    capsuleClipped: shown(capsule) ? capsule.scrollWidth > capsule.clientWidth + 1 : false,
    fit: capsule.dataset.fit,
    end: shown(end) ? box(end) : null,
    composer: box(document.querySelector('.composer')),
    segments: segments.map((e) => ({ id: e.dataset.segment, ...box(e), text: e.innerText.trim() })),
    cut: words.filter((e) => e.scrollWidth > e.clientWidth + 1).map((e) => e.textContent),
  };
}"""


class _Quiet(SimpleHTTPRequestHandler):
    """The page's own files, and the page that paints the capsule; nothing is logged."""

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path.split("?")[0] != "/":
            super().do_GET()
            return
        body: bytes = (_PAGE % json.dumps(_CASES)).encode()
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


def _overlap(one: dict[str, Any], other: dict[str, Any]) -> bool:
    """Whether two boxes share an area (a pixel of touching edges is none)."""
    return (
        one["left"] < other["right"] - 0.5
        and other["left"] < one["right"] - 0.5
        and (one["top"] < other["bottom"] - 0.5 and other["top"] < one["bottom"] - 0.5)
    )


@pytest.mark.parametrize("width", [320, 360, 390, 430])
async def test_the_capsule_is_one_row_of_segments_a_finger_tall_that_neither_overlap_nor_cut_nor_scroll(
    served: str, width: int, tmp_path: Path
) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch()
        try:
            for scheme in ("light", "dark"):
                page: Page = await (
                    await browser.new_context(
                        viewport={"width": width, "height": 700},
                        color_scheme=scheme,
                        reduced_motion="reduce",
                        has_touch=True,
                        is_mobile=True,
                        device_scale_factor=2,
                    )
                ).new_page()
                await page.goto(served)
                await page.wait_for_function("window.ready === true")
                for case, (_, wants_end) in _CASES.items():
                    await page.evaluate(f"paint({json.dumps(case)})")
                    seen: dict[str, Any] = await page.evaluate(_MEASURE)
                    where: str = f"{case} at {width} {scheme}: {seen}"
                    # Never wider than the phone, and nothing in the row is cut or sent sideways.
                    assert seen["pageOverflow"] <= 0, where
                    assert seen["capsuleClipped"] is False, where
                    assert seen["cut"] == [], where
                    row: dict[str, Any] = seen["row"]
                    assert row["left"] >= 0 and row["right"] <= width, where
                    # The row stands over the composer, its own gap above it, and nothing of it is over the composer.
                    assert row["bottom"] <= seen["composer"]["top"] - 4, where
                    # Every segment is a finger tall, and none is over another.
                    segments: list[dict[str, Any]] = seen["segments"]
                    assert all(segment["height"] >= 43.99 for segment in segments), where
                    for index, one in enumerate(segments):
                        for other in segments[index + 1 :]:
                            assert not _overlap(one, other), (where, one, other)
                        capsule: dict[str, Any] = seen["capsule"]
                        assert one["left"] >= capsule["left"] - 0.5 and one["right"] <= capsule["right"] + 0.5, where
                    # The way down is a round control beside the capsule, never over it.
                    if wants_end:
                        end: dict[str, Any] = seen["end"]
                        assert abs(end["width"] - end["height"]) < 1 and end["width"] >= 44, where
                        if seen["capsule"] is not None:
                            assert not _overlap(end, seen["capsule"]), where
                            assert end["left"] >= seen["capsule"]["right"] - 0.5, where
                        assert end["right"] <= width, where
                    else:
                        assert seen["end"] is None, where
                    # A row that has nothing to say is not drawn at all, and a run that simply works has nothing to
                    # say there, nor a Stop (item 2105): the work line and the composer's send say and do it.
                    if case in ("way-down-only", "working"):
                        assert seen["capsule"] is None, where
                    words: dict[str, str] = {segment["id"]: segment["text"] for segment in segments}
                    assert "stop" not in words, where
                    # The row says as much as it has room for, and a second line keeps the status in words.
                    if case.startswith("everything"):
                        assert words["status"] == "Waiting on 2 helpers", where
                    if case in ("everything", "everything-and-the-way-down", "waiting-and-the-rest", "many-queued"):
                        await page.screenshot(
                            path=str(tmp_path / f"capsule-{case}-{width}-{scheme}.png"),
                            clip={"x": 0, "y": 0, "width": width, "height": 220},
                            animations="disabled",
                        )
                await page.close()
        finally:
            await browser.close()
