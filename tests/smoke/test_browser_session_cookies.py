"""Dot's own Chrome keeps the cookies of a site that have no expiry when it starts again (item 453), as a real browser
keeps a sign-in: a real headless Chrome on a throwaway profile against a local server that sets one. Without the word
the profile is given before Chrome starts, Chrome drops such a cookie as it starts, which is what the owner's sites
signed them out of."""

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dotagent.browser import session as session_module
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import PageView
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]


class _Site(BaseHTTPRequestHandler):
    """``/set`` sets a cookie without an expiry and one with; any other page shows the cookies it was sent."""

    def do_GET(self) -> None:
        if self.path == "/set":
            body: bytes = b"<!doctype html><title>Set</title><p>set</p>"
            self.send_response(200)
            self.send_header("Set-Cookie", "session_only=kept-across; Path=/")
            self.send_header("Set-Cookie", "dated=kept-anyway; Path=/; Max-Age=3600")
        else:
            sent: str = self.headers.get("Cookie") or "none"
            body = f"<!doctype html><title>Check</title><p id='sent'>sent: {sent}</p>".encode()
            self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _browser_config(tmp_path: Path) -> BrowserConfig:
    settings: Path = tmp_path / "config.toml"
    settings.write_text("[browser]\nheadless = true\n", encoding="utf-8")
    return load_config(settings).browser


async def _sent_after_a_restart(config: BrowserConfig, profile: Path, downloads: Path, site: str) -> str:
    """What the site is sent once Dot's Chrome, which got its cookies, has closed and started again."""
    server: ServerConfig = load_default_config().server
    first: BrowserSession = BrowserSession(config, server, profile, downloads, no_runs_at_work)
    try:
        await first.open_url("set", f"{site}/set", True)
    finally:
        await first.aclose()
    again: BrowserSession = BrowserSession(config, server, profile, downloads, no_runs_at_work)
    try:
        view: PageView = await again.open_url("check", f"{site}/check", True)
    finally:
        await again.aclose()
    return view.body


async def test_a_cookie_without_an_expiry_is_still_sent_after_dots_chrome_starts_again(
    tmp_path: Path, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config: BrowserConfig = _browser_config(tmp_path)
    kept: str = await _sent_after_a_restart(config, tmp_path / "kept", tmp_path / "downloads", site)
    assert "session_only=kept-across" in kept and "dated=kept-anyway" in kept, kept
    # The word is what keeps it: without it Chrome drops the cookie as it starts, and keeps the dated one.
    monkeypatch.setattr(session_module, "keep_session_cookies", lambda folder: None)
    dropped: str = await _sent_after_a_restart(config, tmp_path / "dropped", tmp_path / "downloads", site)
    assert "session_only" not in dropped and "dated=kept-anyway" in dropped, dropped
