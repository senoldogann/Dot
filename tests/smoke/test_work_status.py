"""The work told in one line (item 663). With Settings › Interface › Work in a chat on "What it is doing", a run's steps
and thoughts are out of the conversation, and one line under it says what the run does now, in the words of its
latest event ("Running a command…" while a command runs); pressed, it shows the steps again, and once the run is over it
offers them. With the default, every step shows and the line does not. Measured in Chrome on a real run of a scripted
model whose command takes a few seconds."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "work-status-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TURNS: tuple[ScriptedTurn, ...] = (
    ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 6"}),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)),
)
# The words the line has for a command, whichever of them stands at the moment it is read.
_COMMAND_WORDS: str = r"^(Running a command|Working in the terminal)…$"


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[RunManager, str, int]]:
    """A daemon whose scripted model runs a command of a few seconds and answers: its run manager, address and port."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, list(_TURNS)))
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
            yield manager, f"http://127.0.0.1:{port}", port
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def _start(manager: RunManager) -> RunRecord:
    return await manager.start(
        "List the folder slowly",
        "task",
        SCRIPTED_MODEL,
        None,
        "test",
        None,
        None,
        "standard",
        None,
        "yolo",
        False,
        (),
    )


async def _open(browser: Browser, port: int, address: str, run_id: str, work: str | None, width: int) -> Page:
    page: Page = await browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce")
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    if work is not None:
        await page.add_init_script(f"localStorage.setItem('dot.work', '{work}')")
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    return page


async def test_the_line_says_what_the_run_does_and_hides_the_steps_until_asked(own_home: Path, tmp_path: Path) -> None:
    async with _served(own_home) as (manager, address, port):
        run: RunRecord = await _start(manager)
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page: Page = await _open(browser, port, address, run.id, "status", 1280)
                line = page.locator(".work-status")
                words = line.locator(".work-status-words")
                await expect(line).to_have_attribute("data-state", "working")
                await expect(words).to_have_text(re.compile(_COMMAND_WORDS))
                await expect(page.locator("details.steps")).to_be_hidden()
                await page.screenshot(path=str(tmp_path / "working.png"))
                # Pressed while the run works, the line shows the steps; pressed again, it folds them back.
                await line.click()
                await expect(page.locator("details.steps").first).to_be_visible()
                await expect(line).to_have_attribute("aria-pressed", "true")
                await line.click()
                await expect(page.locator("details.steps")).to_be_hidden()
                async with asyncio.timeout(30):
                    assert (await manager.wait(run.id)).status == "done"
                await expect(line).to_have_attribute("data-state", "over")
                await expect(line).to_have_text("Show the steps")
                await expect(page.locator(".answer .prose")).to_have_text("Done.")
                await line.click()
                await expect(page.locator("details.steps").first).to_be_visible()
                await expect(line).to_have_text("Hide the steps")
                await page.close()
                # On a phone's width the line is there, and the steps fold out of the conversation as wide.
                phone: Page = await _open(browser, port, address, run.id, "status", 390)
                await expect(phone.locator(".work-status")).to_have_text("Show the steps")
                await expect(phone.locator("details.steps")).to_be_hidden()
                await phone.screenshot(path=str(tmp_path / "phone.png"))
                await phone.close()
            finally:
                await browser.close()


async def test_by_default_every_step_shows_and_the_line_does_not(own_home: Path) -> None:
    async with _served(own_home) as (manager, address, port):
        run: RunRecord = await _start(manager)
        async with asyncio.timeout(30):
            assert (await manager.wait(run.id)).status == "done"
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page: Page = await _open(browser, port, address, run.id, None, 1280)
                await expect(page.locator(".answer .prose")).to_have_text("Done.")
                await expect(page.locator("details.steps").first).to_be_visible()
                await expect(page.locator(".work-status")).to_be_hidden()
                await page.close()
            finally:
                await browser.close()
