"""A tab's console and its size (item 746), end to end in a real headless Chrome on pages a local fixture server
serves: what a page wrote to its console and the errors it did not catch are listed newest first, by level, cut short,
its secrets hidden outside `yolo`, and a new page starts the list again; a tab given a phone's width lays its page out
for it and goes back; the open Chrome of the owner is never resized. As tools both are read-only ones, a research
run's too, and what the console gives back is fenced as the page's and marks the run untrusted."""

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from test_browser_attach import ScratchChrome, start_chrome, stop_chrome
from test_browser_eval import _run_context

from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import FindResult, Screenshot, ScriptRun, Viewport
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config
from dotagent.core.errors import BrowserError
from dotagent.policy.engine import preset_verdict
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.tools.base import PreparedCall, ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.registry import tools_for_mode

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

SERVER: ServerConfig = load_default_config().server
_PAGES: dict[str, str] = {
    "/app": """<!doctype html><title>App</title><p>An app</p>
<script>
console.log("started");
console.warn("slow font");
console.error("load failed for /api?access_token=abcdef0123456789");
console.log("x".repeat(5000));
setTimeout(() => { throw new Error("late failure"); }, 50);
</script>""",
    "/fit": """<!doctype html><title>Fit</title><meta name="viewport" content="width=device-width">
<p id="layout"></p>
<script>
const show = () => {
  document.getElementById("layout").textContent = innerWidth <= 600 ? "phone layout" : "wide layout";
};
addEventListener("resize", show);
show();
</script>""",
    "/other": "<!doctype html><title>Other</title><p>Quiet</p>",
}
_PHONE: Viewport = Viewport(width=390, height=844)


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


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _console(browser: BrowserSession, thread: str, hide: bool, level: str | None = None) -> FindResult:
    """The console of the thread's tab once the page's late error is in it."""
    deadline: float = time.monotonic() + 10.0
    while True:
        found: FindResult = await browser.console_messages(thread, level, 100, hide)
        if any("late failure" in line for line in found.hits) or time.monotonic() > deadline:
            return found
        await asyncio.sleep(0.1)


async def test_the_console_and_the_uncaught_errors_of_a_page_are_listed_newest_first(
    browser: BrowserSession, site: str
) -> None:
    await browser.open_url("console", f"{site}/app", True)
    found: FindResult = await _console(browser, "console", True)
    assert found.url == f"{site}/app"
    levels: list[str] = [line.split(" ")[0] for line in found.hits]
    assert levels == ["error", "log", "error", "warning", "log"], found.hits
    assert found.hits[0].startswith("error uncaught Error: late failure"), found.hits
    assert found.hits[3] == f"warning slow font ({site}/app:3)", found.hits
    errors: FindResult = await browser.console_messages("console", "error", 100, True)
    assert [line.split(" ")[1] for line in errors.hits] == ["uncaught", "load"], errors.hits
    assert (await browser.console_messages("console", "error", 1, True)).hits == errors.hits[:1]
    assert (await browser.console_messages("console", "debug", 100, True)).hits == ()


async def test_a_message_is_cut_short_and_its_secrets_are_hidden_outside_yolo(
    browser: BrowserSession, site: str
) -> None:
    await browser.open_url("secrets", f"{site}/app", True)
    hidden: FindResult = await _console(browser, "secrets", True)
    assert all("abcdef0123456789" not in line for line in hidden.hits), hidden.hits
    assert any("access_token=[hidden]" in line for line in hidden.hits), hidden.hits
    assert all(len(line) < 500 for line in hidden.hits), [len(line) for line in hidden.hits]
    whole: FindResult = await browser.console_messages("secrets", None, 100, False)
    assert any("access_token=abcdef0123456789" in line for line in whole.hits), whole.hits


async def test_a_page_that_is_loaded_again_starts_its_console_again(browser: BrowserSession, site: str) -> None:
    await browser.open_url("again", f"{site}/app", True)
    written: FindResult = await _console(browser, "again", True)
    assert written.hits
    # A route of the page's own scripts is the same page: what it wrote stays, for the error a route change explains.
    await browser.run_script("again", "history.pushState({}, '', '/app/route'); location.hash = 'part'", 10.0, False)
    assert (await _console(browser, "again", True)).hits == written.hits
    await browser.open_url("again", f"{site}/other", True)
    assert (await browser.console_messages("again", None, 100, True)).hits == ()


async def test_a_tab_given_a_phones_width_lays_its_page_out_for_it_and_goes_back(
    browser: BrowserSession, site: str, browser_config: BrowserConfig
) -> None:
    script: str = "[innerWidth, innerHeight, layout.textContent]"
    await browser.open_url("phone", f"{site}/fit", True)
    wide: ScriptRun = await browser.run_script("phone", script, 10.0, False)
    assert wide.text == f'[{browser_config.viewport_width}, {browser_config.viewport_height}, "wide layout"]'
    assert await browser.set_viewport("phone", 390, None) == _PHONE
    phone: ScriptRun = await browser.run_script("phone", script, 10.0, False)
    assert phone.text == '[390, 844, "phone layout"]'
    shot: Screenshot = await browser.screenshot("phone", True)
    assert (shot.width, shot.height) == (390, 844)
    # The tab keeps the size across a page that is loaded again, and another tab is not given it.
    await browser.open_url("phone", f"{site}/fit", True)
    assert (await browser.run_script("phone", "innerWidth", 10.0, False)).text == "390"
    await browser.open_url("other-chat", f"{site}/fit", True)
    other: ScriptRun = await browser.run_script("other-chat", "innerWidth", 10.0, False)
    assert other.text == str(browser_config.viewport_width)
    # Another size is asked from the one the tab had at first, which no step of the way loses.
    assert await browser.set_viewport("phone", 768, 1024) == Viewport(width=768, height=1024)
    back: Viewport = await browser.set_viewport("phone", None, None)
    assert back == Viewport(width=browser_config.viewport_width, height=browser_config.viewport_height)
    after: ScriptRun = await browser.run_script("phone", "[innerWidth, layout.textContent]", 10.0, False)
    assert after.text == f'[{browser_config.viewport_width}, "wide layout"]'


async def test_a_tab_that_fitted_its_window_fits_it_again_after_a_phones_width(site: str, tmp_path: Path) -> None:
    settings: Path = tmp_path / "fit.toml"
    await asyncio.to_thread(settings.write_text, "[browser]\nheadless = true\nfit_window = true\n", "utf-8")
    session: BrowserSession = BrowserSession(
        load_config(settings).browser, SERVER, tmp_path / "profile", tmp_path / "downloads", no_runs_at_work
    )
    try:
        await session.open_url("fit", f"{site}/fit", True)
        # A screenshot at the phone's size, as a run takes one, does not keep the tab from fitting its window after.
        size: str = "[innerWidth, innerHeight]"
        before: str = (await session.run_script("fit", size, 10.0, False)).text
        assert await session.set_viewport("fit", 390, None) == _PHONE
        await session.screenshot("fit", True)
        await session.set_viewport("fit", None, None)
        assert (await session.run_script("fit", size, 10.0, False)).text == before
        # It follows its window again, on a page of another site too, which another renderer draws.
        await session.open_url("fit", f"{site.replace('127.0.0.1', 'localhost')}/fit", True)
        assert (await session.run_script("fit", size, 10.0, False)).text == before
    finally:
        await session.aclose()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    directory: Path = tmp_path / "chrome"
    directory.mkdir(mode=0o700)
    return directory


@pytest.fixture
def attach_config(tmp_path: Path) -> BrowserConfig:
    settings: Path = tmp_path / "attach.toml"
    settings.write_text('[browser]\nmode = "attach"\nattach_timeout_s = 10.0\n', encoding="utf-8")
    return load_config(settings).browser


async def test_the_open_chrome_of_the_owner_is_never_resized(
    site: str, data_dir: Path, attach_config: BrowserConfig, tmp_path: Path
) -> None:
    chrome: ScratchChrome = await start_chrome(data_dir, f"{site}/other")
    session: BrowserSession = BrowserSession(attach_config, SERVER, data_dir, tmp_path / "downloads", no_runs_at_work)
    try:
        await session.open_url("agent", f"{site}/fit", True)
        with pytest.raises(BrowserError, match="the open Chrome of the owner"):
            await session.set_viewport("agent", 390, None)
        measured: ScriptRun = await session.run_script("agent", "innerWidth", 10.0, False)
        assert measured.text != "390"
        # Reading its console is a read, and is allowed there.
        assert (await session.console_messages("agent", None, 10, True)).url == f"{site}/fit"
    finally:
        await session.aclose()
        await stop_chrome(chrome)


async def _wait_for_late_error(ctx: ToolContext, hide: bool) -> None:
    deadline: float = time.monotonic() + 10.0
    while "late failure" not in "".join((await ctx.browser.console_messages(ctx.thread_id, None, 100, hide)).hits):
        assert time.monotonic() < deadline
        await asyncio.sleep(0.1)


async def test_both_are_read_only_tools_a_research_run_has_and_the_console_is_the_pages_words(
    runtime: Runtime, site: str
) -> None:
    for mode in ("research", "task"):
        names: set[str] = {tool.spec.name for tool in tools_for_mode(runtime.registry, mode)}
        assert {"browser_console", "browser_viewport"} <= names, mode
    tools = runtime.registry.tools
    ctx: ToolContext = await _run_context(runtime)
    await ctx.browser.open_url(ctx.thread_id, f"{site}/app", True)
    await _wait_for_late_error(ctx, True)
    console: PreparedCall = await tools["browser_console"].prepare({"level": "error"}, ctx)
    action: ActionInfo = console.action
    assert (action.tool, action.kind, action.risk, action.target, action.flags) == (
        "browser_console",
        "browser_read",
        "safe",
        f"{site}/app",
        (),
    )
    assert [preset_verdict(preset, action)[0] for preset in ("aggressive", "balanced", "cautious")] == ["allow"] * 3
    read: ToolResult = await console.run(ctx)
    assert (read.is_error, read.untrusted) == (False, True)
    assert "<web_content" in read.text and "late failure" in read.text and "slow font" not in read.text
    assert "abcdef0123456789" not in read.text and "access_token=[hidden]" in read.text, read.text
    # A yolo run reads all it is shown (item 443).
    yolo: ToolContext = replace(await _run_context(runtime), approvals="yolo")
    await yolo.browser.open_url(yolo.thread_id, f"{site}/app", True)
    await _wait_for_late_error(yolo, False)
    whole: ToolResult = await (await tools["browser_console"].prepare({}, yolo)).run(yolo)
    assert "access_token=abcdef0123456789" in whole.text
    quiet: ToolContext = await _run_context(runtime)
    await quiet.browser.open_url(quiet.thread_id, f"{site}/other", True)
    none: ToolResult = await (await tools["browser_console"].prepare({}, quiet)).run(quiet)
    assert (none.is_error, none.untrusted, "no console message" in none.text) == (False, False, True)

    viewport: PreparedCall = await tools["browser_viewport"].prepare({"width": 390}, ctx)
    assert (viewport.action.tool, viewport.action.kind, viewport.action.risk) == (
        "browser_viewport",
        "browser_read",
        "safe",
    )
    sized: ToolResult = await viewport.run(ctx)
    assert (sized.is_error, "390x844 CSS pixels" in sized.text) == (False, True), sized.text
    gone: ToolResult = await (await tools["browser_viewport"].prepare({}, ctx)).run(ctx)
    assert "back to its size" in gone.text, gone.text
