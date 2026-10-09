"""Working in the Chrome that is already open (attach mode), against a real scratch Chrome.

The scratch Chrome plays the Chrome of the user: hidden, on its own temporary data directory, with a debugging port and
one tab of "the user" open. What only the real Chrome of the user can show, the Allow dialog that it puts up when a
program connects, is not part of this: only the user can click it.
"""

import asyncio
import io
import re
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from PIL import Image
from pydantic import BaseModel, TypeAdapter

from dotagent.browser.attach import DebugEndpoint, parse_port_file
from dotagent.browser.keychain import KeychainMode, read_keychain_mode
from dotagent.browser.login import chrome_arguments
from dotagent.browser.modes import adopt_mode, browser_data_dir
from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.browser.safety import refuse_secret_field
from dotagent.browser.session import BrowserSession
from dotagent.browser.types import ActOutcome, Attachment, PageView, Screenshot, Step
from dotagent.config import BrowserConfig, load_config
from dotagent.core.errors import BrowserError
from dotagent.core.paths import ensure_layout

pytestmark = pytest.mark.browser

_PORT_FILE: str = "DevToolsActivePort"
_READY_TIMEOUT_S: float = 30.0
_EXIT_TIMEOUT_S: float = 20.0
_TABS_TIMEOUT_S: float = 10.0
_POLL_S: float = 0.1
# Events that Chrome sends when a tab opens or when it quits arrive a few milliseconds after the fact.
_EVENT_GRACE_S: float = 0.5
_PAGES: dict[str, str] = {
    "/": """<!doctype html><title>Home</title><h1>Dot attach fixture</h1>
<button onclick="document.getElementById('n').textContent = String(Number(document.getElementById('n').textContent) + 1)">Increment</button>
<span id="n">0</span>
<a href="/popup" target="_blank">Open popup</a>""",
    "/mine": "<!doctype html><title>Mine</title><h1>A page of the user</h1>",
    "/other": "<!doctype html><title>Other</title><h1>Another page of the user</h1>",
    "/popup": "<!doctype html><title>Popup</title><h1>Popup page</h1>",
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


async def start_chrome(data_dir: Path, url: str) -> ScratchChrome:
    """Start a hidden Chrome on ``data_dir`` with one tab on ``url``; Chrome picks its debugging port itself."""
    keychain: KeychainMode = read_keychain_mode(data_dir)
    command: list[str] = [*chrome_arguments(data_dir, url, keychain), "--headless=new", "--remote-debugging-port=0"]
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
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
    """Quit Chrome the way the system asks a program to quit; a Chrome that does not quit in time is killed."""
    if chrome.process.returncode is None:
        chrome.process.terminate()
    try:
        await asyncio.wait_for(chrome.process.wait(), _EXIT_TIMEOUT_S)
    except TimeoutError:
        chrome.process.kill()
        await chrome.process.wait()
        raise


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


async def test_the_agent_works_in_tabs_of_its_own_and_the_tabs_of_the_user_stay(
    chrome: ScratchChrome, attach_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    session: BrowserSession = BrowserSession(attach_config, chrome.data_dir, tmp_path / "downloads")
    try:
        attachment: Attachment = await session.attachment()
        assert re.match(r"\d+\.", attachment.browser_version), attachment
        assert attachment.open_tabs == 1
        view: PageView = await session.open_url("first", f"{site}/")
        assert view.title == "Home"
        assert [tab.url for tab in view.tabs] == [f"{site}/"]
        clicked: ActOutcome = await session.act(
            "first", [Step(action="click", ref=ref_of(view.body, 'button "Increment"'))], False, refuse_secret_field
        )
        assert [report.outcome for report in clicked.steps] == ["done"]
        assert clicked.view.mode == "delta"
        await session.open_url("second", f"{site}/popup")
        await wait_for_tabs(chrome.port, [f"{site}/mine", f"{site}/", f"{site}/popup"])

        shot: Screenshot = await session.screenshot("first")
        assert Image.open(io.BytesIO(shot.data)).size == (shot.width, shot.height)
        assert (shot.width, shot.height) != (attach_config.viewport_width, attach_config.viewport_height)

        await session.release_thread("first")
        await wait_for_tabs(chrome.port, [f"{site}/mine", f"{site}/popup"])
    finally:
        await session.aclose()
    await wait_for_tabs(chrome.port, [f"{site}/mine"])
    assert chrome.process.returncode is None
    assert profile_holder_pid(chrome.data_dir) == chrome.process.pid


async def test_a_tab_the_user_opens_meanwhile_is_not_taken_over(
    chrome: ScratchChrome, attach_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    session: BrowserSession = BrowserSession(attach_config, chrome.data_dir, tmp_path / "downloads")
    try:
        await session.open_url("agent", f"{site}/")
        await open_tab_as_the_user(chrome.port, f"{site}/other")
        await wait_for_tabs(chrome.port, [f"{site}/mine", f"{site}/other", f"{site}/"])
        await asyncio.sleep(_EVENT_GRACE_S)
        view: PageView = await session.snapshot("agent")
        assert [tab.url for tab in view.tabs] == [f"{site}/"]
    finally:
        await session.aclose()
    await wait_for_tabs(chrome.port, [f"{site}/mine", f"{site}/other"])


async def test_a_tab_that_a_tab_of_the_agent_opens_is_the_agents_and_closes_with_it(
    chrome: ScratchChrome, attach_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    session: BrowserSession = BrowserSession(attach_config, chrome.data_dir, tmp_path / "downloads")
    try:
        view: PageView = await session.open_url("agent", f"{site}/")
        opened: ActOutcome = await session.act(
            "agent", [Step(action="click", ref=ref_of(view.body, 'link "Open popup"'))], False, refuse_secret_field
        )
        assert [tab.url for tab in opened.view.tabs] == [f"{site}/", f"{site}/popup"]
        await wait_for_tabs(chrome.port, [f"{site}/mine", f"{site}/", f"{site}/popup"])
    finally:
        await session.aclose()
    await wait_for_tabs(chrome.port, [f"{site}/mine"])


async def test_a_session_reports_that_chrome_has_quit_and_works_again_when_chrome_is_back(
    data_dir: Path, attach_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    first: ScratchChrome = await start_chrome(data_dir, f"{site}/mine")
    session: BrowserSession = BrowserSession(attach_config, data_dir, tmp_path / "downloads")
    try:
        await session.open_url("agent", f"{site}/")
        await stop_chrome(first)
        assert profile_holder_pid(data_dir) is None
        await asyncio.sleep(_EVENT_GRACE_S)
        with pytest.raises(BrowserError, match="Chrome is not running; open Chrome"):
            await session.snapshot("agent")
        second: ScratchChrome = await start_chrome(data_dir, f"{site}/mine")
        try:
            view: PageView = await session.open_url("agent", f"{site}/")
            assert view.title == "Home"
            assert await open_tabs(second.port) == sorted([f"{site}/mine", f"{site}/"])
        finally:
            await stop_chrome(second)
    finally:
        await session.aclose()


async def test_a_connection_that_chrome_refuses_is_reported_without_the_secret_path(
    chrome: ScratchChrome, attach_config: BrowserConfig, tmp_path: Path
) -> None:
    wrong: str = "00000000-0000-4000-8000-000000000000"
    (chrome.data_dir / _PORT_FILE).write_text(f"{chrome.port}\n/devtools/browser/{wrong}\n", encoding="utf-8")
    session: BrowserSession = BrowserSession(attach_config, chrome.data_dir, tmp_path / "downloads")
    try:
        with pytest.raises(BrowserError, match="did not accept the connection") as raised:
            await session.attachment()
    finally:
        await session.aclose()
    assert wrong not in str(raised.value)
    assert raised.value.context["port"] == chrome.port


async def test_a_session_moves_between_dots_own_chrome_and_the_chrome_of_the_user(
    chrome: ScratchChrome, own_config: BrowserConfig, own_home: Path, site: str
) -> None:
    own_profile: Path = browser_data_dir("own")
    session: BrowserSession = BrowserSession(own_config, own_profile, own_home / "downloads")
    try:
        assert (session.mode, session.connected) == ("own", False)
        home_page: PageView = await session.open_url("own", f"{site}/")
        assert home_page.title == "Home"
        assert session.connected
        assert profile_holder_pid(own_profile) is not None

        await adopt_mode(session, "attach")
        assert (session.mode, session.profile_dir) == ("attach", chrome.data_dir)
        assert profile_holder_pid(own_profile) is None
        await wait_for_tabs(chrome.port, [f"{site}/mine"])
        popup: PageView = await session.open_url("agent", f"{site}/popup")
        assert popup.title == "Popup"
        await wait_for_tabs(chrome.port, [f"{site}/mine", f"{site}/popup"])

        await adopt_mode(session, "own")
        assert (session.mode, session.profile_dir, session.connected) == ("own", own_profile, False)
        await wait_for_tabs(chrome.port, [f"{site}/mine"])
        other: PageView = await session.open_url("own", f"{site}/other")
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
    own_profile: Path = browser_data_dir("own")
    session: BrowserSession = BrowserSession(own_config, own_profile, own_home / "downloads")
    try:
        with pytest.raises(BrowserError, match="did not accept the connection"):
            await adopt_mode(session, "attach")
        assert (session.mode, session.profile_dir, session.connected) == ("own", own_profile, False)
    finally:
        await session.aclose()
