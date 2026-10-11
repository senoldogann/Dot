"""web_fetch against local sites. Item 56: a page that is not there is reported with the links of the host's front page
that share a word with the address asked for, so the next turn opens the right page instead of guessing another. Item
59: a page that turns the plain request away (HTTP 429) or shows little without JavaScript is read in Dot's own
headless Chrome, and the result says so; a page that refuses the browser too says that. The sites are served on the
loopback address, which the web client is allowed."""

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.verify import new_facts
from dotagent.config import load_config
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.evals.site import serve_site
from dotagent.evals.types import SitePage
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.tools.base import ToolResult
from dotagent.tools.context import ToolContext
from dotagent.web.refused import RefusedHosts

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PAGES: tuple[SitePage, ...] = (
    SitePage(
        path="/",
        status=200,
        body='<!doctype html><title>Acme</title><a href="/about/">About</a> <a href="/company/open-roles/">Roles</a> '
        '<a href="/blog/careers-day/">Careers day</a> <a href="https://elsewhere.example/careers/">Partner</a>',
        location=None,
    ),
    SitePage(path="/about/", status=200, body="<!doctype html><title>About</title><p>About Acme.</p>", location=None),
)


_WALL_TEXT: str = "Open roles at Acme: Senior Developer, Oulu."
_SHELL_HTML: str = (
    "<!doctype html><html><head><title>Shell</title>"
    + "".join(f"<script>var s{number} = {number};</script>" for number in range(12))
    + "<script>document.addEventListener('DOMContentLoaded', () => {"
    "document.getElementById('app').textContent = 'Rendered by the script: forty open roles.'; });</script>"
    '</head><body><div id="app"></div></body></html>'
)


class _WallHandler(BaseHTTPRequestHandler):
    """A site that turns plain requests away: ``/wall`` answers 429 and ``/wall-999`` 999 unless a Chrome asks,
    ``/forbidden`` answers 403 to everyone, ``/shell`` is a page whose text a script writes, ``/open`` is open to all
    and says whether the request came from a Chrome."""

    def do_GET(self) -> None:
        path: str = self.path.split("?", 1)[0]
        chrome: bool = "Chrome" in self.headers.get("User-Agent", "")
        if path == "/wall":
            status, body = (200, f"<title>Roles</title><p>{_WALL_TEXT}</p>") if chrome else (429, "<p>Slow down.</p>")
        elif path == "/wall-999":
            status, body = (
                (200, f"<title>Roles</title><p>{_WALL_TEXT}</p>") if chrome else (999, "<p>Request denied.</p>")
            )
        elif path == "/open":
            status, body = 200, f"<title>Open</title><p>Asked by {'a Chrome' if chrome else 'a plain request'}.</p>"
        elif path == "/forbidden":
            status, body = 403, "<p>Access denied for everyone.</p>"
        elif path == "/shell":
            status, body = 200, _SHELL_HTML
        else:
            status, body = 404, "<p>No such page.</p>"
        payload: bytes = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return None


class _Gate:
    async def authorize(self, info: ActionInfo) -> str:
        return ""

    def add_instruction(self, text: str) -> None:
        return None


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    with serve_site(_PAGES) as address:
        yield address


@pytest.fixture(scope="module")
def wall() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _WallHandler)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        "[web]\nallow_private_hosts = true\n\n[browser]\nheadless = true\n", encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(home / "chrome"))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _fetch(runtime: Runtime, url: str) -> ToolResult:
    return await _fetch_as(runtime, url, RefusedHosts())


async def _fetch_as(runtime: Runtime, url: str, refused: RefusedHosts) -> ToolResult:
    ctx: ToolContext = ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id="fetch-test",
        agent_id=None,
        thread_id="fetch-test",
        mode="research",
        approvals="ask",
        questions="wait",
        workspace=workspace_dir(),
        gate=_Gate(),
        cancel=asyncio.Event(),
        output_chars=12000,
        facts=new_facts,
        refused_hosts=refused,
    )
    arguments: JsonObject = {"url": url}
    prepared = await runtime.registry.tools["web_fetch"].prepare(arguments, ctx)
    return await prepared.run(ctx)


async def test_a_page_that_is_not_there_is_reported_with_the_front_pages_likely_links(
    runtime: Runtime, site: str
) -> None:
    missing: ToolResult = await _fetch(runtime, f"{site}/careers/")
    assert missing.is_error
    assert missing.text.startswith(f"web_fetch got HTTP 404 from {site}/careers/.\nThe page is not there.")
    # The blog post shares the word; the partner's page is on another host; About shares nothing.
    assert f"- {site}/blog/careers-day/" in missing.text
    assert "elsewhere.example" not in missing.text and "/about/" not in missing.text.split("<web_content", 1)[0]
    unrelated: ToolResult = await _fetch(runtime, f"{site}/pricing/")
    assert unrelated.is_error and "links to nothing that shares a word" in unrelated.text
    there: ToolResult = await _fetch(runtime, f"{site}/about/")
    assert not there.is_error and "About Acme." in there.text


async def test_a_page_that_turns_the_plain_request_away_is_read_in_dots_own_chrome(runtime: Runtime, wall: str) -> None:
    read: ToolResult = await _fetch(runtime, f"{wall}/wall")
    assert not read.is_error and read.untrusted
    assert read.text.startswith("Read in Dot's browser: the plain request got HTTP 429.\nFetched ")
    assert _WALL_TEXT in read.text
    shell: ToolResult = await _fetch(runtime, f"{wall}/shell")
    assert not shell.is_error
    assert shell.text.startswith("Read in Dot's browser: the page shows little text without JavaScript.")
    assert "Rendered by the script: forty open roles." in shell.text
    refused: ToolResult = await _fetch(runtime, f"{wall}/forbidden")
    assert refused.is_error
    assert refused.text.startswith(
        f"web_fetch got HTTP 403 from {wall}/forbidden in Dot's browser too (the plain request got HTTP 403).\n"
        "The site refuses this machine. Next: another source"
    )
    assert "Access denied for everyone." in refused.text
    # The scratch tab left no tab behind for the thread.
    assert runtime.browser.current_url("fetch-test") is None


async def test_a_host_that_refused_once_is_read_in_the_browser_first_from_then_on(runtime: Runtime, wall: str) -> None:
    refused: RefusedHosts = RefusedHosts()
    walled: ToolResult = await _fetch_as(runtime, f"{wall}/wall-999", refused)
    assert not walled.is_error
    assert walled.text.startswith("Read in Dot's browser: the plain request got HTTP 999.")
    assert _WALL_TEXT in walled.text and len(refused) == 1
    # The same host again: the plain request is not made, the browser reads the page at once.
    again: ToolResult = await _fetch_as(runtime, f"{wall}/open", refused)
    assert again.text.startswith("Read in Dot's browser: the host turned an earlier plain request away.")
    assert "Asked by a Chrome." in again.text
    # Another run has no such memory: the plain request is made and the page read as fetched.
    fresh: ToolResult = await _fetch(runtime, f"{wall}/open")
    assert fresh.text.startswith("Fetched ") and "Asked by a plain request." in fresh.text
