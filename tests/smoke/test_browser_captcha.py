"""The human check end to end: a real headless Chrome on pages served by a local fixture server.

The pages are a site's own "Press & Hold" modal, a checkbox widget in another origin, a page that is still checking
itself, an image challenge, and a page with no check at all. What is measured is the answer and how long it took: a
check that a press or a hold passes is passed in one call and a second or two, not a screenshot and a guessed pixel and
a sleep apiece.
"""

import threading
import time
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image

from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import HumanCheckOutcome
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

SERVER: ServerConfig = load_default_config().server

# How long the page behind the modal waits with no pointer down before it lets go of the hold.
_HOLD_S: float = 1.0

_HOLD_PAGE: str = (
    """<!doctype html><html><head><title>Hold</title></head><body>
<h1>Access starts here</h1>
<div id="modal" style="position:fixed;left:300px;top:300px;width:500px;height:220px;background:#fff;
     border:1px solid #888;padding:20px">
  <p>Press &amp; Hold to confirm you are a human (and not a bot).</p>
  <button id="hold" style="width:200px;height:48px">Press &amp; Hold</button>
</div>
<script>
const b = document.getElementById('hold');
let timer = null, done = false;
b.addEventListener('pointerdown', () => {
  b.textContent = 'Holding\\u2026';
  timer = setTimeout(() => {
    done = true;
    document.getElementById('modal').remove();
  }, """
    + str(int(_HOLD_S * 1000))
    + """);
});
const stop = () => { clearTimeout(timer); if (!done) b.textContent = 'Press & Hold'; };
b.addEventListener('pointerup', stop);
b.addEventListener('pointerleave', stop);
</script></body></html>"""
)

_WIDGET_PAGE: str = """<!doctype html><html><head><title>hCaptcha</title></head><body style="margin:0">
<div id="checkbox" role="checkbox" aria-checked="false"
     style="width:24px;height:24px;margin:24px;border:1px solid #333"></div>
<script>
const box = document.getElementById('checkbox');
box.addEventListener('click', () => {
  setTimeout(() => { box.setAttribute('aria-checked', 'true'); parent.postMessage({token: 'ok'}, '*'); }, 400);
});
</script></body></html>"""

_CHECKBOX_PAGE: str = """<!doctype html><html><head><title>Checkbox</title></head><body>
<h1>Continue with your email</h1>
<textarea name="h-captcha-response" style="display:none"></textarea>
<iframe title="hCaptcha checkbox" src="http://localhost:%(widget)s/widget" width="303" height="78"
        style="border:0;position:absolute;left:400px;top:400px"></iframe>
<script>addEventListener('message', () => { document.querySelector('textarea').value = 'x'.repeat(40); });</script>
</body></html>"""

_CELL: str = '<td style="width:100px;height:100px;border:1px solid #999"></td>'
_ROW: str = f"<tr>{_CELL * 3}</tr>"

_CHALLENGE_PAGE: str = (
    """<!doctype html><html><head><title>reCAPTCHA</title></head><body style="margin:0">
<div class="rc-imageselect-desc">Select all images with traffic lights</div>
<table class="rc-imageselect-table rc-imageselect-table-33" style="margin:8px;border-collapse:collapse">
"""
    + _ROW * 3
    + """</table>
<button class="btn" style="margin:8px;width:96px;height:36px">Verify</button>
</body></html>"""
)

_GRID_PAGE: str = """<!doctype html><html><head><title>Grid</title></head><body>
<h1>Sign in</h1>
<iframe title="reCAPTCHA challenge" src="http://localhost:%(widget)s/challenge" width="400" height="420"
        style="border:0;position:absolute;left:300px;top:200px"></iframe>
</body></html>"""

_CHECKING_PAGE: str = """<!doctype html><html><head><title>Just a moment...</title></head><body>
<h1>Checking your browser before accessing the site.</h1>
<script>setTimeout(() => { document.body.innerHTML = '<h1>All good</h1>'; }, 1200);</script>
</body></html>"""

# The line says "human verification" and a button that pays sits in the same box: no pass, no press (item 1252).
_BESIDE_PAGE: str = """<!doctype html><html><head><title>Human verification</title></head><body>
<div style="width:400px;border:1px solid #888;padding:20px">
  <h2>Human verification</h2>
  <p>Confirm you are a human to continue.</p>
  <button style="width:120px;height:40px">Pay 49,00 €</button>
</div>
</body></html>"""

_PLAIN_PAGE: str = (
    """<!doctype html><html><head><title>Plain</title></head><body><h1>Nothing to pass</h1></body></html>"""
)


class _Handler(BaseHTTPRequestHandler):
    widget_port: int = 0

    def do_GET(self) -> None:
        path: str = self.path.split("?", 1)[0]
        pages: dict[str, str] = {
            "/hold": _HOLD_PAGE,
            "/widget": _WIDGET_PAGE,
            "/checkbox": _CHECKBOX_PAGE % {"widget": self.widget_port},
            "/grid": _GRID_PAGE % {"widget": self.widget_port},
            "/challenge": _CHALLENGE_PAGE,
            "/checking": _CHECKING_PAGE,
            "/plain": _PLAIN_PAGE,
            "/beside": _BESIDE_PAGE,
        }
        if path not in pages:
            self.send_response(404)
            self.end_headers()
            return
        body: bytes = pages[path].encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    _Handler.widget_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # localhost is another origin than 127.0.0.1, so the widget's frame is cross-origin as a real one is.
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def downloads(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("downloads")


@pytest.fixture(scope="module")
def browser_config(tmp_path_factory: pytest.TempPathFactory) -> BrowserConfig:
    settings: Path = tmp_path_factory.mktemp("settings") / "config.toml"
    settings.write_text("[browser]\nheadless = true\n", encoding="utf-8")
    return load_config(settings).browser


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(
    browser_config: BrowserConfig, tmp_path_factory: pytest.TempPathFactory, downloads: Path
) -> AsyncIterator[BrowserSession]:
    session: BrowserSession = BrowserSession(
        browser_config, SERVER, tmp_path_factory.mktemp("profile"), downloads, no_runs_at_work
    )
    yield session
    await session.aclose()


async def _check(
    browser: BrowserSession, thread: str, url: str, timeout_s: float = 8.0
) -> tuple[HumanCheckOutcome, float]:
    await browser.open_url(thread, url, True)
    started: float = time.monotonic()
    outcome: HumanCheckOutcome = await browser.human_check(thread, timeout_s, True)
    return (outcome, time.monotonic() - started)


async def test_a_press_and_hold_button_is_held_until_the_widget_lets_the_page_go(
    browser: BrowserSession, site: str
) -> None:
    outcome, took = await _check(browser, "hold", f"{site}/hold")
    assert outcome.result == "passed", outcome
    assert outcome.role == "hold"
    assert outcome.box is not None
    assert outcome.did == (f"held it {outcome.did[0].split()[2]} s at {outcome.box.words()}",)
    assert outcome.note == "the button went away"
    # The page's modal goes only if the page's own 1 s timer ran to its end while the pointer was down.
    assert "Press & Hold" not in (outcome.view.body if outcome.view else "")
    assert took < 4.0, took


async def test_a_checkbox_in_another_origin_is_pressed_at_its_own_pixels(browser: BrowserSession, site: str) -> None:
    outcome, took = await _check(browser, "checkbox", f"{site}/checkbox")
    assert outcome.result == "passed", outcome
    assert outcome.note == "it cleared"
    assert outcome.view is not None
    assert took < 4.0, took


async def test_a_page_that_is_still_checking_itself_is_waited_for_and_no_longer(
    browser: BrowserSession, site: str
) -> None:
    outcome, took = await _check(browser, "checking", f"{site}/checking")
    assert outcome.result == "passed", outcome
    assert outcome.role == "wait"
    # The page lets go on at 1.2 s: a fixed ten-second wait would have been eight seconds of nothing.
    assert took < 3.0, took


async def test_an_image_challenge_comes_back_as_its_own_picture_and_the_box_to_act_in(
    browser: BrowserSession, site: str
) -> None:
    outcome, took = await _check(browser, "grid", f"{site}/grid")
    assert outcome.result == "challenge", outcome
    assert "traffic lights" in outcome.text
    assert (outcome.cols, outcome.rows) == (3, 3)
    assert outcome.crop_box is not None
    assert outcome.verify is not None
    assert outcome.box is not None
    assert outcome.verify.x >= outcome.box.x and outcome.verify.y >= outcome.box.y
    # Twice its size in CSS pixels, as the answer says.
    with Image.open(BytesIO(outcome.crop)) as picture:
        assert abs(picture.width - outcome.crop_box.width * 2) <= 2, picture.size
        assert abs(picture.height - outcome.crop_box.height * 2) <= 2, picture.size
    assert took < 2.0, took


async def test_a_page_with_no_check_says_so_without_looking_for_one(browser: BrowserSession, site: str) -> None:
    outcome, took = await _check(browser, "plain", f"{site}/plain")
    assert outcome.result == "none"
    assert took < 1.0, took


async def test_a_check_drawn_beside_a_payment_button_presses_nothing(browser: BrowserSession, site: str) -> None:
    """A page that labels a Pay button like a human check: the words are not on the button, so nothing is pressed."""
    outcome, took = await _check(browser, "beside", f"{site}/beside")

    assert outcome.did == (), outcome
    assert outcome.result == "none", outcome
    assert took < 3.0, took
