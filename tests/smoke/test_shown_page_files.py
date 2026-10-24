"""A page Dot made in its workspace, shown with the files beside it (items 600 and 612), in Chrome under the policy of a
shown page (``PAGE_HEADERS``: sandboxed, with scripts, pictures from ``data:`` addresses, and no frame, object or fetch
of another file). The picture each tag of a long table takes from the one copy in the page, the picture its ``srcset``
chooses on a screen of two pixels to the point and on one of one, the one of a ``picture``, and the icon of the page
all load; a frame is left as written and named, since the policy does not let it load.

No network and no model service is used: the page is made in a folder of the test and handed to the browser by the test.
"""

import struct
import zlib
from pathlib import Path

import pytest
from playwright.async_api import Browser, Page, Route, async_playwright

from dotagent.config import load_default_config
from dotagent.server.guard import PAGE_HEADERS
from dotagent.tools.page_files import PageFiles, with_its_files

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

_ROWS: int = 1000
# How each picture is drawn: its width in pixels, which says which file the browser chose.
_CHECK: int = 8
_SMALL: int = 4
_BIG: int = 10
_WIDTHS: str = """() => ({
  rows: [...document.querySelectorAll("table img")].map((image) => image.complete && image.naturalWidth),
  chosen: document.getElementById("chosen").naturalWidth,
  picture: document.getElementById("picture").naturalWidth,
  icon: document.querySelector("link[rel=icon]").href.slice(0, 22),
  stored: document.querySelectorAll("img[data-dot-pic]").length,
})"""


def _png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A picture of one colour, in the format a browser reads."""
    raw: bytes = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body: bytes = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header: bytes = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _page(tmp_path: Path) -> PageFiles:
    """The page of a long table with one icon in each row, a picture with a ``srcset``, a ``picture`` and a frame, with
    the files beside it written in."""
    workspace: Path = tmp_path.resolve() / "workspace"
    folder: Path = workspace / "site"
    folder.mkdir(parents=True)
    for name, size, colour in (
        ("check.png", _CHECK, (0, 160, 0)),
        ("small.png", _SMALL, (200, 0, 0)),
        ("big.png", _BIG, (0, 0, 200)),
        ("icon.png", 2, (0, 0, 0)),
    ):
        (folder / name).write_bytes(_png(size, size, colour))
    rows: str = "".join(
        f'<tr><td>row {number}</td><td><img src="check.png" alt="ok"></td></tr>' for number in range(_ROWS)
    )
    html: str = (
        '<!doctype html><html><head><title>Rows</title><link rel="icon" href="icon.png"></head><body>'
        '<img id="chosen" src="small.png" srcset="small.png 1x, big.png 2x" alt="a">'
        '<picture><source srcset="big.png"><img id="picture" src="small.png" alt="b"></picture>'
        f'<iframe id="frame" src="check.png"></iframe><table>{rows}</table></body></html>'
    )
    page: Path = folder / "index.html"
    page.write_text(html, encoding="utf-8")
    return with_its_files(html, page, workspace, (), ())


async def _shown(browser: Browser, files: PageFiles, scale: int) -> tuple[Page, list[str]]:
    """The page opened as the daemon serves a shown page, with the headers of one: the errors of its scripts come back
    with it."""
    page: Page = await browser.new_page(device_scale_factor=scale)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    async def serve(route: Route) -> None:
        headers: dict[str, str] = {**dict(PAGE_HEADERS), "Content-Type": "text/html; charset=utf-8"}
        await route.fulfill(status=200, body=files.html, headers=headers)

    await page.route("http://pages.test/page", serve)
    await page.goto("http://pages.test/page")
    await page.wait_for_load_state("load")
    return page, errors


async def test_the_files_a_page_names_load_in_a_shown_page_and_the_one_picture_of_a_long_table_is_in_every_row(
    tmp_path: Path,
) -> None:
    files: PageFiles = _page(tmp_path)
    # What the answer says: the files are written in, the table's icon once, and the frame is named and not written in.
    assert files.left_out == [] and files.too_large == [] and files.framed == ["check.png"]
    assert files.html.count('<img data-dot-pic="1" alt="ok">') == _ROWS and len(files.html) < 100_000
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            channel=load_default_config().browser.channel, headless=True
        )
        try:
            for scale in (1, 2):
                page, errors = await _shown(browser, files, scale)
                found = await page.evaluate(_WIDTHS)
                # Every row has its picture: the script of the page gave it, under a policy that allows scripts and
                # data: pictures and nothing else.
                assert found["rows"] == [_CHECK] * _ROWS, scale
                assert found["stored"] == _ROWS and errors == [], (scale, errors)
                # A picture of a srcset loads: one of its two candidates, drawn in CSS pixels (the one of 2x is half as
                # wide as it is). Chrome takes the last of candidates that are data: addresses, whatever the screen, so
                # that the test asks that one loads and not which. Its source in a picture element is chosen too.
                assert found["chosen"] in (_SMALL, _BIG // 2), (scale, found["chosen"])
                assert found["picture"] == _BIG, scale
                assert found["icon"] == "data:image/png;base64,", scale
                await page.close()
        finally:
            await browser.close()
