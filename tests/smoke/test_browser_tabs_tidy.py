"""A chat closes the tabs it is done with, and only its own (item 696): a background tab unused for
``background_tab_idle_s``, the background tabs when its run ends, every tab of a helper's chat, and the others at the
model's word. The tabs of another chat at work at the same time, and a tab the session never opened, stay.

A real headless Chrome drives pages served by a local fixture server.
"""

import asyncio
import re
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Page

from dotagent.browser.pages import Tab
from dotagent.browser.safety import refuse_secret_field
from dotagent.browser.session import BrowserSession
from dotagent.browser.types import PageView, Step
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

SERVER: ServerConfig = load_default_config().server
_IDLE_S: float = 0.3


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        name: str = self.path.strip("/") or "home"
        # A page with a button that opens another in a tab of its own.
        opens: str = "<button onclick=\"window.open('/popped')\">Open</button>" if name == "opens" else ""
        body: bytes = f"<!doctype html><title>{name}</title><h1>{name}</h1>{opens}".encode()
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
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def at_work() -> set[str]:
    """The chats whose run is at work, as the daemon's peers would say."""
    return set()


@pytest.fixture(scope="module")
def config(tmp_path_factory: pytest.TempPathFactory) -> BrowserConfig:
    settings: Path = tmp_path_factory.mktemp("settings") / "config.toml"
    settings.write_text(f"[browser]\nheadless = true\nbackground_tab_idle_s = {_IDLE_S}\n", encoding="utf-8")
    return load_config(settings).browser


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(
    config: BrowserConfig, tmp_path_factory: pytest.TempPathFactory, at_work: set[str]
) -> AsyncIterator[BrowserSession]:
    session: BrowserSession = BrowserSession(
        config,
        SERVER,
        tmp_path_factory.mktemp("profile"),
        tmp_path_factory.mktemp("downloads"),
        lambda: frozenset(at_work),
    )
    yield session
    await session.aclose()


def titles(view: PageView) -> list[str]:
    return [tab.title for tab in view.tabs]


async def test_a_background_tab_left_unused_closes_and_the_current_one_and_other_chats_stay(
    browser: BrowserSession, site: str, at_work: set[str]
) -> None:
    at_work.update({"mine", "theirs"})
    try:
        await browser.open_url("mine", f"{site}/one", True)
        await browser.new_tab("mine", f"{site}/two", True)
        await asyncio.sleep(_IDLE_S + 0.1)
        # Another chat at work at the same time, whose background tab is not idle yet.
        await browser.open_url("theirs", f"{site}/three", True)
        await browser.new_tab("theirs", f"{site}/four", True)
        await browser.switch_tab("theirs", 1, True)
        await browser.close_idle_background_tabs()
        mine: PageView = await browser.snapshot("mine", True)
        theirs: PageView = await browser.snapshot("theirs", True)
    finally:
        await browser.release_thread("mine")
        await browser.release_thread("theirs")
        at_work.clear()
    assert titles(mine) == ["two"]
    assert any("closed a tab no longer in use" in note and "/one" in note for note in mine.notes)
    assert titles(theirs) == ["three", "four"]


async def test_the_end_of_a_run_closes_its_background_tabs_and_keeps_the_current_one(
    browser: BrowserSession, site: str, at_work: set[str]
) -> None:
    await browser.open_url("chat", f"{site}/a", True)
    await browser.new_tab("chat", f"{site}/b", True)
    await browser.new_tab("chat", f"{site}/c", True)
    await browser.switch_tab("chat", 2, True)
    at_work.add("other")
    await browser.open_url("other", f"{site}/d", True)
    await browser.new_tab("other", f"{site}/e", True)
    try:
        await browser.end_of_run("chat", keep_current=True)
        chat: PageView = await browser.snapshot("chat", True)
        other: PageView = await browser.snapshot("other", True)
    finally:
        await browser.release_thread("chat")
        await browser.release_thread("other")
        at_work.clear()
    assert titles(chat) == ["b"]
    assert titles(other) == ["d", "e"]


async def test_a_helpers_chat_closes_all_its_tabs_and_a_chat_at_work_keeps_them(
    browser: BrowserSession, site: str, at_work: set[str]
) -> None:
    await browser.open_url("helper", f"{site}/h1", True)
    await browser.new_tab("helper", f"{site}/h2", True)
    await browser.open_url("busy", f"{site}/b1", True)
    await browser.new_tab("busy", f"{site}/b2", True)
    context = browser._context
    assert context is not None
    before: int = len(context.pages)
    at_work.add("busy")
    try:
        await browser.end_of_run("helper", keep_current=False)
        # A run of the chat at work (another one, or the next of its queue): nothing of it is closed.
        await browser.end_of_run("busy", keep_current=False)
        busy: PageView = await browser.snapshot("busy", True)
    finally:
        await browser.release_thread("busy")
        at_work.clear()
    assert len(context.pages) <= before - 2
    assert titles(busy) == ["b1", "b2"]
    assert browser.current_url("helper") is None


async def test_closing_the_other_tabs_leaves_the_current_one_and_a_tab_no_chat_opened(
    browser: BrowserSession, site: str
) -> None:
    await browser.open_url("closer", f"{site}/x", True)
    await browser.new_tab("closer", f"{site}/y", True)
    await browser.new_tab("closer", f"{site}/z", True)
    await browser.switch_tab("closer", 2, True)
    context = browser._context
    assert context is not None
    # A tab of the owner's, which no chat opened.
    owners = await context.new_page()
    await owners.goto(f"{site}/owners")
    try:
        view: PageView = await browser.close_tab("closer", None, True)
        assert titles(view) == ["y"]
        assert view.title == "y"
        assert not owners.is_closed()
    finally:
        await owners.close()
        await browser.release_thread("closer")


async def test_a_popup_on_its_way_while_the_tabs_are_made_new_for_a_login_does_not_join_the_clean_chat(
    browser: BrowserSession, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 697, from the security review: a tab a page opened is taken into its chat once registered. When the chat's
    tabs are made new for a login meanwhile (a script ran in them), the popup, which that script may have reached, is
    not taken into the chat that is clean now: it closes, as its opener did."""
    view: PageView = await browser.open_url("racy", f"{site}/opens", True)
    await browser.run_script("racy", "1 + 1", 5.0, True)
    registering = browser._register
    reset: list[PageView | None] = []
    done: asyncio.Event = asyncio.Event()
    popups: list[Page] = []

    async def slowly(tab: Tab) -> None:
        if not popups:
            # The chat's tabs are made new while the popup is still being registered.
            popups.append(tab.page)
            await asyncio.sleep(1.2)
            reset.append(await browser.fresh_for_login("racy", lambda address: True, ["username"]))
            await registering(tab)
            done.set()
            return
        await registering(tab)

    monkeypatch.setattr(browser, "_register", slowly)
    button: str = re.search(r'button "Open" \[ref=([^\]]+)\]', view.body).group(1)  # type: ignore[union-attr]
    await browser.act("racy", [Step(action="click", ref=button)], False, refuse_secret_field, True)
    await asyncio.wait_for(done.wait(), 10)
    await asyncio.sleep(0.3)
    after: PageView = await browser.snapshot("racy", True)
    try:
        assert [tab.title for tab in after.tabs] == ["opens"]
        assert reset[0] is not None and popups[0].is_closed()
    finally:
        await browser.release_thread("racy")
