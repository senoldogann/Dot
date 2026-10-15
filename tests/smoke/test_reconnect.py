"""Every way a page loses the daemon comes back by itself (item 200). A page open while the daemon restarts says that it
reconnects, is live again once the daemon is back and reads what changed meanwhile, without loading again. A page loaded
while the daemon is down (from the service worker's copy of the shell) says that the daemon is out of reach and tries
again by itself until it is back. A stream that goes silent without closing, as a network that changed or a Mac that
slept leaves it, is cut and followed again; a phone back from the background cuts such a stream at once and says
nothing of so short a break (item 286). A real server, stopped and started again on its address, in Chrome."""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Error, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "reconnect-token"
# The longest a restart of the daemon took from its stop to its start, in its own log.
_RESTART_S: float = 5.4
# Long enough for the page to try again after the daemon is back: its pauses grow to 8 s for a stream, 30 s on the
# screen of a daemon out of reach, and both try at once when the page comes back into view.
_BACK_WAIT_MS: float = 20_000
# A stream silent this long is cut (STALL_MS in static/js/api.js), and then made again.
_STALL_WAIT_MS: float = 60_000
# The pauses of a stream between tries grow from half a second to 1, 2 and 4 s (RECONNECT_MIN_MS in static/js/api.js):
# after this long without the daemon, the page waits its longest pause, 8 s.
_LONGEST_PAUSE_AFTER_S: float = 8.0
# Far shorter than what is left of the pause a page is in when it is told to try at once.
_AT_ONCE_MS: float = 3_000
_READ_CHUNK: int = 65_536
# Narrower than this, the side menu is a drawer that its button opens.
_DRAWER_BELOW: int = 860
_STREAM_REQUEST: bytes = b"GET /api/stream"
# Longer than a page away may leave its stream silent before it cuts it once back in view (BACK_SILENCE_MS in
# static/js/api.js), shorter than the silence that cuts a stream in any case (STALL_MS).
_AWAY_MS: int = 25_000
# Put the page out of sight or back in view, as a phone does when the app is put away and taken up again.
_SIGHT: str = """(state) => {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}"""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home, so that the chats are only the ones made here."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        '[browser]\nheadless = true\n\n[models]\nmain = "ollama:scripted"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


class Daemon:
    """A real server over ``runtime`` at one address, stopped and started again there as a restart does it: each start
    has a run manager of its own, as a new process has. ``named`` is the port the pages ask for when a proxy stands in
    front of it (the server answers only to its own address), ``None`` when they reach it directly."""

    def __init__(self, runtime: Runtime, named: int | None) -> None:
        self._runtime: Runtime = runtime
        self._named: int | None = named
        self.port: int = 0
        self._running: tuple[DotServer, asyncio.Event, asyncio.Task[None], RunManager] | None = None

    @property
    def address(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        manager: RunManager = RunManager(self._runtime)
        stopping: asyncio.Event = asyncio.Event()
        listener = bind_listener("127.0.0.1", self.port)
        self.port = listener.getsockname()[1]
        context: ServerContext = ServerContext(
            runtime=self._runtime,
            manager=manager,
            scheduler=None,
            token=TOKEN,
            host="127.0.0.1",
            port=self.port if self._named is None else self._named,
            started_at=time.time(),
            stopping=stopping,
        )
        server: DotServer = DotServer(serving_config(create_app(context)), stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        self._running = (server, stopping, serving, manager)

    async def stop(self) -> None:
        assert self._running is not None, "the daemon is not running"
        server, stopping, serving, manager = self._running
        self._running = None
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()

    @property
    def running(self) -> bool:
        return self._running is not None


@pytest_asyncio.fixture(loop_scope="module")
async def daemon(runtime: Runtime) -> AsyncIterator[Daemon]:
    started: Daemon = Daemon(runtime, None)
    await started.start()
    try:
        yield started
    finally:
        if started.running:
            await started.stop()


class Proxy:
    """A TCP proxy in front of the daemon at ``target``: ``freeze()`` stops passing the bytes of the connections that
    carry a lifecycle stream, without closing them, as a network that changed or a Mac that slept leaves a connection.
    The other connections, and the streams made after, pass. A connection the browser keeps open carries one request
    after another, so each request it sends is looked at, not only its first."""

    def __init__(self) -> None:
        self.target: int = 0
        self.port: int = 0
        self._streams: list[asyncio.Event] = []
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._carry, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        assert self._server is not None
        self._server.close()

    @property
    def streams(self) -> int:
        """How many lifecycle streams it has carried."""
        return len(self._streams)

    def freeze(self) -> None:
        for frozen in self._streams:
            frozen.set()

    async def _carry(self, client: asyncio.StreamReader, to_client: asyncio.StreamWriter) -> None:
        upstream, to_upstream = await asyncio.open_connection("127.0.0.1", self.target)
        frozen: asyncio.Event = asyncio.Event()

        def requested(chunk: bytes) -> None:
            if chunk.startswith(_STREAM_REQUEST):
                self._streams.append(frozen)

        await asyncio.gather(
            _pass(client, to_upstream, frozen, requested), _pass(upstream, to_client, frozen, lambda _chunk: None)
        )


async def _pass(
    source: asyncio.StreamReader, sink: asyncio.StreamWriter, frozen: asyncio.Event, seen: Callable[[bytes], None]
) -> None:
    """Pass the bytes of ``source`` to ``sink`` until either closes, showing each chunk to ``seen`` first; once
    ``frozen``, they are no longer passed."""
    try:
        while chunk := await source.read(_READ_CHUNK):
            seen(chunk)
            if frozen.is_set():
                continue
            sink.write(chunk)
            await sink.drain()
    except ConnectionError:
        pass
    finally:
        sink.close()


async def _chat(runtime: Runtime, title: str) -> ThreadRecord:
    """A chat with one run that is over, made in the store as a dotx command would make it, with no event to the page."""
    thread: ThreadRecord = await runtime.store.create_thread(title, "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        title,
        "chat",
        "ollama:scripted",
        None,
        "test",
        "medium",
        "standard",
        None,
        "ask",
        False,
        None,
        None,
        None,
    )
    await runtime.store.finish_run(run.id, "done", "Done.")
    return thread


async def _page(browser: Browser, port: int, width: int, scheme: Literal["light", "dark"]) -> tuple[Page, list[str]]:
    """A page signed in at ``port``, in a browser of its own, with the errors it throws."""
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
    )
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    return page, errors


def _chat_row(page: Page, title: str) -> Locator:
    return page.locator(".side-runs .side-chat").filter(has_text=title)


async def _keep_shell_copy(page: Page) -> None:
    """Let the service worker take the page; loaded again, the page leaves it a copy of the shell's files, as a phone
    that opened Dot before has one."""
    await page.wait_for_function("() => navigator.serviceWorker.controller !== null")
    await page.reload()
    await page.wait_for_function(
        "async () => (await (await caches.open('dot-shell-v1')).keys()).some((request) =>"
        " request.url.endsWith('/js/main.js'))"
    )


async def _open_side_menu(page: Page, width: int) -> None:
    """Open the side menu where it is a drawer, waiting for the page to have it as long as a reconnect may take."""
    if width < _DRAWER_BELOW:
        await page.get_by_role("button", name="Show sidebar").click(timeout=_BACK_WAIT_MS)


async def test_a_page_open_while_the_daemon_restarts_comes_back_by_itself_and_reads_what_changed(
    runtime: Runtime, daemon: Daemon, tmp_path: Path
) -> None:
    await _chat(runtime, "Sort the photos.")
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page, errors = await _page(browser, daemon.port, 1280, "light")
            await expect(_chat_row(page, "Sort the photos.")).to_be_visible()
            await page.evaluate("() => { window.dotStayed = true; }")

            # The daemon stops; while it is down a chat is made, and it starts again at the same address.
            await daemon.stop()
            await expect(page.locator(".live-dot.reconnecting")).to_be_visible()
            await page.screenshot(path=str(tmp_path / "restart-reconnecting-1280-light.png"), animations="disabled")
            await _chat(runtime, "Name the folders.")
            await asyncio.sleep(_RESTART_S)
            await daemon.start()

            # The page is live again by itself, shows the chat made meanwhile, and was not loaded again.
            await expect(page.locator(".live-dot.live")).to_be_attached(timeout=_BACK_WAIT_MS)
            await expect(_chat_row(page, "Name the folders.")).to_be_visible()
            assert await page.evaluate("() => window.dotStayed === true")
            assert errors == []
        finally:
            await browser.close()


@pytest.mark.parametrize(("width", "scheme"), [(1280, "light"), (390, "dark")])
async def test_a_page_loaded_while_the_daemon_is_down_tries_again_by_itself(
    runtime: Runtime, daemon: Daemon, tmp_path: Path, width: int, scheme: Literal["light", "dark"]
) -> None:
    await _chat(runtime, f"Tidy the notes at {width}.")
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page, errors = await _page(browser, daemon.port, width, scheme)
            await _keep_shell_copy(page)
            await _open_side_menu(page, width)
            await expect(_chat_row(page, f"Tidy the notes at {width}.")).to_be_visible()

            # Loaded while the daemon is down, the page says that it cannot reach it and when it tries again.
            await daemon.stop()
            await expect(page.locator(".live-dot.reconnecting")).to_be_visible()
            await page.reload()
            await expect(page.get_by_text("Cannot reach the Dot daemon")).to_be_visible()
            await expect(page.get_by_text("this page tries again by itself")).to_be_visible()
            await expect(page.get_by_text("Trying again in")).to_be_visible()
            await page.screenshot(path=str(tmp_path / f"unreachable-{width}-{scheme}.png"), animations="disabled")

            # Once the daemon is back, the page connects by itself.
            await daemon.start()
            await _open_side_menu(page, width)
            await expect(_chat_row(page, f"Tidy the notes at {width}.")).to_be_visible(timeout=_BACK_WAIT_MS)
            assert errors == []
        finally:
            await browser.close()


async def test_a_page_back_in_view_or_back_on_the_network_tries_at_once(runtime: Runtime, daemon: Daemon) -> None:
    await _chat(runtime, "Plan the trip.")
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page, errors = await _page(browser, daemon.port, 1280, "light")
            await _keep_shell_copy(page)
            await expect(_chat_row(page, "Plan the trip.")).to_be_visible()

            # The daemon is down long enough for the stream's pause between tries to grow to its longest.
            await daemon.stop()
            await expect(page.locator(".live-dot.reconnecting")).to_be_visible()
            await asyncio.sleep(_LONGEST_PAUSE_AFTER_S)
            await _chat(runtime, "Book the rooms.")
            await daemon.start()

            # Back in view, as after the Mac woke, the page tries at once and reads what changed.
            await page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
            await expect(page.locator(".live-dot.live")).to_be_attached(timeout=_AT_ONCE_MS)
            await expect(_chat_row(page, "Book the rooms.")).to_be_visible()

            # Loaded while the daemon is down, the page waits longer each time it finds it down; back on the network,
            # it tries at once.
            await daemon.stop()
            await expect(page.locator(".live-dot.reconnecting")).to_be_visible()
            await page.reload()
            await expect(page.get_by_text(re.compile(r"Trying again in [78] s\."))).to_be_visible(timeout=_BACK_WAIT_MS)
            await _chat(runtime, "Pack the bags.")
            await daemon.start()
            await page.evaluate("() => window.dispatchEvent(new Event('online'))")
            await expect(_chat_row(page, "Pack the bags.")).to_be_visible(timeout=_AT_ONCE_MS)
            assert errors == []
        finally:
            await browser.close()


async def test_a_stream_gone_silent_is_cut_and_followed_again(runtime: Runtime) -> None:
    proxy: Proxy = Proxy()
    await proxy.start()
    behind: Daemon = Daemon(runtime, proxy.port)
    await behind.start()
    proxy.target = behind.port
    await _chat(runtime, "Count the files.")
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page, errors = await _page(browser, proxy.port, 1280, "dark")
            await expect(_chat_row(page, "Count the files.")).to_be_visible()
            await expect(page.locator(".live-dot.live")).to_be_attached()
            assert proxy.streams == 1

            # The stream's connection stays open and passes nothing; a chat is made meanwhile.
            proxy.freeze()
            await _chat(runtime, "Merge the lists.")

            # The page cuts the silent stream, makes it again, and reads what changed.
            await expect(_chat_row(page, "Merge the lists.")).to_be_visible(timeout=_STALL_WAIT_MS)
            assert proxy.streams >= 2
            await expect(page.locator(".live-dot.live")).to_be_attached()
            assert errors == []
        finally:
            await browser.close()
            await behind.stop()
            await proxy.close()


async def test_a_phone_back_from_the_background_follows_again_at_once_and_quietly(runtime: Runtime) -> None:
    async with async_playwright() as playwright:
        # Chrome, and WebKit as Safari on the phone.
        for engine in ("chromium", "webkit"):
            proxy: Proxy = Proxy()
            await proxy.start()
            behind: Daemon = Daemon(runtime, proxy.port)
            await behind.start()
            proxy.target = behind.port
            await _chat(runtime, f"Water the plants in {engine}.")
            browser: Browser = await getattr(playwright, engine).launch(
                **({"channel": runtime.config.browser.channel} if engine == "chromium" else {}), headless=True
            )
            try:
                context: BrowserContext = await browser.new_context(
                    viewport={"width": 390, "height": 844}, color_scheme="light", reduced_motion="reduce"
                )
                page: Page = await context.new_page()
                errors: list[Error] = []
                page.on("pageerror", errors.append)
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.clock.install()
                await page.goto(ui_url("127.0.0.1", proxy.port, TOKEN))
                await _open_side_menu(page, 390)
                await expect(_chat_row(page, f"Water the plants in {engine}.")).to_be_visible()
                await expect(page.locator(".live-dot.live")).to_be_attached()
                assert proxy.streams == 1

                # Put away: the stream stays open and passes nothing, and a chat is made meanwhile.
                await page.evaluate(_SIGHT, "hidden")
                proxy.freeze()
                await _chat(runtime, f"Call the plumber in {engine}.")
                await page.clock.fast_forward(_AWAY_MS)

                # Back in view, the page cuts the silent stream, follows again at once and reads what changed, and
                # the break was too short for the bar to say that it reconnects.
                await page.evaluate(_SIGHT, "visible")
                await expect(_chat_row(page, f"Call the plumber in {engine}.")).to_be_visible(timeout=_AT_ONCE_MS)
                assert proxy.streams == 2, engine
                await expect(page.locator(".live-dot.live")).to_be_attached()
                await expect(page.locator(".pill:has(.live-dot)")).to_be_hidden()
                assert errors == [], engine
            finally:
                await browser.close()
                await behind.stop()
                await proxy.close()
