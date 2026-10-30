"""A note a run saves, corrects or forgets is a quiet line in the conversation (item 313, after Grok's "Updated
memory"): what changed and the note's first words, where it happened, as the run works and again from the history; a
note saved again and a call that failed leave none. The line of a saved or corrected note opens Settings › Memory with
that note first, and one forgotten since is said to be gone. A real daemon whose scripted model calls the real memory
tools, in Chrome, wide and on a phone, light and dark. Before item 313 the conversation showed none of it: the calls
were rows among the steps."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect
from row_menu import more_of

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.memory.service import forget_memory, save_memory
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import MemoryRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "memory-line-test-token"
WAIT_S: float = 30.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
INVOICES: str = "The user keeps the invoices in ~/Documents/Invoices, one folder per year."
REPORT: str = "The weekly report goes to Ayşe."
REPORT_NOW: str = "The weekly report goes to Ayşe and Mert, every Friday before noon."
OFFICE: str = "The office is closed on Friday."
KEPT: str = "The user prefers short answers."
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


class Held:
    """The scripted model with its first reply held back until the test lets it go, so that the page is open before
    the run changes the memory."""

    def __init__(self, turns: list[ScriptedTurn]) -> None:
        self.go: asyncio.Event = asyncio.Event()
        self._script: ScriptedProvider = ScriptedProvider(_PROVIDER, turns)

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        await self.go.wait()
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


def _turns(report_id: str, office_id: str) -> list[ScriptedTurn]:
    """One call a turn, so that the lines come in this order: a new note, a correction, a removal, a note saved again
    and a removal of a note that is not there; then the run ends."""
    calls: tuple[ScriptedCall, ...] = (
        ScriptedCall(tool="memory_save", arguments={"content": INVOICES, "tags": ["work"]}),
        ScriptedCall(tool="memory_update", arguments={"memory_id": report_id, "content": REPORT_NOW, "tags": []}),
        ScriptedCall(tool="memory_forget", arguments={"memory_id": office_id}),
        ScriptedCall(tool="memory_save", arguments={"content": KEPT, "tags": []}),
        ScriptedCall(tool="memory_forget", arguments={"memory_id": "mem_missing"}),
        ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Noted.", "success": True}),
    )
    return [ScriptedTurn(text="", calls=(call,)) for call in calls]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, RunManager, Held, str]]:
    """The daemon in the home of the environment, with the owner's notes the run works on and a model that plays
    ``_turns`` once let go, served at the address it yields."""
    runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
    try:
        report: MemoryRecord = (await save_memory(runtime.memory, REPORT, [], None, None, None, False)).record
        office: MemoryRecord = (await save_memory(runtime.memory, OFFICE, [], None, None, None, False)).record
        await save_memory(runtime.memory, KEPT, [], None, None, None, False)
        held: Held = Held(_turns(report.id, office.id))
        await runtime.router.register(_PROVIDER, held)
        manager: RunManager = RunManager(runtime)
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
            yield runtime, manager, held, f"http://127.0.0.1:{port}"
        finally:
            held.go.set()
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


async def _ended(runtime: Runtime, run_id: str) -> RunRecord:
    deadline: float = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        run: RunRecord = await runtime.store.get_run(run_id)
        if run.status in ("done", "failed", "stopped"):
            return run
        await asyncio.sleep(0.1)
    raise AssertionError(f"the run did not end in {WAIT_S} s")


async def _open(browser: Browser, address: str, path: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    await page.add_init_script("localStorage.setItem('dot.work', 'steps')")  # the steps this test reads (item 830)
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#{path}")
    return page


async def _expect_lines(lines: Locator, saved_id: str, report_id: str) -> None:
    """The three lines, in the order the run changed the memory, and where the two of a note still kept lead."""
    await expect(lines).to_have_count(3)
    await expect(lines.locator(".memory-change")).to_have_text(
        ["Saved to memory", "Updated memory", "Removed from memory"]
    )
    await expect(lines.locator(".memory-words")).to_have_text([f"“{INVOICES}”", f"“{REPORT_NOW}”", f"“{OFFICE}”"])
    await expect(lines.nth(0)).to_have_attribute("href", f"#/settings/memory?note={saved_id}")
    await expect(lines.nth(1)).to_have_attribute("href", f"#/settings/memory?note={report_id}")
    assert await lines.nth(2).evaluate("(node) => node.tagName") == "DIV"


@pytest.mark.browser
async def test_a_change_of_the_memory_is_a_quiet_line_that_leads_to_the_note(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, held, address), async_playwright() as playwright:
            report_id: str = next(
                record.id for record in await runtime.store.list_memories(10, 0) if record.content == REPORT
            )
            run: RunRecord = await manager.start(
                "Tidy my notes", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, None, False, ()
            )
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await _open(browser, address, f"/runs/{run.id}", 1280, "light")
                lines: Locator = page.locator(".timeline .memory-line")
                # The run's first event is drawn: it starts with the owner's three notes, a line of how it was set up,
                # which the side panel's Run tab holds (item 365).
                await expect(page.locator("#panel-run-setup")).to_contain_text("Recalled 3 memories")
                await expect(lines).to_have_count(0)
                # As the run works: the lines come in while the page is open.
                held.go.set()
                assert (await _ended(runtime, run.id)).status == "done"
                saved_id: str = next(
                    record.id for record in await runtime.store.list_memories(10, 0) if record.content == INVOICES
                )
                await _expect_lines(lines, saved_id, report_id)
                await page.close()
                # From the history, wide and on a phone, light and dark.
                for width, scheme in _SHOTS:
                    page = await _open(browser, address, f"/runs/{run.id}", width, scheme)
                    lines = page.locator(".timeline .memory-line")
                    await _expect_lines(lines, saved_id, report_id)
                    await expect(lines.first).to_be_visible()
                    await page.screenshot(path=str(tmp_path / f"line-{width}-{scheme}.png"), animations="disabled")
                    await page.close()
                # The line of the corrected note opens it first in Settings › Memory, where it can be corrected.
                page = await _open(browser, address, f"/runs/{run.id}", 390, "light")
                await page.locator(".timeline .memory-line").nth(1).click()
                await expect(page).to_have_url(f"{address}/#/settings/memory?note={report_id}")
                asked: Locator = page.locator("[data-asked]")
                await expect(asked.locator(".set-section-title")).to_have_text("The note from the conversation")
                await expect(asked.locator(f"[data-memory='{report_id}']")).to_contain_text(REPORT_NOW)
                await expect(more_of(asked.locator(f"[data-memory='{report_id}']"))).to_be_visible()
                await page.screenshot(path=str(tmp_path / "asked-390-light.png"), animations="disabled")
                await page.close()
                # A note forgotten since is said to be gone.
                await forget_memory(runtime.memory, saved_id)
                page = await _open(browser, address, f"/settings/memory?note={saved_id}", 1280, "dark")
                await expect(page.locator("[data-asked]")).to_contain_text(
                    "This note is no longer in memory: it was forgotten since."
                )
                await page.close()
            finally:
                await browser.close()
