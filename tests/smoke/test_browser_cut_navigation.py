"""A navigation a redirect cuts off, in a real headless Chrome against a local site (item 1296).

The tab is on a page whose own script sends it elsewhere while Dot's next navigation is still waiting for a slow
server: Chrome cancels that navigation (``net::ERR_ABORTED``) and the tab ends on the page the script chose. The call
used to fail with the tab already on the real page, unread.
"""

import threading
import time
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import PageView
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

SERVER: ServerConfig = load_default_config().server

# The page sends the tab on after this long: while the slow page below is still being waited for.
_SEND_ON_MS: int = 900
_SLOW_S: float = 1.4


class _Handler(BaseHTTPRequestHandler):
    port: int = 0

    def do_GET(self) -> None:
        path: str = self.path.split("?", 1)[0]
        if path == "/slow":
            time.sleep(_SLOW_S)
        pages: dict[str, str] = {
            "/start": f"<html><body>start<script>setTimeout(() => location.href = '/final', {_SEND_ON_MS})</script>"
            "</body></html>",
            "/start-blocked": "<html><body>start<script>setTimeout(() => location.href = "
            f"'http://localhost:{self.port}/final', {_SEND_ON_MS})</script></body></html>",
            "/slow": "<html><body>slow page</body></html>",
            "/final": "<html><head><title>Final</title></head><body><h1>Final page</h1></body></html>",
            "/plain": "<html><head><title>Plain</title></head><body><h1>Plain page</h1></body></html>",
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
    _Handler.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def browser_config(tmp_path_factory: pytest.TempPathFactory) -> BrowserConfig:
    settings: Path = tmp_path_factory.mktemp("settings") / "config.toml"
    settings.write_text('[browser]\nheadless = true\nblocked_hosts = ["localhost"]\n', encoding="utf-8")
    return load_config(settings).browser


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(
    browser_config: BrowserConfig, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[BrowserSession]:
    session: BrowserSession = BrowserSession(
        browser_config,
        SERVER,
        tmp_path_factory.mktemp("profile"),
        tmp_path_factory.mktemp("downloads"),
        no_runs_at_work,
    )
    yield session
    await session.aclose()


async def test_a_navigation_cut_off_by_a_redirect_reads_the_page_the_tab_is_on(
    browser: BrowserSession, site: str
) -> None:
    await browser.open_url("cut", f"{site}/start", True)

    view: PageView = await browser.open_url("cut", f"{site}/slow", True)

    assert view.url == f"{site}/final", view.url
    assert "Final page" in view.body
    assert any(note.startswith("the page redirected to ") and "/final" in note for note in view.notes), view.notes


async def test_a_navigation_that_is_not_cut_is_read_as_before_with_no_redirect_note(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("plain", f"{site}/plain", True)

    assert view.url == f"{site}/plain"
    assert "Plain page" in view.body
    assert not any("redirected" in note for note in view.notes), view.notes


async def test_a_cut_navigation_whose_redirect_the_rules_refused_is_tried_once_more_and_never_lands_there(
    browser: BrowserSession, site: str
) -> None:
    """The page's script sends the tab to a blocked host: Chrome's guard refuses that request, the navigation Dot began
    is cut and the tab stands on no page, so it is made once more, and the tab ends on the page asked for."""
    await browser.open_url("blocked", f"{site}/start-blocked", True)

    view: PageView = await browser.open_url("blocked", f"{site}/slow", True)

    assert view.url == f"{site}/slow", view.url
    assert "slow page" in view.body
    assert not any("redirected" in note for note in view.notes), view.notes
