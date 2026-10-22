"""A script run in the page of a run's own tab (item 263), end to end: a real headless Chrome drives pages served by a
local fixture server, in Dot's own Chrome and in a scratch Chrome attached as the Chrome of the user.

A script gives the value of an expression, of a function and of a promise as JSON; each thread runs it in its own tab;
an error and a wait that ends first are told apart, also for a script that keeps the page busy, and the page is usable
after them; a script that leads the tab elsewhere is let land and says so, and one that leads it to an address the rules
forbid has the tab reset and fails, also when that page answers only after the script's value came back (item 270).
As a tool of a run it is a click of medium risk, which the cautious preset asks about and a research run does not
have, and what it gives back is fenced as the page's and marked untrusted, a thrown error as a failure.
"""

import asyncio
import json
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

from dotagent.agent.gate import RunGate
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.agent.verify import new_facts
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import ScriptRun
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config
from dotagent.core.errors import BrowserError
from dotagent.core.paths import workspace_dir
from dotagent.policy.engine import preset_verdict
from dotagent.policy.types import ActionInfo, Preset
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import RunRecord, ThreadRecord
from dotagent.tools.base import PreparedCall, ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.registry import tools_for_mode

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

SERVER: ServerConfig = load_default_config().server
GOAL: str = "Read the names on the page."
_PRESETS: tuple[Preset, ...] = ("aggressive", "balanced", "cautious")
_PAGES: dict[str, str] = {
    "/": """<!doctype html><title>Eval page</title>
<ul><li>Ada</li><li>Grace</li><li>Linus</li></ul>
<a id="next" href="/next">Next</a>""",
    "/next": "<!doctype html><title>Next page</title><h1>Arrived</h1>",
    "/other": "<!doctype html><title>Other page</title><p>Elsewhere</p>",
    "/mine": "<!doctype html><title>The user's page</title>",
    "/login": '<!doctype html><title>Sign in</title><input type="password" aria-label="Password" value="hunter2">',
    "/slow": "<!doctype html><title>Slow page</title><h1>Late</h1>",
}
# How long /slow takes to answer: far longer than a script's value takes to come back.
_SLOW_S: float = 1.5
_LIST: str = "() => [...document.querySelectorAll('li')].map((item) => item.textContent)"
# A script that holds the page this long, far past the wait it is given.
_BUSY_S: float = 4.0
_BUSY: str = f"() => {{ const end = Date.now() + {int(_BUSY_S * 1000)}; while (Date.now() < end) {{}} }}"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if urlsplit(self.path).path == "/slow":
            time.sleep(_SLOW_S)
        body: bytes = _PAGES.get(urlsplit(self.path).path, "not found").encode()
        self.send_response(200 if urlsplit(self.path).path in _PAGES else 404)
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


async def test_a_script_gives_the_value_of_an_expression_a_function_and_a_promise(
    browser: BrowserSession, site: str
) -> None:
    await browser.open_url("values", f"{site}/", True)
    title: ScriptRun = await browser.run_script("values", "document.title", 10.0, False)
    assert (title.end, json.loads(title.text), title.url, title.surprise) == ("value", "Eval page", f"{site}/", None)
    names: ScriptRun = await browser.run_script("values", _LIST, 10.0, False)
    assert json.loads(names.text) == ["Ada", "Grace", "Linus"]
    later: ScriptRun = await browser.run_script(
        "values", "async () => { await new Promise((done) => setTimeout(done, 50)); return { n: 7 }; }", 10.0, False
    )
    assert (later.end, json.loads(later.text)) == ("value", {"n": 7})
    nothing: ScriptRun = await browser.run_script("values", "() => undefined", 10.0, False)
    assert (nothing.end, nothing.text) == ("value", "null")


async def test_each_thread_runs_its_script_in_its_own_tab(browser: BrowserSession, site: str) -> None:
    await browser.open_url("one", f"{site}/", True)
    await browser.open_url("two", f"{site}/other", True)
    first, second = await asyncio.gather(
        browser.run_script("one", "document.title", 10.0, False),
        browser.run_script("two", "document.title", 10.0, False),
    )
    assert (json.loads(first.text), json.loads(second.text)) == ("Eval page", "Other page")


async def test_an_error_and_a_wait_that_ends_first_are_told_apart(browser: BrowserSession, site: str) -> None:
    await browser.open_url("ends", f"{site}/", True)
    thrown: ScriptRun = await browser.run_script("ends", "() => { throw new Error('boom'); }", 10.0, False)
    assert (thrown.end, thrown.text) == ("error", "Error: boom")
    waited: ScriptRun = await browser.run_script("ends", "() => new Promise(() => {})", 1.0, False)
    assert (waited.end, waited.text) == ("timeout", "")
    assert 1.0 <= waited.seconds < 3.0
    # A script that keeps the page busy past the wait comes back when the wait ends, not when it lets the page go.
    started: float = time.monotonic()
    busy: ScriptRun = await browser.run_script("ends", _BUSY, 1.0, False)
    assert busy.end == "timeout"
    assert time.monotonic() - started < _BUSY_S - 1.0
    # The page goes on after all three: the scripts left running hold nothing once done.
    again: ScriptRun = await browser.run_script("ends", "document.title", 10.0, False)
    assert (again.end, json.loads(again.text)) == ("value", "Eval page")


async def test_a_script_that_leads_the_tab_elsewhere_lets_it_land_and_says_so(
    browser: BrowserSession, site: str
) -> None:
    await browser.open_url("away", f"{site}/", True)
    clicked: ScriptRun = await browser.run_script(
        "away", "() => { document.getElementById('next').click(); }", 10.0, False
    )
    assert clicked.end in ("value", "navigated")
    assert clicked.url == f"{site}/next"
    assert clicked.surprise == f"the page navigated to {site}/next"
    landed: ScriptRun = await browser.run_script("away", "document.querySelector('h1').textContent", 10.0, False)
    assert json.loads(landed.text) == "Arrived"


async def test_a_script_that_leads_to_an_address_the_rules_forbid_has_the_tab_reset(
    browser: BrowserSession, site: str
) -> None:
    await browser.open_url("blocked", f"{site}/", True)
    forbidden: str = site.replace("127.0.0.1", "localhost") + "/next"
    with pytest.raises(BrowserError, match="may not be opened"):
        await browser.run_script("blocked", f"() => {{ location.href = {json.dumps(forbidden)}; }}", 10.0, False)
    assert browser.current_url("blocked") == "about:blank"


async def test_a_forbidden_address_that_answers_after_the_value_came_back_has_the_tab_reset_too(
    browser: BrowserSession, site: str
) -> None:
    await browser.open_url("slow", f"{site}/", True)
    forbidden: str = site.replace("127.0.0.1", "localhost") + "/slow"
    with pytest.raises(BrowserError, match="may not be opened"):
        await browser.run_script("slow", f"() => {{ location.href = {json.dumps(forbidden)}; return 1; }}", 10.0, False)
    assert browser.current_url("slow") == "about:blank"


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


async def test_a_script_runs_in_the_agents_own_tab_of_the_chrome_of_the_user(
    site: str, data_dir: Path, attach_config: BrowserConfig, tmp_path: Path
) -> None:
    chrome: ScratchChrome = await start_chrome(data_dir, f"{site}/mine")
    session: BrowserSession = BrowserSession(attach_config, SERVER, data_dir, tmp_path / "downloads", no_runs_at_work)
    try:
        await session.open_url("agent", f"{site}/", True)
        run: ScriptRun = await session.run_script("agent", _LIST, 10.0, False)
        assert (run.end, json.loads(run.text), run.url) == ("value", ["Ada", "Grace", "Linus"], f"{site}/")
    finally:
        await session.aclose()
        await stop_chrome(chrome)


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


async def _nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked about this: {request.action.summary}")


async def _run_context(runtime: Runtime) -> ToolContext:
    """The tools of a task run, as the executor hands them a call the gate let through."""
    thread: ThreadRecord = await runtime.store.create_thread("eval", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        GOAL,
        "task",
        "ollama:none",
        None,
        "test",
        "medium",
        "standard",
        None,
        "auto",
        False,
        None,
        None,
        None,
    )
    return ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id=run.id,
        agent_id=None,
        thread_id=thread.id,
        mode="task",
        approvals="auto",
        questions="leave",
        workspace=workspace_dir(),
        gate=RunGate(
            runtime.store,
            runtime.bus,
            runtime.approvals,
            run.id,
            "task",
            "aggressive",
            runtime.config.policy.pace,
            runtime.config.policy.spend,
            "auto",
            _nobody,
            (GOAL,),
            runtime.site_locks,
        ),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )


async def test_the_tool_is_a_click_of_medium_risk_and_what_it_gives_back_is_untrusted(
    runtime: Runtime, site: str
) -> None:
    assert "browser_eval" in {tool.spec.name for tool in tools_for_mode(runtime.registry, "task")}
    assert "browser_eval" not in {tool.spec.name for tool in tools_for_mode(runtime.registry, "research")}
    ctx: ToolContext = await _run_context(runtime)
    await ctx.browser.open_url(ctx.thread_id, f"{site}/", True)
    prepared: PreparedCall = await runtime.registry.tools["browser_eval"].prepare({"script": _LIST}, ctx)
    action: ActionInfo = prepared.action
    assert (action.tool, action.kind, action.risk, action.target, action.flags) == (
        "browser_eval",
        "browser_click",
        "medium",
        f"{site}/",
        (),
    )
    assert action.summary.startswith("Run a script in the page: () => [...document.querySelectorAll('li')]")
    assert [preset_verdict(preset, action)[0] for preset in _PRESETS] == ["allow", "allow", "ask"]
    names: ToolResult = await prepared.run(ctx)
    assert (names.is_error, names.untrusted) == (False, True)
    assert "<web_content" in names.text and json.dumps(["Ada", "Grace", "Linus"]) in names.text
    thrown: PreparedCall = await runtime.registry.tools["browser_eval"].prepare(
        {"script": "() => { throw new Error('boom'); }"}, ctx
    )
    failed: ToolResult = await thrown.run(ctx)
    assert (failed.is_error, failed.untrusted) == (True, True)
    assert failed.text.startswith("The script threw an error") and "Error: boom" in failed.text


async def test_the_tool_keeps_a_script_off_a_page_with_a_secret_field_and_hides_the_cookies_outside_yolo(
    runtime: Runtime, site: str
) -> None:
    """The tool hands the run's approval mode to the session (item 533): outside yolo a script is refused on a page that
    holds a password field, a value Chrome filled in too, and what it gives back shows no cookie value; a yolo run reads
    all it is shown (item 443)."""
    ctx: ToolContext = await _run_context(runtime)
    eval_tool = runtime.registry.tools["browser_eval"]
    await ctx.browser.open_url(ctx.thread_id, f"{site}/login", True)
    refused: PreparedCall = await eval_tool.prepare({"script": "document.querySelector('input').value"}, ctx)
    with pytest.raises(BrowserError, match="a script stays off a page that holds a password"):
        await refused.run(ctx)
    yolo: ToolContext = replace(ctx, approvals="yolo")
    shown: ToolResult = await (await eval_tool.prepare({"script": "document.querySelector('input').value"}, yolo)).run(
        yolo
    )
    assert (shown.is_error, "hunter2" in shown.text) == (False, True)
    await ctx.browser.open_url(ctx.thread_id, f"{site}/", True)
    script: str = "() => { document.cookie = 'session=abcdef0123456789; path=/'; return document.cookie; }"
    hidden: ToolResult = await (await eval_tool.prepare({"script": script}, ctx)).run(ctx)
    assert "abcdef0123456789" not in hidden.text and "session=[hidden]" in hidden.text, hidden.text
    visible: ToolResult = await (await eval_tool.prepare({"script": "document.cookie"}, yolo)).run(yolo)
    assert "session=abcdef0123456789" in visible.text, visible.text
