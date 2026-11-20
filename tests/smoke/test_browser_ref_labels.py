"""The screenshot a model is sent marks each interactive element in the viewport with its ref from the current aria
snapshot, on that element's box. An element outside the viewport is left unmarked, and so is one that is not a
control. The picture with no marks stays available: it is what anything other than the model is shown.

The page is local. The snapshot and the JPEG are taken the way a tab's screenshot is (``capture_page``,
``screenshot_jpeg``), and the marks are the ones ``BrowserSession.screenshot`` draws for the model.
"""

import io
import sys
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image
from playwright.async_api import Page, async_playwright

from dotagent.browser.driver import capture_page, screenshot_jpeg
from dotagent.browser.labels import MARK_CAP, mark_screenshot
from dotagent.browser.snapshot import SnapshotLine
from dotagent.core.images import jpeg_of_size

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

_VIEWPORT: tuple[int, int] = (800, 600)
_QUALITY: int = 60
# The ink of a ref mark, as JPEG quality 60 still leaves it: a light-blue control is not this.
_MARK_RED: int = 200
_MARK_GREEN: tuple[int, int] = (40, 160)
# A mark smaller than this is JPEG noise, not a badge.
_MIN_MARK_PIXELS: int = 24
# How far a badge's top-left may sit from the element's own top-left.
_PLACE_PX: int = 8

_PAGE: str = """<!doctype html><title>Labels</title>
<style>
  body { margin: 0; background: #fff; }
  h1 { margin: 16px; font: 20px sans-serif; color: #222; }
  button, a { display: block; width: 180px; height: 44px; margin: 20px; background: #d6e6f5; color: #123; }
  #later { position: absolute; top: 900px; left: 20px; }
  iframe { display: block; margin: 20px; border: 0; width: 200px; height: 60px; }
</style>
<h1 id="title">Plain heading</h1>
<button id="save">Save</button>
<a id="next" href="/next">Next</a>
<iframe id="frame" src="/frame"></iframe>
<button id="later">Later</button>
"""
_FRAME: str = (
    "<!doctype html><style>body{margin:0}</style>"
    "<button id='inframe' style='width:160px;height:40px;background:#d6e6f5'>Inside</button>"
)
# A control the model can act on. A heading is read, not acted on, and is not marked.
_INTERACTIVE: frozenset[str] = frozenset({"save", "next", "inside", "later"})


def _serve() -> Iterator[str]:
    pages: dict[str, str] = {"/": _PAGE, "/frame": _FRAME}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body: bytes = pages.get(self.path.split("?", 1)[0], "").encode()
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), Handler)

    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    yield from _serve()


@pytest_asyncio.fixture
async def page(site: str) -> AsyncIterator[Page]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True, args=["--no-sandbox"])
        try:
            opened: Page = await browser.new_page(viewport={"width": _VIEWPORT[0], "height": _VIEWPORT[1]})
            await opened.goto(site)
            yield opened
        finally:
            await browser.close()


def _is_mark(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return red >= _MARK_RED and _MARK_GREEN[0] <= green <= _MARK_GREEN[1] and blue <= 70 and red > green + 50


def _marks(data: bytes) -> list[tuple[int, int, int, int]]:
    """The bounding box of each ref badge in ``data``: left, top, right, bottom."""
    image: Image.Image = Image.open(io.BytesIO(data)).convert("RGB")
    width, height = image.size
    pixels = image.load()
    assert pixels is not None
    remaining: set[tuple[int, int]] = set()
    for y in range(height):
        for x in range(width):
            red, green, blue = pixels[x, y]
            if _is_mark((int(red), int(green), int(blue))):
                remaining.add((x, y))
    found: list[tuple[int, int, int, int]] = []
    while remaining:
        start: tuple[int, int] = remaining.pop()
        stack: list[tuple[int, int]] = [start]
        members: list[tuple[int, int]] = [start]
        while stack:
            x, y = stack.pop()
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    nxt: tuple[int, int] = (x + dx, y + dy)
                    if nxt in remaining:
                        remaining.remove(nxt)
                        stack.append(nxt)
                        members.append(nxt)
        if len(members) < _MIN_MARK_PIXELS:
            continue
        xs: list[int] = [point[0] for point in members]
        ys: list[int] = [point[1] for point in members]
        found.append((min(xs), min(ys), max(xs), max(ys)))
    return found


def _intersects(box: dict[str, float], width: int, height: int) -> bool:
    return box["x"] < width and box["x"] + box["w"] > 0 and box["y"] < height and box["y"] + box["h"] > 0


def _on_box(mark: tuple[int, int, int, int], box: dict[str, float]) -> bool:
    """Whether ``mark`` is the badge drawn at ``box``'s top-left."""
    return abs(mark[0] - box["x"]) <= _PLACE_PX and abs(mark[1] - box["y"]) <= _PLACE_PX


async def _boxes(page: Page) -> dict[str, dict[str, float]]:
    raw: dict[str, dict[str, float]] = await page.evaluate(
        """() => {
          const frame = document.getElementById('frame');
          const fr = frame.getBoundingClientRect();
          const inner = frame.contentDocument.getElementById('inframe').getBoundingClientRect();
          const one = (id) => {
            const r = document.getElementById(id).getBoundingClientRect();
            return {x: r.x, y: r.y, w: r.width, h: r.height};
          };
          return {
            title: one('title'), save: one('save'), next: one('next'), later: one('later'),
            inside: {x: fr.x + inner.x, y: fr.y + inner.y, w: inner.width, h: inner.height},
          };
        }"""
    )
    return raw


def _line(lines: tuple[SnapshotLine, ...], role: str, name: str) -> SnapshotLine:
    found: list[SnapshotLine] = [line for line in lines if line.role == role and line.name == name and line.ref]
    assert len(found) == 1, (role, name, [(line.role, line.name, line.ref) for line in lines])
    return found[0]


async def _shot(page: Page) -> tuple[bytes, tuple[SnapshotLine, ...]]:
    """The raw viewport JPEG and the snapshot lines a tab keeps, at the production quality."""
    lines: tuple[SnapshotLine, ...] = (await capture_page(page, 8000, True)).lines
    raw: bytes = await screenshot_jpeg(page, _QUALITY, 8000, True)
    fitted: bytes = jpeg_of_size(raw, _VIEWPORT[0], _VIEWPORT[1], _QUALITY)
    return fitted, lines


async def test_the_screenshot_marks_each_visible_ref_on_its_element(page: Page) -> None:
    """Save, Next and the button inside the frame are marked on their boxes with the snapshot's refs. The heading is
    not a control, and Later is below the viewport, so neither is marked. The JPEG that was taken is unmarked."""
    raw, lines = await _shot(page)
    boxes: dict[str, dict[str, float]] = await _boxes(page)
    visible: set[str] = {name for name in _INTERACTIVE if _intersects(boxes[name], *_VIEWPORT)}
    assert visible == {"save", "next", "inside"}, boxes
    refs: dict[str, str] = {
        "save": _line(lines, "button", "Save").ref or "",
        "next": _line(lines, "link", "Next").ref or "",
        "inside": _line(lines, "button", "Inside").ref or "",
    }
    assert refs["inside"].startswith("f"), refs
    marked: bytes = mark_screenshot(raw, lines, _VIEWPORT[0], _VIEWPORT[1], _QUALITY)
    assert marked, "the model is sent the page with no ref marks"
    assert not _marks(raw), "the raw screenshot carries ref marks"
    badges: list[tuple[int, int, int, int]] = _marks(marked)
    owned: dict[str, tuple[int, int, int, int]] = {}
    for badge in badges:
        hits: list[str] = [name for name, box in boxes.items() if _on_box(badge, box)]
        assert len(hits) == 1, (badge, boxes)
        owned[hits[0]] = badge
    assert set(owned) == visible, (owned, visible, refs)
    assert len(badges) == len(visible)


@pytest.fixture(scope="module")
def crowd_page() -> str:
    buttons: str = "".join(f"<button>B{number:02d}</button>" for number in range(1, MARK_CAP + 9))
    return (
        "<!doctype html><title>Crowd</title><style>"
        "body{margin:0;display:grid;grid-template-columns:repeat(6,90px);gap:6px;background:#fff}"
        "button{width:90px;height:36px;background:#d6e6f5;color:#123}</style>" + buttons
    )


async def test_a_crowded_viewport_marks_only_the_first_visible_refs(crowd_page: str) -> None:
    """Past the cap, the marks are the first interactive refs in the snapshot that sit in the viewport, and no more."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True, args=["--no-sandbox"])
        try:
            page: Page = await browser.new_page(viewport={"width": _VIEWPORT[0], "height": _VIEWPORT[1]})
            await page.set_content(crowd_page)
            raw, lines = await _shot(page)
            boxes: list[dict[str, float]] = await page.evaluate(
                """() => [...document.querySelectorAll('button')].map((el) => {
                  const r = el.getBoundingClientRect();
                  return {name: el.textContent, x: r.x, y: r.y, w: r.width, h: r.height};
                })"""
            )
        finally:
            await browser.close()
    by_name: dict[str, dict[str, float]] = {str(box["name"]): box for box in boxes}
    visible: list[SnapshotLine] = [
        line
        for line in lines
        if line.role == "button" and line.ref and line.name in by_name and _intersects(by_name[line.name], *_VIEWPORT)
    ]
    assert len(visible) > MARK_CAP, len(visible)
    marked: bytes = mark_screenshot(raw, lines, _VIEWPORT[0], _VIEWPORT[1], _QUALITY)
    badges: list[tuple[int, int, int, int]] = _marks(marked)
    assert len(badges) == MARK_CAP, (len(badges), MARK_CAP)
    kept: set[str] = set()
    for badge in badges:
        hits: list[str] = [line.name for line in visible if _on_box(badge, by_name[line.name])]
        assert len(hits) == 1, (badge, hits)
        kept.add(hits[0])
    assert kept == {line.name for line in visible[:MARK_CAP]}
    assert not _marks(raw)


def _headless_browser(directory: Path):
    from dotagent.config import load_config

    settings: Path = directory / "config.toml"
    settings.write_text("[browser]\nheadless = true\n", encoding="utf-8")
    return load_config(settings).browser


@pytest.mark.skipif(sys.platform != "darwin", reason="Dot's browser session drives Chrome through macOS")
async def test_the_session_sends_the_marks_and_keeps_the_raw_shot(site: str, tmp_path: Path) -> None:
    """The model picture is marked, and ``data`` is the page with no marks."""
    from dotagent.browser.session import BrowserSession, no_runs_at_work
    from dotagent.browser.types import Screenshot
    from dotagent.config import BrowserConfig, ServerConfig, load_default_config

    config: BrowserConfig = _headless_browser(tmp_path)
    server: ServerConfig = load_default_config().server
    session: BrowserSession = BrowserSession(
        config, server, tmp_path / "profile", tmp_path / "downloads", no_runs_at_work
    )
    try:
        await session.open_url("marks", f"{site}/", True)
        shot: Screenshot = await session.screenshot("marks", True)
    finally:
        await session.aclose()
    assert shot.width == config.viewport_width and shot.height == config.viewport_height
    assert not _marks(shot.data), "the screenshot kept for the owner carries ref marks"
    assert shot.marked, "the model is sent the page with no ref marks"
    badges: list[tuple[int, int, int, int]] = _marks(shot.marked)
    assert len(badges) == 3, badges
