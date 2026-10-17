"""The DevTools port of Dot's own Chrome (item 265), end to end on a real headless Chrome: with ``[browser] debug_port``
set, Dot's Chrome answers the DevTools Protocol on that port of this Mac, and a script connected there sees the tab a
run opened and works in a tab of its own while the session goes on; a port another program holds stops the start with an
error that names it, and no Chrome is left on the profile. Through the API of a served daemon the port is set, kept in
the config file, and refused when it is taken or out of range (below 1024 too); Settings › Browser shows and sets it in
Chrome, wide and on a phone, light and dark. A browser session that refuses the port leaves every session of the pool as
it was. Before, Dot's Chrome took no connection but Playwright's pipe.

No network and no model service is used.
"""

import asyncio
import json
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
import pytest
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright

from dotagent.agent.manager import RunManager
from dotagent.browser.devtools import devtools_address, port_is_taken
from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import ScriptRun
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config
from dotagent.core.errors import BrowserError
from dotagent.core.paths import ensure_layout
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

SERVER: ServerConfig = load_default_config().server
TOKEN: str = "devtools-port-test-token"
_PAGES: dict[str, str] = {
    "/": "<!doctype html><title>Home</title><h1>The run's page</h1>",
    "/other": "<!doctype html><title>Other</title><h1>The script's page</h1>",
}
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path: str = urlsplit(self.path).path
        body: bytes = _PAGES.get(path, "not found").encode()
        self.send_response(200 if path in _PAGES else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    directory: Path = tmp_path / "profile"
    directory.mkdir(mode=0o700)
    return directory


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _config(tmp_path: Path, port: int) -> BrowserConfig:
    settings: Path = tmp_path / "config.toml"
    settings.write_text(f"[browser]\nheadless = true\ndebug_port = {port}\n", encoding="utf-8")
    return load_config(settings).browser


async def test_dots_chrome_takes_devtools_connections_on_the_port(site: str, profile: Path, tmp_path: Path) -> None:
    config: BrowserConfig = _config(tmp_path, _free_port())
    address: str | None = devtools_address(config)
    assert address == f"http://127.0.0.1:{config.debug_port}"
    session: BrowserSession = BrowserSession(config, SERVER, profile, tmp_path / "downloads", no_runs_at_work)
    try:
        await session.open_url("run", f"{site}/")
        async with httpx.AsyncClient() as client:
            version: object = (await client.get(f"{address}/json/version")).json()
        assert isinstance(version, dict) and "Chrome" in str(version["Browser"]), version
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.connect_over_cdp(address)
            context: BrowserContext = browser.contexts[0]
            assert f"{site}/" in [page.url for page in context.pages]
            mine: Page = await context.new_page()
            await mine.goto(f"{site}/other")
            assert await mine.title() == "Other"
            await mine.close()
        # The script is gone and the run's tab is the session's still.
        title: ScriptRun = await session.run_script("run", "document.title", 10.0)
        assert (title.end, json.loads(title.text)) == ("value", "Home")
    finally:
        await session.aclose()


async def test_a_port_another_program_holds_stops_the_start_and_is_named(profile: Path, tmp_path: Path) -> None:
    assert not port_is_taken(_free_port())
    with socket.socket() as bound:
        # Bound without listening is held too: Chrome could not listen there.
        bound.bind(("127.0.0.1", 0))
        assert port_is_taken(int(bound.getsockname()[1]))
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port: int = int(holder.getsockname()[1])
        assert port_is_taken(port)
        session: BrowserSession = BrowserSession(
            _config(tmp_path, port), SERVER, profile, tmp_path / "downloads", no_runs_at_work
        )
        try:
            with pytest.raises(BrowserError, match=f"port {port} of this Mac is taken by another program"):
                await session.stored_cookie_count()
            assert profile_holder_pid(profile) is None
        finally:
            await session.aclose()


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[str]:
    """A daemon on the Dot home ``home``, its own Chrome hidden, at the address it yields."""
    (home / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    manager: RunManager = RunManager(runtime)
    try:
        stopping: asyncio.Event = asyncio.Event()
        listener = bind_listener("127.0.0.1", 0)
        port: int = listener.getsockname()[1]
        context: ServerContext = ServerContext(
            runtime=runtime,
            manager=manager,
            scheduler=None,
            token=TOKEN,
            host="127.0.0.1",
            port=port,
            started_at=time.time(),
            stopping=stopping,
        )
        server: DotServer = DotServer(serving_config(create_app(context)), stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def test_the_api_sets_the_port_keeps_it_and_refuses_a_taken_one(own_home: Path) -> None:
    async with (
        _served(own_home) as address,
        httpx.AsyncClient(
            base_url=f"{address}/api", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30.0
        ) as client,
    ):
        before: dict[str, object] = (await client.get("/browser")).json()
        assert (before["debug_port"], before["devtools_address"]) == (0, None)
        port: int = _free_port()
        chosen: httpx.Response = await client.post("/browser/debug-port", json={"debug_port": port})
        assert chosen.status_code == 200, chosen.text
        assert (chosen.json()["debug_port"], chosen.json()["devtools_address"]) == (port, f"http://127.0.0.1:{port}")
        assert f"debug_port = {port}\n" in (own_home / "config.toml").read_text(encoding="utf-8")
        with socket.socket() as holder:
            holder.bind(("127.0.0.1", 0))
            holder.listen()
            taken: int = int(holder.getsockname()[1])
            refused: httpx.Response = await client.post("/browser/debug-port", json={"debug_port": taken})
            assert refused.status_code == 422 and f"port {taken} of this Mac is taken" in refused.text
        assert (await client.get("/browser")).json()["debug_port"] == port
        assert f"debug_port = {port}\n" in (own_home / "config.toml").read_text(encoding="utf-8")
        for out_of_range in (80, 70000):
            assert (await client.post("/browser/debug-port", json={"debug_port": out_of_range})).status_code == 422
        off: httpx.Response = await client.post("/browser/debug-port", json={"debug_port": 0})
        assert (off.json()["debug_port"], off.json()["devtools_address"]) == (0, None)
        assert "debug_port = 0\n" in (own_home / "config.toml").read_text(encoding="utf-8")


async def test_settings_shows_the_port_and_sets_it(own_home: Path, tmp_path: Path) -> None:
    port: int = _free_port()
    async with _served(own_home) as address, async_playwright() as playwright:
        host, served_port = "127.0.0.1", int(address.rpartition(":")[2])
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url(host, served_port, TOKEN))
                await page.goto(f"{address}/#/settings/browser")
                field: Locator = page.get_by_role("textbox", name="DevTools port")
                await field.wait_for()
                await page.wait_for_function(
                    "() => { const f = document.querySelector('[aria-label=\"DevTools port\"]');"
                    " return f !== null && !f.disabled; }"
                )
                assert await field.input_value() == "0"
                setting: Locator = page.locator(".setting", has=field)
                assert "does not start while another program holds the port" in (await setting.text_content() or "")
                assert await setting.evaluate("(row) => row.getBoundingClientRect().right <= window.innerWidth")
                await field.fill(str(port))
                await field.press("Enter")
                await setting.get_by_text(f"http://127.0.0.1:{port}").wait_for()
                assert f"debug_port = {port}\n" in (own_home / "config.toml").read_text(encoding="utf-8")
                await page.screenshot(path=tmp_path / f"devtools-port-{width}-{scheme}.png")
                await field.fill("0")
                await field.press("Enter")
                await setting.get_by_text("does not start while another program holds the port").wait_for()
                assert "debug_port = 0\n" in (own_home / "config.toml").read_text(encoding="utf-8")
                await page.close()
        finally:
            await browser.close()
