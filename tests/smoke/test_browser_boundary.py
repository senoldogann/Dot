"""The URL rules on what Dot's own Chrome sends (item 135): a real headless Chrome against two local servers, one of
which stands in for Dot's interface and counts what reaches it.

Covers the ways a page can send the browser to the interface: a popup, a resource it loads, a fetch, a service worker,
a link, a script and a redirect on the way; and the daemon's own refusal of navigations that pages of other sites start
(``Sec-Fetch-Site``), but for the page a sign-in comes back to, measured with the real guard behind a real server.
"""

import asyncio
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import uvicorn
from playwright.async_api import (
    Browser,
    BrowserContext,
    CDPSession,
    Frame,
    Page,
    Playwright,
    Response,
    async_playwright,
)
from starlette.types import Message, Receive, Scope, Send
from test_browser import act, ref_of

from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import PageView, ScriptRun, Step
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config
from dotagent.core.errors import ForbiddenUrlError
from dotagent.mcp.signin import CALLBACK_PATH
from dotagent.server.guard import LocalGuard

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_TOKEN: str = "boundary-test-token"
_STARTUP_S: float = 10.0
_READS: int = 10
_SLOW_CDP_S: float = 0.3


@dataclass(frozen=True, slots=True)
class Arrival:
    """One request that reached a server: its path and the Fetch Metadata Chrome sent with it."""

    path: str
    fetch_site: str | None
    fetch_mode: str | None


@dataclass(slots=True)
class Arrivals:
    items: list[Arrival] = field(default_factory=list)

    def of(self, path: str) -> list[Arrival]:
        return [arrival for arrival in self.items if arrival.path == path]


class _InsideHandler(BaseHTTPRequestHandler):
    """The stand-in for Dot's interface: every request that reaches it is recorded, and answered."""

    arrivals: Arrivals = Arrivals()

    def do_GET(self) -> None:
        self.arrivals.items.append(
            Arrival(
                path=self.path,
                fetch_site=self.headers.get("Sec-Fetch-Site"),
                fetch_mode=self.headers.get("Sec-Fetch-Mode"),
            )
        )
        body: bytes = b"<!doctype html><title>Inside</title><h1>Inside Dot</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _site_pages(inside: str, guarded: str) -> dict[str, str]:
    return {
        "/": f"""<!doctype html><title>Outside</title><h1>Outside</h1>
<a href="{inside}/popup" target="_blank">Open inside</a>
<a href="{inside}/link">Inside link</a>
<a href="/hop">Hop to inside</a>
<a href="{guarded}/">Guarded link</a>
<a href="/hop-guarded">Hop to guarded</a>
<a href="/allow">Allow sign-in</a>
<img src="{inside}/pixel.png" alt="pixel">
<img src="/own.png" alt="own">
<p id="out">quiet</p>""",
        "/sw-page": """<!doctype html><title>Worker</title><h1>Worker page</h1><p id="out">quiet</p>""",
    }


def _site_handler(inside: str, guarded: str) -> type[BaseHTTPRequestHandler]:
    pages: dict[str, str] = _site_pages(inside, guarded)

    class Handler(BaseHTTPRequestHandler):
        arrivals: Arrivals = Arrivals()

        def do_GET(self) -> None:
            path: str = self.path.split("?", 1)[0]
            self.arrivals.items.append(Arrival(path=path, fetch_site=None, fetch_mode=None))
            if path == "/hop":
                self._redirect(f"{inside}/chain")
                return
            if path == "/hop-guarded":
                self._redirect(f"{guarded}/")
                return
            if path == "/allow":
                # An authorization server that the owner allowed a sign-in on sends the browser back to the daemon.
                self._redirect(f"{guarded}{CALLBACK_PATH}?code=MARKER-code&state=MARKER-state")
                return
            if path == "/sw.js":
                body: bytes = (
                    "self.addEventListener('fetch', (event) => { "
                    f"if (event.request.url.includes('/through-worker')) event.respondWith(fetch('{inside}/worker')); "
                    f"}});"
                ).encode()
                self._answer(body, "text/javascript")
                return
            if path == "/own.png":
                self._answer(b"\x89PNG\r\n\x1a\n", "image/png")
                return
            if path in pages:
                self._answer(pages[path].encode(), "text/html; charset=utf-8")
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _redirect(self, target: str) -> None:
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _answer(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


async def _guarded_app(scope: Scope, receive: Receive, send: Send) -> None:
    body: bytes = b"<!doctype html><title>Guarded</title><h1>Guarded</h1>"
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/html")]})
    await send({"type": "http.response.body", "body": body})


@dataclass(frozen=True, slots=True)
class Served:
    """What the guarded server did with one request: the Fetch Metadata it saw and the status it answered with."""

    path: str
    fetch_site: str | None
    status: int


class _Recorder:
    """Keeps what the guard answered to each request, before the guard's own refusal or the app's answer."""

    def __init__(self, port: int) -> None:
        self.served: list[Served] = []
        self._app: LocalGuard = LocalGuard(_guarded_app, token=_TOKEN, port=port, reach=frozenset())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers: dict[bytes, bytes] = dict(scope["headers"])
        fetch_site: bytes | None = headers.get(b"sec-fetch-site")

        async def recording_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                self.served.append(
                    Served(
                        path=scope["path"],
                        fetch_site=None if fetch_site is None else fetch_site.decode(),
                        status=message["status"],
                    )
                )
            await send(message)

        await self._app(scope, receive, recording_send)


@pytest.fixture(scope="module")
def inside() -> Iterator[str]:
    server, url = _serve(_InsideHandler)
    yield url
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def recorder() -> Iterator[_Recorder]:
    port: int = _free_port()
    app: _Recorder = _Recorder(port)
    server: uvicorn.Server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread: threading.Thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline: float = time.monotonic() + _STARTUP_S
    while not server.started:
        assert time.monotonic() < deadline, "the guarded server did not start"
        time.sleep(0.05)
    yield app
    server.should_exit = True
    thread.join(_STARTUP_S)


@pytest.fixture(scope="module")
def guarded(recorder: _Recorder) -> str:
    return f"http://127.0.0.1:{recorder._app._port}"


@pytest.fixture(scope="module")
def site(inside: str, guarded: str) -> Iterator[str]:
    server, url = _serve(_site_handler(inside, guarded))
    yield url
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def browser_config(tmp_path_factory: pytest.TempPathFactory) -> BrowserConfig:
    settings: Path = tmp_path_factory.mktemp("settings") / "config.toml"
    settings.write_text("[browser]\nheadless = true\n", encoding="utf-8")
    return load_config(settings).browser


@pytest.fixture(scope="module")
def server_config(inside: str) -> ServerConfig:
    """The daemon stands at the inside server's port."""
    port: int | None = urlsplit(inside).port
    assert port is not None
    return load_default_config().server.model_copy(update={"port": port})


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(
    browser_config: BrowserConfig, server_config: ServerConfig, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[BrowserSession]:
    session: BrowserSession = BrowserSession(
        browser_config,
        server_config,
        tmp_path_factory.mktemp("profile"),
        tmp_path_factory.mktemp("downloads"),
        no_runs_at_work,
    )
    yield session
    await session.aclose()


@pytest.fixture(autouse=True)
def _fresh_arrivals() -> None:
    _InsideHandler.arrivals.items.clear()


async def _settled(seconds: float = 0.5) -> None:
    await asyncio.sleep(seconds)


async def test_a_popup_a_page_opens_to_the_interface_never_reaches_it(
    browser: BrowserSession, site: str, inside: str
) -> None:
    view: PageView = await browser.open_url("popup", f"{site}/")
    await act(browser, "popup", [Step(action="click", ref=ref_of(view.body, 'link "Open inside"'))], False)
    await _settled()
    assert _InsideHandler.arrivals.items == []
    await browser.run_script("popup", f"window.open('{inside}/opened'); 'opened'", 5.0)
    await _settled()
    assert _InsideHandler.arrivals.items == []


async def test_a_resource_a_fetch_and_a_script_of_the_page_are_refused(
    browser: BrowserSession, site: str, inside: str
) -> None:
    await browser.open_url("resource", f"{site}/")
    await _settled()
    assert _InsideHandler.arrivals.of("/pixel.png") == []
    fetched: ScriptRun = await browser.run_script(
        "resource", f"fetch('{inside}/api/status').then((r) => 'status ' + r.status, (e) => 'failed: ' + e.name)", 10.0
    )
    assert "failed" in fetched.text, fetched
    # A script that sends the tab there: the request is refused, and the tab is reset as after a redirect there.
    with pytest.raises(ForbiddenUrlError) as refused:
        await browser.run_script("resource", f"() => {{ location.href = '{inside}/script'; }}", 10.0)
    assert refused.value.url == f"{inside}/script" and "Dot's own interface" in refused.value.reason, refused.value
    assert browser.current_url("resource") == "about:blank"
    assert _InsideHandler.arrivals.items == []


async def test_a_service_worker_fetches_nothing_for_the_page(browser: BrowserSession, site: str) -> None:
    await browser.open_url("worker", f"{site}/sw-page")
    registered: ScriptRun = await browser.run_script(
        "worker",
        "navigator.serviceWorker.register('/sw.js').then(() => 'registered', (e) => 'refused: ' + e.name)",
        10.0,
    )
    through: ScriptRun = await browser.run_script(
        "worker", "fetch('/through-worker').then((r) => 'status ' + r.status, (e) => 'failed: ' + e.name)", 10.0
    )
    assert _InsideHandler.arrivals.items == [], (registered, through)


async def test_a_redirect_to_the_interface_is_stopped_before_it_is_fetched(browser: BrowserSession, site: str) -> None:
    """The route of the context does not see the hops of a redirect; the tab's own Fetch session does."""
    port: int | None = urlsplit(site).port
    with pytest.raises(ForbiddenUrlError) as refused:
        await browser.open_url("hop", f"http://localhost:{port}/hop")
    assert "Dot's own interface" in refused.value.reason, refused.value
    assert refused.value.resource_type == "document"
    assert browser.current_url("hop") == "about:blank"
    assert _InsideHandler.arrivals.items == []


async def test_a_link_that_redirects_to_the_interface_is_stopped(
    browser: BrowserSession, site: str, inside: str
) -> None:
    """A link straight to the interface, and one whose redirect ends there: the request is refused, the step ends with
    the refusal and the tab is reset, as when a page landed there before."""
    for label, refused_url in (('link "Inside link"', f"{inside}/link"), ('link "Hop to inside"', f"{inside}/chain")):
        view: PageView = await browser.open_url("link", f"{site}/")
        with pytest.raises(ForbiddenUrlError) as refused:
            await act(browser, "link", [Step(action="click", ref=ref_of(view.body, label))], False)
        assert refused.value.url == refused_url and "Dot's own interface" in refused.value.reason, refused.value
        assert browser.current_url("link") == "about:blank"
    assert _InsideHandler.arrivals.items == []


async def test_a_page_read_in_a_scratch_tab_is_stopped_on_the_way_too(
    browser: BrowserSession, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each read navigates a new tab, whose guard is also begun when Chrome announces the tab. With Chrome slow to open
    the guard's session, reads in a row show that none navigates before its tab's guard is up (measured 2026-10-16:
    a read could navigate while the guard begun for its tab was still under way, and its redirect reached Dot's
    port)."""
    open_session = BrowserContext.new_cdp_session

    async def slow_session(context: BrowserContext, page: Page | Frame) -> CDPSession:
        await asyncio.sleep(_SLOW_CDP_S)
        return await open_session(context, page)

    monkeypatch.setattr(BrowserContext, "new_cdp_session", slow_session)
    port: int | None = urlsplit(site).port
    for read in range(_READS):
        with pytest.raises(ForbiddenUrlError):
            await browser.read_page(f"http://localhost:{port}/hop?read={read}")
    assert _InsideHandler.arrivals.items == []


async def test_the_pages_and_resources_of_other_servers_load_as_before(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("plain", f"{site}/")
    assert view.title == "Outside" and view.status == 200
    own: ScriptRun = await browser.run_script(
        "plain", "fetch('/own.png').then((r) => 'status ' + r.status, (e) => 'failed: ' + e.name)", 10.0
    )
    assert "status 200" in own.text, own


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def plain_page(browser_config: BrowserConfig) -> AsyncIterator[Page]:
    """A Chrome of the test's own, without Dot's route: what reaches the daemon is the daemon's guard to refuse."""
    playwright: Playwright = await async_playwright().start()
    chrome: Browser = await playwright.chromium.launch(channel=browser_config.channel, headless=True)
    page: Page = await chrome.new_page()
    yield page
    await chrome.close()
    await playwright.stop()


async def _navigation_status(page: Page) -> int:
    response: Response = await page.wait_for_event(
        "response", lambda r: r.request.is_navigation_request() and r.frame == page.main_frame
    )
    return response.status


def _last_page(recorder: _Recorder) -> Served:
    """What the guarded server did with the last request for its page; Chrome asks for a favicon beside it."""
    return [served for served in recorder.served if served.path == "/"][-1]


async def test_the_daemon_refuses_navigations_that_pages_of_other_sites_start(
    plain_page: Page, site: str, guarded: str, recorder: _Recorder
) -> None:
    """The address bar reaches the UI (``Sec-Fetch-Site: none``); a link of another site, and a redirect it sends the
    browser through, do not (``cross-site``)."""
    recorder.served.clear()
    direct: Response | None = await plain_page.goto(f"{guarded}/")
    assert direct is not None and direct.status == 200
    assert _last_page(recorder) == Served(path="/", fetch_site="none", status=200), recorder.served

    # The other site is reached by name: a site is a scheme and a host, so another server of 127.0.0.1 would count as
    # the same site, whatever its port.
    port: int | None = urlsplit(site).port
    await plain_page.goto(f"http://localhost:{port}/")
    async with plain_page.expect_navigation():
        await plain_page.click('text="Guarded link"')
    assert _last_page(recorder) == Served(path="/", fetch_site="cross-site", status=403), recorder.served

    await plain_page.goto(f"http://localhost:{port}/")
    async with plain_page.expect_navigation():
        await plain_page.click('text="Hop to guarded"')
    assert _last_page(recorder) == Served(path="/", fetch_site="cross-site", status=403), recorder.served


async def test_the_page_a_sign_in_comes_back_to_is_served_after_a_redirect_from_another_site(
    plain_page: Page, site: str, recorder: _Recorder
) -> None:
    """An authorization server sends the browser back to ``/oauth/callback`` through a redirect once the owner allowed
    the sign-in on its page (item 279): Chrome sends that navigation as ``cross-site``, and the daemon serves this path
    to it while it refuses the UI to the same navigation."""
    recorder.served.clear()
    port: int | None = urlsplit(site).port
    await plain_page.goto(f"http://localhost:{port}/")
    async with plain_page.expect_navigation():
        await plain_page.click('text="Allow sign-in"')
    callbacks: list[Served] = [served for served in recorder.served if served.path == CALLBACK_PATH]
    assert callbacks == [Served(path=CALLBACK_PATH, fetch_site="cross-site", status=200)], recorder.served


async def test_what_chrome_sends_when_the_browser_itself_is_redirected(
    plain_page: Page, site: str, recorder: _Recorder
) -> None:
    """A redirect that a navigation of the browser's own (``goto``, the address bar) runs into keeps ``Sec-Fetch-Site:
    none`` through the hop (measured 2026-10-15, Chrome 155), so the daemon serves it: no page of another site started
    it. In Dot's own Chrome the tab's Fetch session stops such a hop before it is sent
    (``test_a_redirect_to_the_interface_is_stopped_before_it_is_fetched``); in the owner's Chrome the landing check
    leaves the page before anything is read from it."""
    recorder.served.clear()
    port: int | None = urlsplit(site).port
    await plain_page.goto(f"http://localhost:{port}/hop-guarded")
    assert _last_page(recorder) == Served(path="/", fetch_site="none", status=200), recorder.served
