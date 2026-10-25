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
from playwright.async_api import Browser, Locator, Page, Request, Route, async_playwright, expect
from pydantic import JsonValue

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.memory.service import OwnerSection, owner_section, set_owner_section
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


async def _access_key_box(page: Page) -> Locator:
    """The box for Dot's access key: the sign-in code comes first, and the key is one of the other ways under it."""
    await page.get_by_text("Other ways to sign in").click()
    return page.get_by_label("Access key", exact=True)


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
            # The home shows the day (its now line, which these tests read) only when asked to (Settings › General).
            await opened.add_init_script("localStorage.setItem('dot.homeDay', 'shown')")
            # Dot has had its first conversation (item 420): the home shows what is going on, as these tests need.
            runtime.assistant.mark_introduced(True)
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
            await expect(opened.get_by_label("Sign-in code", exact=True)).to_be_visible()
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
    assert isinstance(told, list) and len(told) == 2, told
    refused, taken = told
    # However often the page tried the stream the server refused in that time (twice on a Mac, once on a slower
    # runner, item 486), it never said it was live; the one the server took is.
    assert isinstance(refused, list) and refused and set(refused) == {"reconnecting"}, told
    assert taken == ["live"], told


async def _answer_with_an_error(route: Route) -> None:
    """The answer of a daemon that runs and fails on an error it did not expect."""
    body: str = json.dumps(
        {
            "message": "Dot failed on an error it did not expect",
            "context": {"error": "PermissionError: [Errno 13] Permission denied: 'themes'"},
        }
    )
    await route.fulfill(status=500, content_type="application/json", body=body)


async def _answer_refused(route: Route) -> None:
    """The answer of a daemon that does not take the token the page sends."""
    await route.fulfill(status=401, content_type="application/json", body='{"message": "refused", "context": {}}')


async def test_a_daemon_that_answers_with_an_error_is_not_said_to_be_down(page: Page) -> None:
    """Item 178: when a read of the sign-in fails with an answer of the daemon, the page says that the daemon runs and
    shows the error; before, it asked to start the daemon that had answered."""
    await page.route("**/api/status", _answer_with_an_error)
    await page.reload()
    signin = page.locator(".signin")
    await expect(signin).to_contain_text("Dot is open, but it could not answer just now")
    await expect(signin).to_contain_text("PermissionError: [Errno 13] Permission denied")
    await expect(signin).not_to_contain_text("Could not reach Dot")


async def test_a_page_that_cannot_read_again_keeps_what_it_showed_and_says_so(runtime: Runtime, page: Page) -> None:
    """Item 178: the home reads what is going on again at each event of a run; a read that fails keeps what it showed
    and says so, as item 160 meant, where before it was written to the console only."""
    now = page.locator(".home-now")
    await expect(now).not_to_have_text("Looking around…")
    shown: str = await now.inner_text()
    await page.route("**/api/activity", _answer_with_an_error)
    await runtime.bus.emit("run-178", "run_started", {}, False)
    await expect(page.locator(".toast.error")).to_contain_text("What is going on could not be updated")
    await expect(page.locator(".toast.error")).to_contain_text("Dot failed on an error it did not expect")
    await expect(now).to_have_text(shown)


async def test_a_status_that_cannot_be_read_again_is_told_and_does_not_keep_the_approvals_from_showing(
    runtime: Runtime, page: Page
) -> None:
    """Item 559: the status and the approvals that wait are read again at each event; a read that failed was only
    written to the console, so the waiting count and the emergency stop's banner stood as they were, with the page
    saying it was live, and the one read that failed kept the other's answer out as well. It is told as the pages tell
    theirs, and the tab counts the question that came while the status could not be read."""
    await expect(page.locator(".home-now")).not_to_have_text("Looking around…")
    await page.route("**/api/status", _answer_with_an_error)
    asked = await runtime.store.create_approval("run-559", "shell", "touch x", "write", {"command": "touch x"})
    try:
        await runtime.bus.emit("run-559", "run_started", {}, False)
        await expect(page.locator(".toast.error", has_text="Dot's status could not be updated")).to_be_visible()
        await page.wait_for_function("() => document.title.startsWith('(1) ')")
    finally:
        await runtime.store.decide_approval(asked.id, "denied", "test", "")


async def test_an_address_with_a_token_the_daemon_does_not_hold_signs_nobody_out(
    runtime: Runtime, address: str
) -> None:
    """Item 559: any link to the daemon's address with ``#token=`` in it put that token in the browser's storage, and
    the proof that failed took it out again, with the token that was there: every tab of the browser signed out, and a
    phone's home-screen app to pair anew. The token of an address is kept once the daemon has proven that it holds it,
    and one it does not hold gives way to the token the browser has; with none to go on, it is the sign-in screen."""
    port: int = int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            context = await browser.new_context(service_workers="block")
            signed: Page = await context.new_page()
            await signed.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await signed.goto(ui_url("127.0.0.1", port, TOKEN))
            await expect(signed.locator("main.page")).to_be_visible()
            assert await signed.evaluate("localStorage.getItem('dot.token')") == TOKEN
            # A link another page made, with a token of its own, in the same browser.
            linked: Page = await context.new_page()
            await linked.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await linked.goto(ui_url("127.0.0.1", port, "a-token-the-daemon-never-made"))
            await expect(linked.locator(".toast.error", has_text="is not the one Dot uses")).to_be_visible()
            await expect(linked.locator("main.page")).to_be_visible()
            await expect(linked.get_by_label("Access key", exact=True)).to_have_count(0)
            assert "token" not in linked.url, linked.url
            assert await linked.evaluate("localStorage.getItem('dot.token')") == TOKEN
            await context.close()
            # A browser with no token of its own has nothing to go on: it is asked for one, and keeps nothing.
            bare = await (await browser.new_context(service_workers="block")).new_page()
            await bare.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await bare.goto(ui_url("127.0.0.1", port, "a-token-the-daemon-never-made"))
            await expect(bare.get_by_label("Sign-in code", exact=True)).to_be_visible()
            assert await bare.evaluate("localStorage.getItem('dot.token')") is None
        finally:
            await browser.close()


async def test_a_page_loaded_again_while_its_token_is_being_proven_tries_it_again(
    runtime: Runtime, address: str
) -> None:
    """Item 559: the token of an address is kept once the daemon has proven that it holds it, and the page takes it out
    of the address as it opens, so a page loaded again before the proof came had it nowhere and asked for it. The tab
    keeps the token it is trying until it is proven or refused."""
    port: int = int(address.rpartition(":")[2])
    asked: asyncio.Event = asyncio.Event()
    given: asyncio.Event = asyncio.Event()

    async def hold_the_proof(route: Route) -> None:
        asked.set()
        await given.wait()
        await route.continue_()

    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            opened: Page = await (await browser.new_context(service_workers="block")).new_page()
            await opened.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await opened.route(_PROOF, hold_the_proof)
            await opened.goto(ui_url("127.0.0.1", port, TOKEN))
            await asyncio.wait_for(asked.wait(), 30)
            # Loaded again before the daemon was heard: the address no longer has the token.
            assert "token" not in opened.url, opened.url
            await opened.reload()
            given.set()
            await expect(opened.locator("main.page")).to_be_visible()
            assert await opened.evaluate("localStorage.getItem('dot.token')") == TOKEN
            assert await opened.evaluate("sessionStorage.getItem('dot.tokenTrying')") is None
        finally:
            await browser.close()


async def test_your_lines_are_not_saved_before_they_are_read_nor_after_they_could_not_be(
    runtime: Runtime, page: Page
) -> None:
    """Item 559: a save of Your lines replaces them all with what is in the box, so one made before they were read, or
    after the read failed and left the box empty, wiped what the owner wrote before. Settings › Memory draws what Dot
    knows about you only once the lines are read (item 566): while they come, and after a read that fails, there is no
    box and no Save."""
    kept: str = "I read Turkish; answer me in Turkish."
    await set_owner_section(runtime.store, OwnerSection(dot_lines=(), owner_lines=(kept,), updated_at=time.time()))
    section = page.locator("section.set-section").filter(has_text="What Dot knows about you")
    lines = section.get_by_label("Your lines", exact=True)
    save = section.get_by_role("button", name="Save your lines")
    # Slow: nothing of it is shown while the lines are on their way, and the box holds them once they come.
    arrive: asyncio.Event = asyncio.Event()

    async def late(route: Route) -> None:
        await arrive.wait()
        await route.continue_()

    await page.route("**/api/memories/owner", late)
    await page.evaluate("location.hash = '#/settings/memory'")
    await expect(page.locator("section.notes-section")).to_be_visible()
    await expect(save).to_be_hidden()
    arrive.set()
    await expect(lines).to_have_value(kept)
    await expect(save).to_be_enabled()
    await page.unroute("**/api/memories/owner", late)
    # A read that fails: no box and no Save, so an empty one cannot be saved over the lines.
    await page.route("**/api/memories/owner", _answer_with_an_error)
    await page.reload()
    await expect(page.locator("section.notes-section")).to_be_visible()
    await expect(lines).to_be_hidden()
    await expect(save).to_be_hidden()
    await page.unroute("**/api/memories/owner", _answer_with_an_error)
    await page.reload()
    await expect(lines).to_have_value(kept)
    await lines.fill(f"{kept}\nI like short answers.")
    await save.click()
    await expect(page.locator(".toast.ok", has_text="Your lines are kept")).to_be_visible()
    assert (await owner_section(runtime.store)).owner_lines == (kept, "I like short answers.")


# What main.js adds to the document and to the phone's media query while a session is on, with the signal each was
# given: every one is read from the call, by the file it was made in.
_WATCH_LISTENERS: str = """() => {
  window.__listeners = [];
  const add = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (type, listener, options) {
    const target = this === document ? "document" : this instanceof MediaQueryList ? "media" : "other";
    const made = (new Error().stack ?? "").split("\\n").find((line) => line.includes("/js/")) ?? "";
    if (made.includes("/js/main.js") && target !== "other" && (type === "click" || type === "change")) {
      const signal = options !== null && typeof options === "object" ? (options.signal ?? null) : null;
      window.__listeners.push({ type, target, signal });
    }
    return add.call(this, type, listener, options);
  };
}"""


async def test_the_listeners_a_session_adds_end_with_it(runtime: Runtime, address: str) -> None:
    """Item 559: a token the daemon refuses ends the session without loading the page again, and signing in again
    begins another; the click and the phone's media query listeners of the first stayed for the life of the tab, a
    pair more for each sign-in. Each is given the session's signal now, which ends when it does."""
    port: int = int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            opened: Page = await (await browser.new_context(service_workers="block")).new_page()
            await opened.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await opened.add_init_script(f"({_WATCH_LISTENERS})()")
            await opened.goto(ui_url("127.0.0.1", port, TOKEN))
            await expect(opened.locator("main.page")).to_be_visible()
            # The daemon refuses the token once: the session ends, and the page asks for a token.
            await opened.route("**/api/status", _answer_refused)
            await runtime.bus.emit("run-559", "run_started", {}, False)
            sign_in = await _access_key_box(opened)
            await expect(sign_in).to_be_visible()
            await opened.unroute("**/api/status", _answer_refused)
            await sign_in.fill(TOKEN)
            await opened.get_by_role("button", name="Sign in with the key", exact=True).click()
            await expect(opened.locator("main.page")).to_be_visible()
            listeners: list[dict[str, JsonValue]] = await opened.evaluate(
                """() => window.__listeners.map((one) => ({
                    type: one.type, target: one.target, scoped: one.signal !== null,
                    ended: one.signal !== null && one.signal.aborted,
                }))"""
            )
            # One of each for each session, each scoped to its session; the first session's have ended.
            assert len(listeners) == 4, listeners
            assert all(one["scoped"] for one in listeners), listeners
            assert [one["ended"] for one in listeners] == [True, True, False, False], listeners
        finally:
            await browser.close()
