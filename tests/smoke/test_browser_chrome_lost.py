"""Chrome quitting or crashing under a run's tabs, and a quiet read whose tab cannot be closed (item 1297), against a
real headless Chrome on a local page."""

import asyncio
import os
import signal
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.browser.session import TABS_LOST, BrowserSession, no_runs_at_work
from dotagent.browser.types import PageView
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config
from dotagent.core.errors import BrowserError

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

SERVER: ServerConfig = load_default_config().server


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body: bytes = b"<html><head><title>Page</title></head><body><h1>A page</h1><button>Go</button></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def browser_config(tmp_path_factory: pytest.TempPathFactory) -> BrowserConfig:
    settings: Path = tmp_path_factory.mktemp("settings") / "config.toml"
    settings.write_text("[browser]\nheadless = true\n", encoding="utf-8")
    return load_config(settings).browser


@pytest_asyncio.fixture(loop_scope="module")
async def session(
    browser_config: BrowserConfig, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[BrowserSession]:
    profile: Path = tmp_path_factory.mktemp("profile")
    opened: BrowserSession = BrowserSession(
        browser_config, SERVER, profile, tmp_path_factory.mktemp("downloads"), no_runs_at_work
    )
    yield opened
    await opened.aclose()


async def _kill_chrome(session: BrowserSession) -> None:
    holder: int | None = profile_holder_pid(session.profile_dir)
    assert holder is not None
    os.kill(holder, signal.SIGKILL)
    for _ in range(100):
        if session._context is None:
            return
        await asyncio.sleep(0.1)
    raise AssertionError("the session never learned that Chrome was gone")


async def test_a_run_whose_chrome_crashed_is_told_its_tabs_are_gone_once_and_not_given_a_blank_tab(
    session: BrowserSession, site: str
) -> None:
    await session.open_url("lost", f"{site}/", True)
    await session.open_url("other", f"{site}/", True)
    await _kill_chrome(session)

    with pytest.raises(BrowserError) as told:
        await session.snapshot("lost", True)

    assert told.value.message == TABS_LOST
    # Told once: the next call goes on, in a new tab.
    view: PageView = await session.snapshot("lost", True)
    assert view.url in ("about:blank", "")
    # The other chat held tabs too, and is told on its own first call.
    with pytest.raises(BrowserError, match="your tabs and refs are gone"):
        await session.snapshot("other", True)


async def test_a_call_that_opens_a_page_goes_on_after_a_crash_and_the_page_carries_the_note(
    session: BrowserSession, site: str
) -> None:
    await session.open_url("opens", f"{site}/", True)
    await _kill_chrome(session)

    view: PageView = await session.open_url("opens", f"{site}/", True)

    assert "A page" in view.body
    assert TABS_LOST in view.notes, view.notes
    # And the note is said once: the next view does not repeat it.
    again: PageView = await session.snapshot("opens", True)
    assert TABS_LOST not in again.notes


async def test_a_chat_that_held_no_tab_when_chrome_crashed_is_told_nothing(session: BrowserSession, site: str) -> None:
    await session.open_url("held", f"{site}/", True)
    await session.open_url("quiet", f"{site}/", True)
    await session.end_of_run("quiet", keep_current=False)
    await _kill_chrome(session)

    view: PageView = await session.open_url("quiet", f"{site}/", True)

    assert TABS_LOST not in view.notes


async def test_a_quiet_read_is_given_back_when_its_tab_cannot_be_closed_and_the_reaper_closes_it_later(
    session: BrowserSession, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    closing = session._close_scratch
    attempts: list[str] = []

    async def refuse(page: object) -> None:
        attempts.append("refused")
        raise BrowserError("closing a tab failed", {"error": "Chrome did not close the tab within 10 s"})

    monkeypatch.setattr(session, "_close_scratch", refuse)

    document = await session.read_page(f"{site}/", public_only=False)

    assert "A page" in document.html
    assert attempts == ["refused"]
    assert len(session._stray_scratch) == 1

    monkeypatch.setattr(session, "_close_scratch", closing)
    await session._close_strays()

    assert session._stray_scratch == set()
