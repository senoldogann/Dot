"""Working in the Chrome that is already open (attach mode), against a real scratch Chrome.

The scratch Chrome plays the Chrome of the user: hidden, on its own temporary data directory, with a debugging port and
one tab of "the user" open. What only the real Chrome of the user can show, the Allow dialog that it puts up when a
program connects, is not part of this: only the user can click it. Nor is the click on Chrome's icon that a switch to
a closed Chrome makes (item 261): the scratch Chrome is started in its place, and the Chrome of the user is never
opened.
"""

import asyncio
import io
import re
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from PIL import Image
from playwright.async_api import Browser, Locator, Page, Route, async_playwright, expect
from pydantic import BaseModel, TypeAdapter
from structlog.testing import capture_logs

from dotagent.agent.manager import RunManager
from dotagent.browser.attach import DebugEndpoint, parse_port_file
from dotagent.browser.keychain import KeychainMode, read_keychain_mode
from dotagent.browser.login import chrome_arguments
from dotagent.browser.look import PageLook
from dotagent.browser.modes import adopt_mode, browser_data_dir
from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.browser.safety import refuse_secret_field
from dotagent.browser.session import BrowserSession, RenderedDocument, no_runs_at_work
from dotagent.browser.types import ActOutcome, Attachment, PageView, Screenshot, ScriptRun, Step
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config
from dotagent.core.errors import BrowserError, ToolExecutionError
from dotagent.core.paths import ensure_layout
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = pytest.mark.browser

# Whose interface the browser never opens: the daemon's default address.
SERVER: ServerConfig = load_default_config().server
TOKEN: str = "open-chrome-token"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
_PORT_FILE: str = "DevToolsActivePort"
_READY_TIMEOUT_S: float = 30.0
_EXIT_TIMEOUT_S: float = 20.0
# The longest a Back may take on a page of this Mac: a navigation, not the navigation timeout.
_BACK_MOST_S: float = 5.0
# How long the browser card takes at most to show how Chrome stands now: two of its reads (item 341).
_CARD_READS_MS: int = 7000
_TABS_TIMEOUT_S: float = 10.0
_POLL_S: float = 0.1
# Events that Chrome sends when a tab opens or when it quits arrive a few milliseconds after the fact.
_EVENT_GRACE_S: float = 0.5
_PAGES: dict[str, str] = {
    "/": """<!doctype html><title>Home</title><h1>Dot attach fixture</h1>
<button onclick="document.getElementById('n').textContent =
  String(Number(document.getElementById('n').textContent) + 1)">Increment</button>
<span id="n">0</span>
<a href="/popup" target="_blank">Open popup</a>""",
    "/mine": "<!doctype html><title>Mine</title><h1>A page of the user</h1>",
    "/other": "<!doctype html><title>Other</title><h1>Another page of the user</h1>",
    "/popup": "<!doctype html><title>Popup</title><h1>Popup page</h1>",
    # Two tabs from one click: the Chrome of the user opens the first and blocks the second (item 342).
    "/two": """<!doctype html><title>Two</title>
<button onclick="window.open('/popup'); window.open('/popup?second')">Open two</button><button>Stay</button>""",
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path: str = self.path.split("?", 1)[0]
        body: bytes = _PAGES.get(path, "not found").encode()
        self.send_response(200 if path in _PAGES else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class _Target(BaseModel):
    """One entry of the list of targets that Chrome serves; only what the tests read."""

    type: str
    url: str


_TARGETS: TypeAdapter[list[_Target]] = TypeAdapter(list[_Target])


@dataclass(frozen=True)
class ScratchChrome:
    """A Chrome that plays the Chrome of the user."""

    data_dir: Path
    port: int
    process: asyncio.subprocess.Process


async def wait_for_port(data_dir: Path) -> int:
    """The debugging port that the Chrome on ``data_dir`` writes into its port file, once it answers on it."""
    port_file: Path = data_dir / _PORT_FILE
    deadline: float = time.monotonic() + _READY_TIMEOUT_S
    async with httpx.AsyncClient() as client:
        while True:
            try:
                text: str = await asyncio.to_thread(port_file.read_text, encoding="utf-8")
                endpoint: DebugEndpoint = parse_port_file(text, port_file)
                await client.get(f"http://127.0.0.1:{endpoint.port}/json/version")
                return endpoint.port
            except (OSError, BrowserError, httpx.TransportError):
                if time.monotonic() > deadline:
                    raise
                await asyncio.sleep(_POLL_S)


async def spawn_chrome(data_dir: Path, url: str) -> asyncio.subprocess.Process:
    """Start a hidden Chrome on ``data_dir`` with one tab on ``url`` and return at once, as a click on Chrome's icon
    does; Chrome picks its debugging port itself."""
    keychain: KeychainMode = read_keychain_mode(data_dir)
    command: list[str] = [*chrome_arguments(data_dir, url, keychain), "--headless=new", "--remote-debugging-port=0"]
    return await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )


async def spawn_chrome_without_debugging(data_dir: Path, url: str) -> asyncio.subprocess.Process:
    """Start a hidden Chrome on ``data_dir`` whose remote debugging is off, and return at once."""
    keychain: KeychainMode = read_keychain_mode(data_dir)
    command: list[str] = [*chrome_arguments(data_dir, url, keychain), "--headless=new"]
    return await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )


async def wait_for_profile(data_dir: Path) -> None:
    """Wait until a Chrome holds the profile lock of ``data_dir``."""
    deadline: float = time.monotonic() + _READY_TIMEOUT_S
    while profile_holder_pid(data_dir) is None:
        if time.monotonic() > deadline:
            raise AssertionError(f"no Chrome took the profile at {data_dir}")
        await asyncio.sleep(_POLL_S)


async def start_chrome(data_dir: Path, url: str) -> ScratchChrome:
    """Start a hidden Chrome on ``data_dir`` with one tab on ``url``, once it answers on its debugging port."""
    process: asyncio.subprocess.Process = await spawn_chrome(data_dir, url)
    ready: bool = False
    try:
        port: int = await wait_for_port(data_dir)
        ready = True
    finally:
        if not ready:
            process.kill()
            await process.wait()
    return ScratchChrome(data_dir=data_dir, port=port, process=process)


async def stop_chrome(chrome: ScratchChrome) -> None:
    await stop_process(chrome.process)


async def stop_process(process: asyncio.subprocess.Process) -> None:
    """Quit Chrome the way the system asks a program to quit; a Chrome that does not quit in time is killed."""
    if process.returncode is None:
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), _EXIT_TIMEOUT_S)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise


async def never_opened(data_dir: Path) -> None:
    """The opener of a switch whose Chrome runs already: it is never called."""
    raise AssertionError(f"the Chrome on {data_dir} runs; nothing is opened")


async def open_tabs(port: int) -> list[str]:
    """The URLs of the tabs Chrome has open, sorted, as Chrome itself lists them."""
    async with httpx.AsyncClient() as client:
        response: httpx.Response = await client.get(f"http://127.0.0.1:{port}/json/list")
    response.raise_for_status()
    return sorted(target.url for target in _TARGETS.validate_json(response.content) if target.type == "page")


async def wait_for_tabs(port: int, expected: list[str]) -> None:
    """Wait until Chrome lists exactly the tabs ``expected``; closing a tab takes a moment to show."""
    deadline: float = time.monotonic() + _TABS_TIMEOUT_S
    while True:
        urls: list[str] = await open_tabs(port)
        if urls == sorted(expected):
            return
        if time.monotonic() > deadline:
            raise AssertionError(f"Chrome lists the tabs {urls}, expected {sorted(expected)}")
        await asyncio.sleep(_POLL_S)


async def open_tab_as_the_user(port: int, url: str) -> None:
    """A tab that nobody in Dot opened: Chrome opens it on request of its own debugging endpoint, without an opener."""
    async with httpx.AsyncClient() as client:
        response: httpx.Response = await client.put(f"http://127.0.0.1:{port}/json/new?{url}")
    response.raise_for_status()


def ref_of(body: str, label: str) -> str:
    match: re.Match[str] | None = re.search(rf"{re.escape(label)}[^\n]*?\[ref=([^\]]+)\]", body)
    assert match is not None, f"{label!r} not in:\n{body}"
    return match.group(1)


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    directory: Path = tmp_path / "chrome"
    directory.mkdir(mode=0o700)
    return directory


@pytest.fixture
def attach_config(tmp_path: Path) -> BrowserConfig:
    settings: Path = tmp_path / "config.toml"
    settings.write_text('[browser]\nmode = "attach"\nattach_timeout_s = 10.0\n', encoding="utf-8")
    return load_config(settings).browser


@pytest.fixture
def own_config(tmp_path: Path) -> BrowserConfig:
    """Dot's own Chrome, hidden; the attach timeout is what a switch to the Chrome of the user carries along."""
    settings: Path = tmp_path / "own.toml"
    settings.write_text("[browser]\nheadless = true\nattach_timeout_s = 10.0\n", encoding="utf-8")
    return load_config(settings).browser


@pytest.fixture
def own_home(tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A Dot home of its own, with the scratch Chrome standing in for the Chrome of the user."""
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(data_dir))
    ensure_layout()
    return home


@pytest_asyncio.fixture
async def chrome(data_dir: Path, site: str) -> AsyncIterator[ScratchChrome]:
    running: ScratchChrome = await start_chrome(data_dir, f"{site}/mine")
    try:
        yield running
    finally:
        await stop_chrome(running)


async def _windows_by_url(port: int) -> dict[str, int]:
    """The window each tab of the scratch Chrome is in, by its address, as a second client of its DevTools reads it."""
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", is_local=True)
        try:
            cdp = await browser.new_browser_cdp_session()
            targets: list[dict[str, str]] = (await cdp.send("Target.getTargets"))["targetInfos"]
            windows: dict[str, int] = {}
            for target in targets:
                if target["type"] == "page":
                    found = await cdp.send("Browser.getWindowForTarget", {"targetId": target["targetId"]})
                    windows[target["url"]] = int(found["windowId"])
            return windows
        finally:
            await browser.close()


async def test_a_tab_of_dot_opens_in_a_window_of_its_own(
    chrome: ScratchChrome, attach_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    """Item 340: a tab Dot opens in the Chrome of the user is in a window of its own, not among the user's tabs (a
    background tab is hidden there, so its page drew no frame and its timers ran 8 a second); a second thread's tab is
    in another window again."""
    session: BrowserSession = BrowserSession(
        attach_config, SERVER, chrome.data_dir, tmp_path / "downloads", no_runs_at_work
    )
    try:
        await session.open_url("own-window", f"{site}/", True)
        await session.open_url("other-window", f"{site}/other", True)
        windows: dict[str, int] = await _windows_by_url(chrome.port)
        assert len({windows[f"{site}/mine"], windows[f"{site}/"], windows[f"{site}/other"]}) == 3, windows
    finally:
        await session.aclose()


async def test_the_colour_scheme_chosen_reaches_new_tabs_and_the_connection_stays(
    chrome: ScratchChrome, attach_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    """Item 354: in the Chrome of the user a new colour scheme lets go of nothing; the tabs Dot opens after it are told
    it, as the tab of another thread, opened before, keeps working."""
    session: BrowserSession = BrowserSession(
        attach_config, SERVER, chrome.data_dir, tmp_path / "downloads", no_runs_at_work
    )
    try:
        await session.open_url("before", f"{site}/", True)
        await session.set_look(
            PageLook(fit_window=False, viewport_width=1280, viewport_height=860, appearance="dark", zoom=100)
        )
        assert session.connected
        await session.open_url("after", f"{site}/other", True)
        dark: ScriptRun = await session.run_script(
            "after", "matchMedia('(prefers-color-scheme: dark)').matches", 5.0, False
        )
        assert (dark.end, dark.text) == ("value", "true"), dark
        kept: ScriptRun = await session.run_script("before", "document.title", 5.0, False)
        assert kept.end == "value", kept
    finally:
        await session.aclose()


async def test_back_in_the_chrome_of_the_user_takes_no_longer_than_a_page(
    chrome: ScratchChrome, attach_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    """A Chrome that keeps its back-forward cache (the owner's does; Dot's own does not) brings the page before back
    from the cache, with no load or DOMContentLoaded: Back is done once the page is committed, not after the whole
    navigation timeout (item 339: each Back took 30 s, then failed). On the tab's first page there is no page before."""
    session: BrowserSession = BrowserSession(
        attach_config, SERVER, chrome.data_dir, tmp_path / "downloads", no_runs_at_work
    )
    try:
        first: PageView = await session.open_url("back", f"{site}/mine", True)
        with pytest.raises(BrowserError, match="there is no earlier page"):
            await session.back("back", True)
        await session.open_url("back", f"{site}/", True)
        started: float = time.monotonic()
        view: PageView = await session.back("back", True)
        took: float = time.monotonic() - started
        assert view.url == first.url and view.title == "Mine", view
        assert took < _BACK_MOST_S, took
    finally:
        await session.aclose()


async def test_the_agent_works_in_tabs_of_its_own_and_the_tabs_of_the_user_stay(
    chrome: ScratchChrome, attach_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    session: BrowserSession = BrowserSession(
        attach_config, SERVER, chrome.data_dir, tmp_path / "downloads", no_runs_at_work
    )
    try:
        attachment: Attachment = await session.attachment()
        assert re.match(r"\d+\.", attachment.browser_version), attachment
        assert attachment.open_tabs == 1
        view: PageView = await session.open_url("first", f"{site}/", True)
        assert view.title == "Home"
        assert [tab.url for tab in view.tabs] == [f"{site}/"]
        clicked: ActOutcome = await session.act(
            "first",
            [Step(action="click", ref=ref_of(view.body, 'button "Increment"'))],
            False,
            refuse_secret_field,
            True,
        )
        assert [report.outcome for report in clicked.steps] == ["done"]
        assert clicked.view.mode == "delta"
        await session.open_url("second", f"{site}/popup", True)
        await wait_for_tabs(chrome.port, [f"{site}/mine", f"{site}/", f"{site}/popup"])

        shot: Screenshot = await session.screenshot("first", True)
        assert Image.open(io.BytesIO(shot.data)).size == (shot.width, shot.height)
        assert (shot.width, shot.height) != (attach_config.viewport_width, attach_config.viewport_height)

        await session.release_thread("first")
        await wait_for_tabs(chrome.port, [f"{site}/mine", f"{site}/popup"])
    finally:
        await session.aclose()
    await wait_for_tabs(chrome.port, [f"{site}/mine"])
    assert chrome.process.returncode is None
    assert profile_holder_pid(chrome.data_dir) == chrome.process.pid


async def test_a_page_is_read_quietly_in_a_tab_behind_the_users_and_the_tab_closes_after(
    chrome: ScratchChrome, attach_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    session: BrowserSession = BrowserSession(
        attach_config, SERVER, chrome.data_dir, tmp_path / "downloads", no_runs_at_work
    )
    try:
        assert session.quiet_reads and not session.human_like
        rendered: RenderedDocument = await session.read_page(f"{site}/other")
        assert (rendered.url, rendered.status) == (f"{site}/other", 200)
        assert "Another page of the user" in rendered.html
        await wait_for_tabs(chrome.port, [f"{site}/mine"])
        # The read left nothing behind for a thread, and the user's tab is the one in front.
        assert session.current_url("quiet") is None
        front: PageView = await session.open_url("quiet", f"{site}/", True)
        assert [tab.url for tab in front.tabs] == [f"{site}/"]
    finally:
        await session.aclose()
    await wait_for_tabs(chrome.port, [f"{site}/mine"])


async def test_a_tab_the_user_opens_meanwhile_is_not_taken_over(
    chrome: ScratchChrome, attach_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    session: BrowserSession = BrowserSession(
        attach_config, SERVER, chrome.data_dir, tmp_path / "downloads", no_runs_at_work
    )
    try:
        await session.open_url("agent", f"{site}/", True)
        await open_tab_as_the_user(chrome.port, f"{site}/other")
        await wait_for_tabs(chrome.port, [f"{site}/mine", f"{site}/other", f"{site}/"])
        await asyncio.sleep(_EVENT_GRACE_S)
        view: PageView = await session.snapshot("agent", True)
        assert [tab.url for tab in view.tabs] == [f"{site}/"]
    finally:
        await session.aclose()
    await wait_for_tabs(chrome.port, [f"{site}/mine", f"{site}/other"])


async def test_a_tab_that_a_tab_of_the_agent_opens_is_the_agents_and_closes_with_it(
    chrome: ScratchChrome, attach_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    session: BrowserSession = BrowserSession(
        attach_config, SERVER, chrome.data_dir, tmp_path / "downloads", no_runs_at_work
    )
    try:
        view: PageView = await session.open_url("agent", f"{site}/", True)
        opened: ActOutcome = await session.act(
            "agent",
            [Step(action="click", ref=ref_of(view.body, 'link "Open popup"'))],
            False,
            refuse_secret_field,
            True,
        )
        assert [tab.url for tab in opened.view.tabs] == [f"{site}/", f"{site}/popup"]
        await wait_for_tabs(chrome.port, [f"{site}/mine", f"{site}/", f"{site}/popup"])
    finally:
        await session.aclose()
    await wait_for_tabs(chrome.port, [f"{site}/mine"])


async def test_a_click_that_asks_for_two_tabs_stops_at_the_one_chrome_opened(
    chrome: ScratchChrome, attach_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    """The batch waits for the tabs Chrome made, not for those the page asked for (item 342): the blocked second popup
    holds no step."""
    session: BrowserSession = BrowserSession(
        attach_config, SERVER, chrome.data_dir, tmp_path / "downloads", no_runs_at_work
    )
    try:
        view: PageView = await session.open_url("two", f"{site}/two", True)
        steps: list[Step] = [
            Step(action="click", ref=ref_of(view.body, 'button "Open two"')),
            Step(action="click", ref=ref_of(view.body, 'button "Stay"')),
        ]
        with capture_logs() as logs:
            outcome: ActOutcome = await session.act("two", steps, False, refuse_secret_field, True)
        assert [report.outcome for report in outcome.steps] == ["done", "skipped"]
        assert outcome.stopped is not None and "new tab" in outcome.stopped
        assert [tab.url for tab in outcome.view.tabs] == [f"{site}/two", f"{site}/popup"]
        assert not [entry for entry in logs if entry["event"] == "browser_new_tab_not_taken_in"]
    finally:
        await session.aclose()


async def test_a_session_reports_that_chrome_has_quit_and_works_again_when_chrome_is_back(
    data_dir: Path, attach_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    """Chrome quits: the session says so (item 341), a step meanwhile says Chrome is not running; Chrome is back: the
    next step connects again by itself, and the owner is told once, as Chrome asks for its Allow then."""
    first: ScratchChrome = await start_chrome(data_dir, f"{site}/mine")
    session: BrowserSession = BrowserSession(attach_config, SERVER, data_dir, tmp_path / "downloads", no_runs_at_work)
    told: list[str] = []

    async def notice() -> None:
        told.append("told")

    session.tell_reconnects(notice)
    try:
        await session.open_url("agent", f"{site}/", True)
        assert not session.lost and told == []
        await stop_chrome(first)
        assert profile_holder_pid(data_dir) is None
        await asyncio.sleep(_EVENT_GRACE_S)
        assert session.lost and not session.connected
        with pytest.raises(BrowserError, match="Chrome is not running; open Chrome"):
            await session.snapshot("agent", True)
        second: ScratchChrome = await start_chrome(data_dir, f"{site}/mine")
        try:
            view: PageView = await session.open_url("agent", f"{site}/", True)
            assert view.title == "Home"
            assert await open_tabs(second.port) == sorted([f"{site}/mine", f"{site}/"])
            assert told == ["told"] and not session.lost and not session.waiting_allow
        finally:
            await stop_chrome(second)
    finally:
        await session.aclose()


async def test_a_reconnection_whose_notice_fails_connects_all_the_same(
    data_dir: Path, attach_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    """The notice of a reconnection is told beside it (the review's B1): one that fails, a notification macOS did not
    show, is logged, and the step connects all the same. Before, its error was the step's, and every later step's, as
    the session stayed lost."""
    first: ScratchChrome = await start_chrome(data_dir, f"{site}/mine")
    session: BrowserSession = BrowserSession(attach_config, SERVER, data_dir, tmp_path / "downloads", no_runs_at_work)

    async def failing() -> None:
        raise ToolExecutionError("the notification could not be shown", {"code": 1})

    session.tell_reconnects(failing)
    try:
        await session.open_url("agent", f"{site}/", True)
        await stop_chrome(first)
        await asyncio.sleep(_EVENT_GRACE_S)
        assert session.lost
        second: ScratchChrome = await start_chrome(data_dir, f"{site}/mine")
        try:
            with capture_logs() as logs:
                view: PageView = await session.open_url("agent", f"{site}/", True)
            assert view.title == "Home" and not session.lost
            assert [entry for entry in logs if entry["event"] == "browser_reconnect_notice_failed"]
        finally:
            await stop_chrome(second)
    finally:
        await session.aclose()


async def test_a_chrome_that_restarted_and_refuses_says_so_in_one_line(
    data_dir: Path, attach_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    """Item 341: after Chrome went away, a connection it does not accept (the Allow not given in time) says that Chrome
    was closed or restarted and what to do, not only that it refused."""
    first: ScratchChrome = await start_chrome(data_dir, f"{site}/mine")
    session: BrowserSession = BrowserSession(attach_config, SERVER, data_dir, tmp_path / "downloads", no_runs_at_work)
    try:
        await session.open_url("agent", f"{site}/", True)
        await stop_chrome(first)
        await asyncio.sleep(_EVENT_GRACE_S)
        second: ScratchChrome = await start_chrome(data_dir, f"{site}/mine")
        try:
            wrong: str = "00000000-0000-4000-8000-000000000000"
            (data_dir / _PORT_FILE).write_text(f"{second.port}\n/devtools/browser/{wrong}\n", encoding="utf-8")
            with pytest.raises(
                BrowserError, match=r"^Chrome was closed or restarted; keep a Chrome window open, click Allow"
            ):
                await session.open_url("agent", f"{site}/", True)
            assert session.lost and not session.waiting_allow
        finally:
            await stop_chrome(second)
    finally:
        await session.aclose()


async def test_a_connection_that_chrome_refuses_is_reported_without_the_secret_path(
    chrome: ScratchChrome, attach_config: BrowserConfig, tmp_path: Path
) -> None:
    wrong: str = "00000000-0000-4000-8000-000000000000"
    (chrome.data_dir / _PORT_FILE).write_text(f"{chrome.port}\n/devtools/browser/{wrong}\n", encoding="utf-8")
    session: BrowserSession = BrowserSession(
        attach_config, SERVER, chrome.data_dir, tmp_path / "downloads", no_runs_at_work
    )
    try:
        with pytest.raises(BrowserError, match="did not accept the connection") as raised:
            await session.attachment()
    finally:
        await session.aclose()
    assert wrong not in str(raised.value)
    assert raised.value.context["port"] == chrome.port


async def test_a_script_reaches_the_runs_own_tab_every_time_while_three_chromes_run(
    chrome: ScratchChrome,
    attach_config: BrowserConfig,
    own_config: BrowserConfig,
    own_home: Path,
    site: str,
    tmp_path: Path,
) -> None:
    """Part 46 found AppleScript reaching whichever Chrome macOS picked, about four calls in ten while three Chromes ran
    (item 343 proves its answer, item 263): browser_eval runs in the run's own tab, in the Chrome of the user beside
    Dot's own Chrome and a third one, ten times in ten."""
    third_dir: Path = tmp_path / "third"
    await asyncio.to_thread(third_dir.mkdir, mode=0o700)
    third: ScratchChrome = await start_chrome(third_dir, f"{site}/other")
    own: BrowserSession = BrowserSession(
        own_config, SERVER, browser_data_dir("own", own_config.profile), own_home / "downloads", no_runs_at_work
    )
    attached: BrowserSession = BrowserSession(
        attach_config, SERVER, chrome.data_dir, tmp_path / "downloads", no_runs_at_work
    )
    try:
        await own.open_url("own", f"{site}/other", True)
        await attached.open_url("run", f"{site}/", True)
        titles: list[str] = [(await attached.run_script("run", "document.title", 5.0, False)).text for _ in range(10)]
        assert titles == ['"Home"'] * 10
        await wait_for_tabs(chrome.port, [f"{site}/mine", f"{site}/"])
    finally:
        await attached.aclose()
        await own.aclose()
        await stop_chrome(third)


async def test_a_session_moves_between_dots_own_chrome_and_the_chrome_of_the_user(
    chrome: ScratchChrome, own_config: BrowserConfig, own_home: Path, site: str
) -> None:
    own_profile: Path = browser_data_dir("own", own_config.profile)
    session: BrowserSession = BrowserSession(own_config, SERVER, own_profile, own_home / "downloads", no_runs_at_work)
    try:
        assert (session.mode, session.connected) == ("own", False)
        home_page: PageView = await session.open_url("own", f"{site}/", True)
        assert home_page.title == "Home"
        assert session.connected
        assert profile_holder_pid(own_profile) is not None

        await adopt_mode(session, "attach", never_opened)
        assert (session.mode, session.profile_dir) == ("attach", chrome.data_dir)
        assert profile_holder_pid(own_profile) is None
        await wait_for_tabs(chrome.port, [f"{site}/mine"])
        popup: PageView = await session.open_url("agent", f"{site}/popup", True)
        assert popup.title == "Popup"
        await wait_for_tabs(chrome.port, [f"{site}/mine", f"{site}/popup"])

        await adopt_mode(session, "own", never_opened)
        assert (session.mode, session.profile_dir, session.connected) == ("own", own_profile, False)
        await wait_for_tabs(chrome.port, [f"{site}/mine"])
        other: PageView = await session.open_url("own", f"{site}/other", True)
        assert other.title == "Other"
        assert profile_holder_pid(own_profile) is not None
    finally:
        await session.aclose()
    assert profile_holder_pid(own_profile) is None
    assert chrome.process.returncode is None


async def test_a_switch_that_chrome_refuses_leaves_the_session_where_it_was(
    chrome: ScratchChrome, own_config: BrowserConfig, own_home: Path
) -> None:
    wrong: str = "00000000-0000-4000-8000-000000000000"
    (chrome.data_dir / _PORT_FILE).write_text(f"{chrome.port}\n/devtools/browser/{wrong}\n", encoding="utf-8")
    own_profile: Path = browser_data_dir("own", own_config.profile)
    session: BrowserSession = BrowserSession(own_config, SERVER, own_profile, own_home / "downloads", no_runs_at_work)
    try:
        with pytest.raises(BrowserError, match="did not accept the connection"):
            await adopt_mode(session, "attach", never_opened)
        assert (session.mode, session.profile_dir, session.connected) == ("own", own_profile, False)
    finally:
        await session.aclose()


async def test_a_switch_to_the_chrome_of_the_user_while_it_is_closed_opens_it_first(
    data_dir: Path, own_config: BrowserConfig, own_home: Path, site: str
) -> None:
    """Item 261: the switch opens the closed Chrome, as a click on its icon does, waits until it runs and listens, and
    attaches to it; the opener returns at once, as ``open`` does, so the switch itself does the waiting."""
    opened: list[asyncio.subprocess.Process] = []

    async def open_as_a_click_does(directory: Path) -> None:
        opened.append(await spawn_chrome(directory, f"{site}/mine"))

    session: BrowserSession = BrowserSession(
        own_config, SERVER, browser_data_dir("own", own_config.profile), own_home / "downloads", no_runs_at_work
    )
    try:
        assert profile_holder_pid(data_dir) is None
        await adopt_mode(session, "attach", open_as_a_click_does)
        assert len(opened) == 1
        assert (session.mode, session.profile_dir) == ("attach", data_dir)
        port: int = await wait_for_port(data_dir)
        await wait_for_tabs(port, [f"{site}/mine"])
        popup: PageView = await session.open_url("agent", f"{site}/popup", True)
        assert popup.title == "Popup"
    finally:
        await session.aclose()
        for process in opened:
            await stop_process(process)


async def test_a_chrome_opened_with_its_remote_debugging_off_is_named_and_the_session_stays(
    data_dir: Path, own_config: BrowserConfig, own_home: Path, site: str
) -> None:
    """Item 261: a Chrome the switch opened whose remote debugging is off is not "not running": the switch says how to
    turn remote debugging on, and the session stays on Dot's own Chrome."""
    opened: list[asyncio.subprocess.Process] = []

    async def open_as_a_click_does(directory: Path) -> None:
        opened.append(await spawn_chrome_without_debugging(directory, f"{site}/mine"))

    own_profile: Path = browser_data_dir("own", own_config.profile)
    session: BrowserSession = BrowserSession(own_config, SERVER, own_profile, own_home / "downloads", no_runs_at_work)
    try:
        with pytest.raises(BrowserError, match="does not allow remote debugging"):
            await adopt_mode(session, "attach", open_as_a_click_does)
        assert len(opened) == 1 and profile_holder_pid(data_dir) is not None
        assert (session.mode, session.profile_dir, session.connected) == ("own", own_profile, False)
    finally:
        await session.aclose()
        for process in opened:
            await stop_process(process)


def held_until(released: asyncio.Event) -> Callable[[Route], Awaitable[None]]:
    """A route handler that lets the request through once ``released`` is set."""

    async def hold(route: Route) -> None:
        await released.wait()
        await route.continue_()

    return hold


@asynccontextmanager
async def _served_attached(home: Path) -> AsyncIterator[tuple[Runtime, str]]:
    """A daemon on the Dot home ``home`` that works in the Chrome of the user (the scratch Chrome), with its runtime."""
    (home / "config.toml").write_text(
        '[browser]\nmode = "attach"\nheadless = true\nattach_timeout_s = 10.0\n', encoding="utf-8"
    )
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
            yield runtime, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def test_the_settings_card_follows_chrome_as_it_quits_and_comes_back(
    data_dir: Path, own_home: Path, site: str
) -> None:
    """Item 341: with My Chrome the browser card reads Chrome again while it is open, so it says, with no click, that
    Dot is connected, that Chrome is closed, that Chrome restarted and asks for Allow on the next step, and that Dot is
    connected again; each with the one line that says what to do."""
    first: ScratchChrome = await start_chrome(data_dir, f"{site}/mine")
    async with _served_attached(own_home) as (runtime, address), async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            channel=load_default_config().browser.channel, headless=True
        )
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, service_workers="block")
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            await page.goto(f"{address}/#/settings/browser")
            card: Locator = page.locator("section.card").filter(
                has=page.locator(".card-head .grow", has_text=re.compile(r"^Browser$"))
            )
            connection: Locator = (
                card.locator("dl div")
                .filter(has=page.locator("dt", has_text=re.compile(r"^Agent connection$")))
                .locator("dd")
            )
            line: Locator = card.locator(".attach-standing")
            await runtime.browsers.main.open_url("card", f"{site}/", True)
            await expect(connection).to_have_text("connected", timeout=_CARD_READS_MS)
            await expect(line).to_contain_text("later runs connect with no click")
            await stop_chrome(first)
            await expect(line).to_contain_text("Chrome is closed", timeout=_CARD_READS_MS)
            second: ScratchChrome = await start_chrome(data_dir, f"{site}/mine")
            try:
                await expect(connection).to_have_text("Chrome restarted", timeout=_CARD_READS_MS)
                await expect(line).to_contain_text("Chrome asks you to Allow")
                await runtime.browsers.main.open_url("card", f"{site}/", True)
                await expect(connection).to_have_text("connected", timeout=_CARD_READS_MS)
            finally:
                await stop_chrome(second)
        finally:
            await browser.close()


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


async def test_the_settings_card_says_what_a_switch_waits_for_and_how_chrome_stands_after_it(
    data_dir: Path, own_home: Path, site: str, tmp_path: Path
) -> None:
    """Item 261: while the switch to My Chrome is under way the card says what it waits for; when the switch fails, the
    card says why and reads Chrome again, which the switch may have opened (here a Chrome whose remote debugging is
    off), and Own Chrome stays chosen. In Chrome, wide and on a phone, light and dark."""
    opened: list[asyncio.subprocess.Process] = []
    async with _served(own_home) as address, async_playwright() as playwright:
        host, port = "127.0.0.1", int(address.rpartition(":")[2])
        browser: Browser = await playwright.chromium.launch(
            channel=load_default_config().browser.channel, headless=True
        )
        try:
            for width, scheme in _SHOTS:
                # Without the service worker: once it controls the page, the switch's request passes through it and
                # not through the route that holds it.
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 900},
                    color_scheme=scheme,
                    reduced_motion="reduce",
                    service_workers="block",
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{address}/#/settings/browser")
                card: Locator = page.locator("section.card").filter(
                    has=page.locator(".card-head .grow", has_text=re.compile(r"^Browser$"))
                )
                chrome_fact: Locator = (
                    card.locator("dl div")
                    .filter(has=page.locator("dt", has_text=re.compile(r"^Chrome$")))
                    .locator("dd")
                )
                await expect(chrome_fact).to_have_text("running" if opened else "not running")
                released: asyncio.Event = asyncio.Event()
                await page.route("**/api/browser/mode", held_until(released))
                await card.get_by_role("button", name="My Chrome").click()
                waiting: Locator = card.locator(".note[role='status']")
                await expect(waiting).to_be_visible()
                await expect(waiting).to_contain_text("Dot opens it when it is closed")
                await expect(waiting).to_contain_text("choose Allow")
                await card.scroll_into_view_if_needed()
                await page.screenshot(path=tmp_path / f"waiting-{width}-{scheme}.png")
                if not opened:
                    # What the switch's click on Chrome's icon starts here: a Chrome whose remote debugging is off.
                    opened.append(await spawn_chrome_without_debugging(data_dir, f"{site}/mine"))
                    await wait_for_profile(data_dir)
                released.set()
                await expect(card.locator(".note.bad")).to_contain_text("does not allow remote debugging")
                await expect(waiting).to_be_hidden()
                await expect(chrome_fact).to_have_text("running")
                await expect(card.get_by_role("button", name="Own Chrome")).to_have_attribute("aria-pressed", "true")
                await page.screenshot(path=tmp_path / f"after-{width}-{scheme}.png")
                await page.close()
        finally:
            await browser.close()
            for process in opened:
                await stop_process(process)
