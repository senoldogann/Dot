"""Unread chats (item 131): a chat whose latest run ends while it is out of sight shows an accent dot and says unread
until it is opened; a run that ends in the open chat while the page is in view leaves no dot; Mark unread in a chat's
menu brings the dot back, which the daemon keeps while another chat is opened and over a reload, and Mark read clears
it."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import ApprovalRecord, RunRecord, ThreadSummary
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "unread-chats-token"
PHOTOS: str = "Sort the photos."
FOLDERS: str = "Name the folders."
SHELVES: str = "Label the shelves."
# The menu of a read chat whose run is over.
_MENU: list[str] = ["Pin", "Mark unread", "Rename", "Copy link", "Select", "Delete"]
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# Four runs, each finished at its first step.
_RUNS: int = 4
_RUN_WAIT_S: float = 30.0
_MARK_WAIT_S: float = 10.0


def _finish() -> ScriptedTurn:
    return ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the scripted model, so that the chats are only the ones made here."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_finish() for _ in range(_RUNS)]))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[tuple[RunManager, str]]:
    """The run manager and the address of a real server over it."""
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
            yield manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()


async def _ended(manager: RunManager, goal: str, thread_id: str | None) -> RunRecord:
    """``goal`` as a task on ``thread_id`` (a new chat for ``None``) that the scripted model finishes, once it is
    over."""
    run: RunRecord = await manager.start(
        goal, "task", SCRIPTED_MODEL, thread_id, "test", None, None, "standard", None, "ask", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended
    return ended


async def _until_unread(runtime: Runtime, thread_id: str, unread: bool) -> None:
    """Wait until the daemon holds the chat as ``unread`` or not: the page marks a chat without waiting for the
    answer."""
    deadline: float = time.monotonic() + _MARK_WAIT_S
    while time.monotonic() < deadline:
        chats: list[ThreadSummary] = await runtime.store.list_thread_summaries(50)
        if any(chat.id == thread_id and chat.unread == unread for chat in chats):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"the chat {thread_id} is not {'unread' if unread else 'read'} after {_MARK_WAIT_S} s")


def _row(page: Page, title: str) -> Locator:
    return page.locator(".side-runs .side-chat").filter(has_text=title)


async def _choose(page: Page, row: Locator, title: str, item: str) -> None:
    """Choose ``item`` in the menu of the chat ``title``, opened by its "…" button."""
    await row.hover()
    await row.get_by_role("button", name=f"More for {title}").click()
    await page.get_by_role("menu", name=title).get_by_role("menuitem", name=item).click()


async def test_a_chat_whose_run_ends_out_of_sight_is_unread_until_it_is_opened(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    photos_run: RunRecord = await _ended(manager, PHOTOS, None)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            context: BrowserContext = await browser.new_context(
                viewport={"width": 1280, "height": 800}, color_scheme="light", reduced_motion="reduce"
            )
            page: Page = await context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url(host, port, TOKEN))

            # The run ended while no page showed its chat: the chat is unread, an accent dot that says so.
            photos: Locator = _row(page, PHOTOS)
            photos_link: Locator = photos.locator("a.side-run")
            photos_dot: Locator = photos.locator(".side-unread")
            await expect(photos_dot).to_have_count(1)
            await expect(photos_dot).not_to_have_css("background-color", "rgba(0, 0, 0, 0)")
            await expect(photos_link).to_have_attribute("aria-label", f"{PHOTOS} (unread)")

            # Opened by a key, it is read, and the focus stays on its row; a run that ends in it while the page is in
            # view leaves no dot.
            await photos_link.focus()
            await page.keyboard.press("Enter")
            await expect(photos_link).to_have_attribute("aria-current", "page")
            await expect(photos_dot).to_have_count(0)
            await expect(photos_link).to_be_focused()
            await expect(photos_link).not_to_have_attribute("aria-label", f"{PHOTOS} (unread)")
            await _until_unread(runtime, photos_run.thread_id, False)
            await _ended(manager, "Sort them again.", photos_run.thread_id)
            await _until_unread(runtime, photos_run.thread_id, False)
            await expect(photos_dot).to_have_count(0)

            # A run that ends in another chat while this one is open leaves that chat unread.
            folders_run: RunRecord = await _ended(manager, FOLDERS, None)
            folders: Locator = _row(page, FOLDERS)
            folders_dot: Locator = folders.locator(".side-unread")
            await expect(folders_dot).to_have_count(1)
            await expect(photos_dot).to_have_count(0)
            await page.screenshot(path=str(tmp_path / "unread-1280-light.png"), animations="disabled")

            # Opened, it is read; its menu offers Mark unread, which brings the dot back while it is open. The daemon
            # keeps the mark while another chat is opened and over a reload.
            await folders.locator("a.side-run").click()
            await expect(folders_dot).to_have_count(0)
            await _until_unread(runtime, folders_run.thread_id, False)
            await folders.hover()
            await folders.get_by_role("button", name=f"More for {FOLDERS}").click()
            await expect(page.get_by_role("menu", name=FOLDERS).get_by_role("menuitem")).to_have_text(_MENU)
            await page.keyboard.press("Escape")
            await _choose(page, folders, FOLDERS, "Mark unread")
            await expect(folders_dot).to_have_count(1)
            await _until_unread(runtime, folders_run.thread_id, True)
            await photos_link.click()
            await expect(photos_link).to_have_attribute("aria-current", "page")
            await page.reload()
            await expect(folders_dot).to_have_count(1)
            await expect(photos_dot).to_have_count(0)
            await page.emulate_media(color_scheme="dark")
            await page.screenshot(path=str(tmp_path / "unread-1280-dark.png"), animations="disabled")

            # Mark read clears it.
            await _choose(page, folders, FOLDERS, "Mark read")
            await expect(folders_dot).to_have_count(0)
            await _until_unread(runtime, folders_run.thread_id, False)
            assert errors == []
        finally:
            await browser.close()


async def test_a_chat_that_waits_says_what_waits_and_shows_no_unread_dot_until_nothing_waits(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    """Item 836's pre-push review: a chat whose run ended out of sight is unread, but while a step of it waits for the
    owner its row says what waits, with the dot of a run that waits and no unread dot, and its name says no unread;
    once nothing waits, the chat is back among the days with its unread dot."""
    manager, address = served
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    run: RunRecord = await _ended(manager, SHELVES, None)
    assert run.thread_id is not None
    kept: ApprovalRecord = await runtime.store.create_kept_approval(
        run.id,
        "write_file",
        "Write shelves.md",
        "handoff",
        {"tool": "write_file", "kind": "write", "risk": "medium", "target": "shelves.md", "amount": None, "flags": []},
        "digest-836-unread",
        time.time() + 3600,
        run.thread_id,
        None,
    )
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url(host, port, TOKEN))
            waiting: Locator = page.locator("#sidebar [data-section=waiting] .side-chat").filter(has_text=SHELVES)
            link: Locator = waiting.locator("a.side-run")
            await expect(link.locator(".side-chat-waits")).to_have_text("Waiting for you: Write shelves.md")
            await expect(link.locator(".side-waits-dot")).to_have_count(1)
            await expect(link.locator(".side-unread")).to_have_count(0)
            label: str = await link.get_attribute("aria-label") or ""
            assert "unread" not in label, label
            await _until_unread(runtime, run.thread_id, True)
            await page.screenshot(path=str(tmp_path / "waiting-unread.png"), animations="disabled")
            # The step answered from the page: nothing waits, and the chat is among the days again, unread.
            await page.evaluate(
                "async (id) => { const { api } = await import('/js/api.js');"
                " await api.post(`/approvals/${id}/decision`, { approve: false }); }",
                kept.id,
            )
            back: Locator = _row(page, SHELVES)
            await expect(back.locator(".side-unread")).to_have_count(1)
            await expect(back.locator("a.side-run")).to_have_attribute("aria-label", f"{SHELVES} (unread)")
            await expect(waiting).to_have_count(0)
        finally:
            await browser.close()
