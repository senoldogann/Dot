"""Guards of the web UI (item 139): a note sent twice from the keyboard before the first save is answered is kept once,
and an answer of the daemon's address that is not JSON (a proxy's page) is an error that names the path and the status,
not a bare parse error. The page sends its token only to a daemon that has proven it holds it (item 143), and says a
stream is live only once the server has taken it (item 144)."""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, Request, Route, async_playwright, expect
from pydantic import JsonValue

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "page-guards-token"
NOTE: str = "I prefer short answers."
# Long enough for a second save, sent with the first, to be sent and stored too.
_SETTLE_S: float = 0.5
_PROOF: re.Pattern[str] = re.compile(r"/api/proof\?")
# Long enough for the page to try a stream that is not answered twice (it waits 0.5 s, then 1 s).
_RETRIES_MS: int = 1200


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home whose notes are found by their words, so that no model service is needed."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n',
        encoding="utf-8",
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def address(runtime: Runtime) -> AsyncIterator[str]:
    """The address of a real server over the runtime."""
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


@pytest_asyncio.fixture(loop_scope="module")
async def page(runtime: Runtime, address: str) -> AsyncIterator[Page]:
    """A page of the UI, signed in, on its home."""
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            # Without the service worker, so that the answers a test puts in place are the ones the page gets.
            opened: Page = await (await browser.new_context(service_workers="block")).new_page()
            await opened.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await opened.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            yield opened
        finally:
            await browser.close()


async def test_a_note_sent_twice_from_the_keyboard_is_kept_once(runtime: Runtime, page: Page) -> None:
    # The page itself sends one save: the daemon's own check for a note it has is not what keeps the second out.
    saves: list[Request] = []

    def keep_save(request: Request) -> None:
        if request.method == "POST" and urlsplit(request.url).path == "/api/memories":
            saves.append(request)

    page.on("request", keep_save)
    await page.evaluate("location.hash = '#/memory'")
    await page.get_by_label("Note", exact=True).fill(NOTE)
    tags = page.get_by_label("Tags", exact=True)
    await tags.fill("preferences")
    # Two presses of Enter before the first save is answered, as a quick double press gives.
    await tags.evaluate(
        "node => { for (let press = 0; press < 2; press += 1) {"
        " node.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })); } }"
    )
    await expect(page.get_by_label("Note", exact=True)).to_have_value("")
    await asyncio.sleep(_SETTLE_S)
    assert len(saves) == 1
    assert [note.content for note in await runtime.store.list_memories(10, 0)] == [NOTE]


async def test_an_answer_that_is_not_json_is_an_error_naming_its_path(page: Page) -> None:
    async def proxy_page(route: Route) -> None:
        await route.fulfill(status=200, content_type="text/html", body="<html>a proxy's page</html>")

    await page.route("**/api/not-json", proxy_page)
    caught: JsonValue = await page.evaluate(
        """async () => {
            const { api } = await import("/js/api.js");
            try {
                await api.get("/not-json");
                return null;
            } catch (error) {
                return [error.name, error.status, error.context.path, error.context.start];
            }
        }"""
    )
    assert caught == ["ApiError", 200, "/not-json", "<html>a proxy's page</html>"]


async def test_the_page_sends_its_token_only_to_a_daemon_that_proves_it_holds_it(
    runtime: Runtime, address: str
) -> None:
    """A program on the daemon's port answers the page's nonce with a proof that is not the token's: the page, opened
    with the token, sends it in no request and asks for one. The daemon, once it answers, is sent it."""
    port: int = int(address.rpartition(":")[2])
    carried: list[str] = []

    def keep_carried(request: Request) -> None:
        if "authorization" in request.headers:
            carried.append(urlsplit(request.url).path)

    async def stranger(route: Route) -> None:
        await route.fulfill(status=200, content_type="application/json", body=json.dumps({"proof": "00" * 32}))

    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            opened: Page = await (await browser.new_context(service_workers="block")).new_page()
            await opened.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            opened.on("request", keep_carried)
            await opened.route(_PROOF, stranger)
            await opened.goto(ui_url("127.0.0.1", port, TOKEN))
            await expect(opened.get_by_label("Access token", exact=True)).to_be_visible()
            assert carried == []
            await opened.unroute(_PROOF, stranger)
            answered: JsonValue = await opened.evaluate(
                """async (token) => {
                    const { api, setToken } = await import("/js/api.js");
                    setToken(token);
                    return (await api.get("/status")).port;
                }""",
                TOKEN,
            )
            assert answered == port
            assert "/api/status" in carried
        finally:
            await browser.close()


async def test_a_stream_is_said_to_be_live_only_once_the_server_has_taken_it(page: Page) -> None:
    """Item 144: a stream the server does not take is never said to be live, however often the page tries it again;
    one it takes is, once it is connected."""

    async def unavailable(route: Route) -> None:
        await route.fulfill(
            status=503, content_type="application/json", body='{"message": "unavailable", "context": {}}'
        )

    await page.route("**/api/unavailable-stream*", unavailable)
    told: JsonValue = await page.evaluate(
        """async (waitMs) => {
            const { follow } = await import("/js/api.js");
            const statesOf = async (path) => {
                const states = [];
                const controller = new AbortController();
                const following = follow({
                    path,
                    after: 0,
                    signal: controller.signal,
                    onMessage: () => undefined,
                    onState: (state) => states.push(state),
                    isFinished: () => false,
                });
                await new Promise((resolve) => setTimeout(resolve, waitMs));
                controller.abort();
                await following;
                return states;
            };
            return [await statesOf("/unavailable-stream"), await statesOf("/stream")];
        }""",
        _RETRIES_MS,
    )
    assert told == [["reconnecting", "reconnecting"], ["live"]]


async def _answer_with_an_error(route: Route) -> None:
    """The answer of a daemon that runs and fails on an error it did not expect."""
    body: str = json.dumps(
        {
            "message": "Dot failed on an error it did not expect",
            "context": {"error": "PermissionError: [Errno 13] Permission denied: 'themes'"},
        }
    )
    await route.fulfill(status=500, content_type="application/json", body=body)


async def test_a_daemon_that_answers_with_an_error_is_not_said_to_be_down(page: Page) -> None:
    """Item 178: when a read of the sign-in fails with an answer of the daemon, the page says that the daemon runs and
    shows the error; before, it asked to start the daemon that had answered."""
    await page.route("**/api/status", _answer_with_an_error)
    await page.reload()
    signin = page.locator(".signin")
    await expect(signin).to_contain_text("The daemon is running but could not answer")
    await expect(signin).to_contain_text("PermissionError: [Errno 13] Permission denied")
    await expect(signin).not_to_contain_text("Make sure the daemon is running")


async def test_a_page_that_cannot_read_again_keeps_what_it_showed_and_says_so(runtime: Runtime, page: Page) -> None:
    """Item 178: the home reads what is going on again at each event of a run; a read that fails keeps what it showed
    and says so, as item 160 meant, where before it was written to the console only."""
    now = page.locator(".home-now")
    await expect(now).not_to_have_text("Looking around…")
    shown: str = await now.inner_text()
    await page.route("**/api/activity", _answer_with_an_error)
    await runtime.bus.emit("run-178", "run_started", {}, False)
    await expect(page.locator(".toast.error")).to_contain_text(
        "could not be read again: Dot failed on an error it did not expect"
    )
    await expect(now).to_have_text(shown)
