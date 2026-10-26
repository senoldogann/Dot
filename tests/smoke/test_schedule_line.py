"""A schedule a run made is a line of the conversation (item 665): "Made a schedule", its name and when it runs in
words, a link that opens the Schedules page with its row marked and in sight. The line stays in sight when the chat
shows only what Dot is doing (item 663), and an earlier turn keeps it. Measured in Chrome on a real run of a scripted
model."""

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

TOKEN: str = "schedule-line-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_MADE: dict[str, str] = {"name": "Overnight outbound", "goal": "Draft the outbound emails", "cron": "0 9 * * 1"}
_TURNS: tuple[ScriptedTurn, ...] = (
    ScriptedTurn(text="", calls=(ScriptedCall(tool="schedule_create", arguments=dict(_MADE)),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Sure.", "success": True}),)),
)


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[RunManager, str, int]]:
    """A daemon whose scripted model makes a schedule and answers: its run manager, address and port."""
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


async def _run(manager: RunManager, goal: str, thread_id: str | None) -> RunRecord:
    run: RunRecord = await manager.start(
        goal, "task", SCRIPTED_MODEL, thread_id, "test", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(30):
        assert (await manager.wait(run.id)).status == "done"
    return run


async def _page(browser: Browser, port: int, address: str, route: str, work: str) -> Page:
    page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.add_init_script(f"localStorage.setItem('dot.work', '{work}')")
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    await page.goto(f"{address}/#/{route}")
    return page


async def test_a_schedule_the_run_made_is_a_line_that_leads_to_it(own_home: Path, tmp_path: Path) -> None:
    async with _served(own_home) as (manager, address, port):
        run: RunRecord = await _run(manager, "Run this every week", None)
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                for work in ("steps", "status"):
                    page: Page = await _page(browser, port, address, f"runs/{run.id}", work)
                    await expect(page.locator(".answer .prose")).to_have_text("Done.")
                    line = page.locator("a.schedule-line")
                    await expect(line).to_have_text("Made a schedule Overnight outbound · on Mondays at 09:00")
                    await expect(line).to_be_visible()
                    await page.screenshot(path=str(tmp_path / f"line-{work}.png"))
                    await page.close()
                page = await _page(browser, port, address, f"runs/{run.id}", "steps")
                await page.locator("a.schedule-line").click()
                await expect(page).to_have_url(re.compile(r"#/schedules\?schedule="))
                row = page.locator(".list-row.asked")
                await expect(row).to_have_count(1)
                await expect(row).to_contain_text("Overnight outbound")
                await expect(row).to_be_in_viewport()
                await page.close()
                # The chat goes on: the earlier turn keeps its line.
                second: RunRecord = await _run(manager, "Thanks", run.thread_id)
                page = await _page(browser, port, address, f"runs/{second.id}", "steps")
                await expect(page.locator(".answer .prose").last).to_have_text("Sure.")
                await expect(page.locator("a.schedule-line")).to_have_count(1)
                await page.close()
            finally:
                await browser.close()
