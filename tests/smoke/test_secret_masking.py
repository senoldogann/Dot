"""The values of password, one-time-code and card fields stay out of what a run reads (item 467), in a real headless
Chrome against local pages: the snapshot, a search, the changes view after a value changed, the text and the links of
the page, and a screenshot, a field of another origin's frame included; an ordinary field and a page of sixty filled
fields keep their values; and yolo, which hides nothing, sees them all."""

import io
import json
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image

from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import FindResult, PageView, Screenshot, ScriptRun
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

SERVER: ServerConfig = load_default_config().server
_SECRETS: tuple[str, ...] = ("hunter2", "123456", "4242424242424242", "5555555555554444")
_MASK: tuple[int, int, int] = (51, 51, 51)
# A JPEG keeps a flat colour within a few levels.
_TOLERANCE: int = 14
_CARD_FRAME: str = (
    "<!doctype html><title>Card</title>"
    "<style>body{margin:0}input{width:100%;height:76px;border:0;font-size:20px}</style>"
    '<input aria-label="Frame card" autocomplete="cc-number" value="5555555555554444" placeholder="Card">'
)


def _form(frame_origin: str) -> str:
    return (
        "<!doctype html><title>Pay</title><style>input{display:block;width:300px;height:40px;margin:8px;"
        "font-size:20px}</style>"
        '<label>Account password <input id="pass" type="password" value="hunter2"></label>'
        '<label>Login code <input id="code" autocomplete="one-time-code" value="123456" placeholder="6 digits"></label>'
        '<label>Card number <input id="card" autocomplete="section-pay cc-number" value="4242424242424242" '
        'placeholder="1234 1234 1234 1234"></label>'
        '<label>Full name <input id="name" value="Ada" placeholder="Your name"></label>'
        f'<iframe id="frame" title="Card frame" src="{frame_origin}/card" width="320" height="80"></iframe>'
        '<textarea aria-label="Notes">Some notes</textarea><a href="/next">Next</a>'
    )


_MANY: str = "<!doctype html><title>Many</title>" + "".join(
    f'<label>Field {number} <input value="Item {number}"></label>' for number in range(1, 61)
)


def _serve(pages: dict[str, str]) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body: bytes = pages.get(self.path.split("?", 1)[0], "<!doctype html><title>Next</title>").encode()
            self.send_response(200)
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
def frame_site() -> Iterator[str]:
    """A second server: another port is another origin, as a payment provider's frame is."""
    yield from _serve({"/card": _CARD_FRAME})


@pytest.fixture(scope="module")
def site(frame_site: str) -> Iterator[str]:
    yield from _serve({"/form": _form(frame_site), "/many": _MANY})


@pytest.fixture(scope="module")
def browser_config(tmp_path_factory: pytest.TempPathFactory) -> BrowserConfig:
    settings: Path = tmp_path_factory.mktemp("settings") / "config.toml"
    settings.write_text("[browser]\nheadless = true\n", encoding="utf-8")
    return load_config(settings).browser


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(
    browser_config: BrowserConfig, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[BrowserSession]:
    config: BrowserConfig = browser_config
    session: BrowserSession = BrowserSession(
        config, SERVER, tmp_path_factory.mktemp("profile"), tmp_path_factory.mktemp("downloads"), no_runs_at_work
    )
    yield session
    await session.aclose()


def _leaks(text: str) -> list[str]:
    return [secret for secret in _SECRETS if secret in text]


async def test_no_view_a_run_reads_shows_a_secret_fields_value(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("hidden", f"{site}/form", True)
    assert _leaks(view.body) == [], view.body
    assert 'textbox "Account password"' in view.body and "[hidden]" in view.body
    # An ordinary field, a text area and the placeholders keep what they show.
    assert "Ada" in view.body and "Some notes" in view.body and "6 digits" in view.body
    for needle in ("hunter2", "password", "Card", "4242"):
        found: FindResult = await browser.find("hidden", needle, 20, True)
        assert _leaks(" ".join(found.hits)) == [], (needle, found.hits)
    # What the owner types in a takeover, played by the page: the changes view after it shows no value either.
    changed: ScriptRun = await browser.run_script(
        "hidden",
        "document.querySelector('#pass').value = 'typed-in-a-takeover';"
        " document.querySelector('#name').value = 'Grace'; true",
        5.0,
    )
    assert changed.end == "value", changed
    after: PageView = await browser.scroll("hidden", "down", 5, None, True)
    assert "typed-in-a-takeover" not in after.body and _leaks(after.body) == [], after.body
    text: str = (await browser.read_text("hidden", None, 0, 20_000)).text
    links: FindResult = await browser.links("hidden", "", 50, True)
    assert _leaks(text) == [] and "typed-in-a-takeover" not in text
    assert _leaks(" ".join(links.hits)) == []


def _pixel(image: Image.Image, box: dict[str, float]) -> tuple[int, int, int]:
    centre: tuple[int, int] = (round(box["x"] + box["w"] / 2), round(box["y"] + box["h"] / 2))
    red, green, blue = image.convert("RGB").getpixel(centre)  # type: ignore[misc]
    return int(red), int(green), int(blue)


def _masked(colour: tuple[int, int, int]) -> bool:
    return all(abs(channel - mask) <= _TOLERANCE for channel, mask in zip(colour, _MASK, strict=True))


async def _boxes(browser: BrowserSession, thread: str) -> dict[str, dict[str, float]]:
    ran: ScriptRun = await browser.run_script(
        thread,
        "Object.fromEntries(['pass', 'code', 'card', 'name', 'frame'].map((id) => {"
        " const r = document.getElementById(id).getBoundingClientRect();"
        " return [id, {x: r.x, y: r.y, w: r.width, h: r.height}]; }))",
        5.0,
    )
    assert ran.end == "value", ran
    boxes: dict[str, dict[str, float]] = json.loads(ran.text)
    return boxes


async def test_a_screenshot_covers_the_secret_fields_in_every_frame_but_not_the_others(
    browser: BrowserSession, site: str
) -> None:
    await browser.open_url("shot", f"{site}/form", True)
    boxes: dict[str, dict[str, float]] = await _boxes(browser, "shot")
    shot: Screenshot = await browser.screenshot("shot", True)
    image: Image.Image = Image.open(io.BytesIO(shot.data))
    for secret in ("pass", "code", "card", "frame"):
        assert _masked(_pixel(image, boxes[secret])), (secret, _pixel(image, boxes[secret]))
    assert not _masked(_pixel(image, boxes["name"]))


async def test_yolo_sees_every_value(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("yolo", f"{site}/form", False)
    assert _leaks(view.body) == list(_SECRETS), view.body
    boxes: dict[str, dict[str, float]] = await _boxes(browser, "yolo")
    shot: Screenshot = await browser.screenshot("yolo", False)
    assert not _masked(_pixel(Image.open(io.BytesIO(shot.data)), boxes["pass"]))


async def test_a_page_of_sixty_filled_fields_keeps_their_values(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("many", f"{site}/many", True)
    found: FindResult = await browser.find("many", "Item", 100, True)
    shown: str = view.body + "\n".join(found.hits)
    assert all(f"Item {number}" in shown for number in range(1, 61)), shown
    assert "[hidden]" not in shown
