"""The web tools' rule that a page is on the public internet, held before a request goes out (item 1319), in Dot's own
headless Chrome against local servers.

``localhost`` stands for a public name where a test says so (the look at an address is told it is public); every other
address keeps the real rule. What is measured is what the servers were asked: a request refused before it was sent
never reaches them, where the rule used to be applied to a page Chrome had already fetched.
"""

import asyncio
import socket
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
import pytest_asyncio

from dotagent.browser import session as session_module
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import PageView
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config
from dotagent.core.errors import ForbiddenUrlError
from dotagent.web import addresses

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

SERVER: ServerConfig = load_default_config().server


class _Handler(BaseHTTPRequestHandler):
    seen: ClassVar[list[str]] = []
    port: ClassVar[int] = 0

    def do_GET(self) -> None:
        _Handler.seen.append(self.path)
        if self.path == "/hop":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{_Handler.port}/inner")
            self.end_headers()
            return
        if self.path == "/frame-hop":
            # The same hop out of a frame of another site: 127.0.0.1 is public in the look below, 0.0.0.0 never is.
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{_Handler.port}/frame-inner")
            self.end_headers()
            return
        pages: dict[str, tuple[str, bytes]] = {
            "/with-same-site-frame": (
                "text/html",
                f'<html><body><h1>With a same-site frame</h1><iframe src="http://localhost:{_Handler.port}/hop">'
                "</iframe></body></html>".encode(),
            ),
            "/with-frame": (
                "text/html",
                f'<html><body><h1>With a frame</h1><iframe src="http://127.0.0.1:{_Handler.port}/frame-hop"></iframe>'
                "</body></html>".encode(),
            ),
            "/page": ("text/html", b"<html><body><h1>A public page</h1></body></html>"),
            "/with-image": (
                "text/html",
                f'<html><body><h1>With an image</h1><img src="http://127.0.0.1:{_Handler.port}/pixel.png">'
                "</body></html>".encode(),
            ),
            "/inner": ("text/html", b"<html><body><h1>The inside</h1></body></html>"),
        }
        content_type, body = pages.get(self.path, ("text/plain", b"nothing"))
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site() -> Iterator[int]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    _Handler.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def _fresh() -> None:
    _Handler.seen.clear()


@pytest.fixture(scope="module")
def browser_config(tmp_path_factory: pytest.TempPathFactory) -> BrowserConfig:
    settings: Path = tmp_path_factory.mktemp("settings") / "config.toml"
    settings.write_text("[browser]\nheadless = true\n", encoding="utf-8")
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


@pytest.fixture
def localhost_is_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """``localhost`` is a public name for the look at an address that comes before a request, and for the check that
    follows a page read (which asks what Chrome connected to: this Mac, here); ``127.0.0.1`` and every other address
    keep the real rule in the look before the request."""
    real = addresses.unread_or_non_public

    async def look(host: str, timeout_s: float) -> str | None:
        return None if host == "localhost" else await real(host, timeout_s)

    async def public_host(host: str) -> None:
        return None

    monkeypatch.setattr(session_module, "unread_or_non_public", look)
    monkeypatch.setattr(session_module, "non_public_address", public_host)
    monkeypatch.setattr(session_module, "is_private_text", lambda address: False)


@pytest.fixture
def loopback_names_are_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """``localhost`` and ``127.0.0.1`` are two public names (two sites, so Chrome keeps a frame of one in a process of
    its own inside a page of the other); ``0.0.0.0``, which reaches this Mac as well, keeps the real rule."""
    real = addresses.unread_or_non_public

    async def look(host: str, timeout_s: float) -> str | None:
        if host == "localhost" and "/frame-hop" in _Handler.seen:
            return "it is a private or local network address"
        return None if host in ("localhost", "127.0.0.1") else await real(host, timeout_s)

    async def public_host(host: str) -> None:
        return None

    monkeypatch.setattr(session_module, "unread_or_non_public", look)
    monkeypatch.setattr(session_module, "non_public_address", public_host)
    monkeypatch.setattr(session_module, "is_private_text", lambda address: False)


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def _forget_looks(browser: BrowserSession) -> None:
    browser._public_hosts.clear()


async def test_a_page_at_a_private_address_is_refused_before_a_request_is_sent(
    browser: BrowserSession, site: int
) -> None:
    with pytest.raises(ForbiddenUrlError, match="the web tools only reach the public internet") as refused:
        await browser.read_page(f"http://127.0.0.1:{site}/page", public_only=True)

    assert "private or local network" in refused.value.reason
    assert _Handler.seen == [], "the request went out before the rule was applied"


async def test_a_public_page_is_read_as_before(browser: BrowserSession, site: int, localhost_is_public: None) -> None:
    document = await browser.read_page(f"http://localhost:{site}/page", public_only=True)

    assert "A public page" in document.html
    assert document.status == 200
    assert _Handler.seen == ["/page"]


async def test_a_redirect_to_a_private_address_is_refused_at_its_hop_and_never_reaches_it(
    browser: BrowserSession, site: int, localhost_is_public: None
) -> None:
    with pytest.raises(ForbiddenUrlError):
        await browser.read_page(f"http://localhost:{site}/hop", public_only=True)

    assert _Handler.seen == ["/hop"], "the second hop was sent although it leads to the local network"


async def test_a_resource_of_a_public_page_at_a_private_address_is_not_requested_and_the_page_is_read(
    browser: BrowserSession, site: int, localhost_is_public: None
) -> None:
    document = await browser.read_page(f"http://localhost:{site}/with-image", public_only=True)

    assert "With an image" in document.html
    assert _Handler.seen == ["/with-image"], "the page's image at 127.0.0.1 was fetched"


async def test_a_name_that_cannot_be_read_is_refused_not_let_by(
    browser: BrowserSession, site: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resolver is told to have no answer, so that no query leaves this Mac."""
    asked: list[str] = []

    async def unresolved(host: str, port: object, **_: object) -> list[tuple[Any, ...]]:
        asked.append(host)
        raise socket.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", unresolved)

    with pytest.raises(ForbiddenUrlError) as refused:
        await browser.read_page("http://no-such-host.invalid/page", public_only=True)

    assert "not known to be public" in refused.value.reason
    assert "no-such-host.invalid" in asked


async def test_the_same_private_address_is_read_when_the_rule_is_not_asked_for(
    browser: BrowserSession, site: int
) -> None:
    """The owner's dev servers in the browser tools, and a web tool with ``allow_private_hosts`` on, are unchanged."""
    document = await browser.read_page(f"http://127.0.0.1:{site}/page", public_only=False)
    assert "A public page" in document.html

    view: PageView = await browser.open_url("dev", f"http://127.0.0.1:{site}/page", True)
    assert "A public page" in view.body


async def test_a_page_a_crawl_renders_in_the_run_tab_is_held_to_the_rule_only_while_it_loads(
    browser: BrowserSession, site: int, localhost_is_public: None
) -> None:
    with pytest.raises(ForbiddenUrlError):
        await browser.open_url("crawl", f"http://127.0.0.1:{site}/page", True, public_only=True)
    assert _Handler.seen == []

    # The tab is let go of the rule afterwards: the browser tools reach what they did with it.
    view: PageView = await browser.open_url("crawl", f"http://127.0.0.1:{site}/page", True)
    assert "A public page" in view.body
    assert _Handler.seen == ["/page"]

    public: PageView = await browser.open_url("crawl", f"http://localhost:{site}/page", True, public_only=True)
    assert "A public page" in public.body


async def test_a_redirect_out_of_a_frame_of_the_same_site_is_refused_at_its_hop(
    browser: BrowserSession, site: int, localhost_is_public: None
) -> None:
    """A frame Chrome keeps in the page's own process is asked about by the tab's guard, hops included."""
    document = await browser.read_page(f"http://localhost:{site}/with-same-site-frame", public_only=True)

    assert "With a same-site frame" in document.html
    assert "/hop" in _Handler.seen
    assert "/inner" not in _Handler.seen, "the frame's second hop was sent although it leads to the local network"


async def test_a_redirect_out_of_a_frame_of_another_site_is_not_reached_by_the_rule(
    browser: BrowserSession, site: int, loopback_names_are_public: None
) -> None:
    """The limit docs/safety.md names, held as measured (2026-11-22, Chrome headless): a frame of another site is a
    target of its own, and only its first request is asked about (by the context's route); the hops that follow are
    not. A page can therefore make a private address a hop of its frame. When this test fails, the hop is reached
    now: the rule holds more than the docs say, and docs/safety.md, docs/runs.md and roadmap/1319.md say so."""
    document = await browser.read_page(f"http://localhost:{site}/with-frame", public_only=True)

    assert "With a frame" in document.html
    assert "/frame-hop" in _Handler.seen
    assert "/frame-inner" in _Handler.seen
