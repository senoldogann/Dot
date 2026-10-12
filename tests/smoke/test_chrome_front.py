"""Dot's own Chrome stays behind the application in front (item 118). A real, visible Chrome on a throwaway profile
drives the pages of a local fixture server while Finder is in front: a thread's tab, a second thread's tab and a quiet
read are opened in the background and leave the front where it was; a tab the page opens itself takes the front by
Chrome's own doing, and Dot gives it back. The front is read with ``lsappinfo``, which needs no permission; giving it
back needs accessibility, so that test is skipped without it. It changes what is in front of the screen, so it is
marked ``desktop`` as well and stays out of a run that leaves the screen alone. macOS only."""

import asyncio
import re
import subprocess
import sys
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.browser.safety import refuse_secret_field
from dotagent.browser.session import BrowserSession
from dotagent.browser.types import PageView, Step
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config
from dotagent.desktop.perms import check_permissions

pytestmark = [
    pytest.mark.browser,
    pytest.mark.desktop,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(sys.platform != "darwin", reason="the front of the Mac"),
]

# Whose interface the browser never opens: the daemon's default address.
SERVER: ServerConfig = load_default_config().server
_PAGE: bytes = (
    b"<!doctype html><title>Front</title><h1>Front</h1><a href='/other' target='_blank'>Open the other page</a>"
)
_OTHER: bytes = b"<!doctype html><title>Other</title><h1>Other</h1>"
_CHROME: str = "Google Chrome"
_FINDER: str = "Finder"
# How long the front is watched after an action, and how often it is read.
_AFTER_S: float = 1.5
_POLL_S: float = 0.1


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body: bytes = _OTHER if self.path == "/other" else _PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _front() -> str:
    """The name of the application in front, as Launch Services has it."""
    asn: str = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, check=True).stdout.strip()
    info: str = subprocess.run(
        ["lsappinfo", "info", "-only", "name", asn], capture_output=True, text=True, check=True
    ).stdout
    return info.strip().split("=")[-1].strip('"')


def _open_finder() -> None:
    subprocess.run(["open", "-a", _FINDER], check=True)


async def _finder_in_front() -> None:
    await asyncio.to_thread(_open_finder)
    for _ in range(40):
        if await asyncio.to_thread(_front) == _FINDER:
            return
        await asyncio.sleep(_POLL_S)
    pytest.skip("Finder did not come to the front: no window server session")


async def _fronts_during(action: Callable[[], Awaitable[object]]) -> set[str]:
    """The applications that had the front while ``action`` ran and for :data:`_AFTER_S` after it."""
    seen: set[str] = set()
    task: asyncio.Task[object] = asyncio.ensure_future(action())
    while not task.done():
        seen.add(await asyncio.to_thread(_front))
        await asyncio.sleep(_POLL_S)
    await task
    for _ in range(int(_AFTER_S / _POLL_S)):
        seen.add(await asyncio.to_thread(_front))
        await asyncio.sleep(_POLL_S)
    return seen


def _ref_of(body: str, label: str) -> str:
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


@pytest.fixture(scope="module")
def config(tmp_path_factory: pytest.TempPathFactory) -> BrowserConfig:
    settings: Path = tmp_path_factory.mktemp("settings") / "config.toml"
    settings.write_text("[browser]\nheadless = false\nraise_window = false\n", encoding="utf-8")
    return load_config(settings).browser


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(
    config: BrowserConfig, tmp_path_factory: pytest.TempPathFactory, site: str
) -> AsyncIterator[BrowserSession]:
    session: BrowserSession = BrowserSession(
        config, SERVER, tmp_path_factory.mktemp("profile"), tmp_path_factory.mktemp("downloads")
    )
    # The start takes the front once (macOS starts an application in front) and gives it back; it is item 97's.
    await session.open_url("first", site)
    yield session
    await session.aclose()


async def test_the_tabs_dot_opens_and_a_quiet_read_leave_the_front_where_it_was(
    browser: BrowserSession, site: str
) -> None:
    await _finder_in_front()
    fronts: set[str] = await _fronts_during(lambda: browser.new_tab("first", f"{site}/other"))
    fronts |= await _fronts_during(lambda: browser.open_url("second", site))
    fronts |= await _fronts_during(lambda: browser.read_page(f"{site}/other"))
    assert fronts == {_FINDER}


@pytest.mark.skipif(not check_permissions().accessibility, reason="giving the front back needs accessibility")
async def test_a_tab_the_page_opens_takes_the_front_and_dot_gives_it_back(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("popup", site)
    await _finder_in_front()
    step: Step = Step(action="click", ref=_ref_of(view.body, 'link "Open the other page"'))
    outcome = await browser.act("popup", [step], False, refuse_secret_field)
    assert len(outcome.view.tabs) == 2, outcome.view.tabs
    for _ in range(int(3.0 / _POLL_S)):
        if await asyncio.to_thread(_front) == _FINDER:
            break
        await asyncio.sleep(_POLL_S)
    assert await asyncio.to_thread(_front) == _FINDER
