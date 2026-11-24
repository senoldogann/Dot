"""Firings skipped in a row (item 1401), where the owner looks: the chat's Automations say "Skipped 5 in a row" beside
the schedule's last result, and so does the row of the Schedules page, from two on (one skip is the last status's to
say) and while the last status is a skip, at 1280 and at 390 wide, light and dark, with nothing wider than the screen.
Served by a real server over a real store; no run is made."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler.plans import UNSET_OPTIONS
from dotagent.scheduler.runner import Scheduler
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, ScheduleRecord, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "schedule-skips-token"
OPUS: str = "anthropic:claude-opus-5-5"
_BUSY: str = "skipped: a run is already active on this thread"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{OPUS}"\n\n'
        # A tick past the nightly reflection's time would start it, and drain() would wait on a model the test lacks.
        '[agent]\ngoal_mode = false\nreflection_time = "off"\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def address(runtime: Runtime) -> AsyncIterator[str]:
    manager: RunManager = RunManager(runtime)
    scheduler: Scheduler = Scheduler(runtime, manager)
    stopping: asyncio.Event = asyncio.Event()
    listener = bind_listener("127.0.0.1", 0)
    port: int = listener.getsockname()[1]
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=scheduler,
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
        await scheduler.drain()
        await manager.shutdown()


async def _skipped(runtime: Runtime, name: str, times: int) -> tuple[ThreadRecord, RunRecord, ScheduleRecord]:
    """A chat with one task that ended, and a repeating schedule of the chat whose last ``times`` firings were skipped
    for a busy chat."""
    thread: ThreadRecord = await runtime.store.create_thread(name, "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id, name, "task", OPUS, None, "test", "medium", "standard", None, "yolo", False, None, None, None
    )
    await runtime.store.end_run(run.id, "done", "Done.", {"status": "done", "result": "Done.", "needs": ""})
    soon: float = time.time() + 600
    schedule: ScheduleRecord = await runtime.store.add_schedule(
        name, "*/10 * * * *", None, f"{name}: look again", "task", soon, UNSET_OPTIONS, thread.id, True
    )
    for _ in range(times):
        await runtime.store.count_schedule_skip(schedule.id, _BUSY)
    return thread, run, await runtime.store.get_schedule(schedule.id)


async def _page(browser: Browser, width: int, scheme: Literal["light", "dark"]) -> Page:
    phone: bool = width < 720
    return await browser.new_page(
        viewport={"width": width, "height": 900},
        color_scheme=scheme,
        reduced_motion="reduce",
        has_touch=phone,
        is_mobile=phone,
    )


async def test_the_chats_automations_and_the_schedules_row_say_how_many_firings_were_skipped_in_a_row(
    runtime: Runtime, address: str, tmp_path: Path
) -> None:
    _, run, five = await _skipped(runtime, "PR merge loop", 5)
    _, once_run, once = await _skipped(runtime, "Nightly check", 1)
    assert (five.skipped_in_row, once.skipped_in_row) == (5, 1)
    # A count whose status is no skip any more (a run by hand ended done meanwhile) is not said (the review's finding).
    _, _, answered = await _skipped(runtime, "Weekly digest", 4)
    await runtime.store.set_schedule_status(answered.id, "done")
    port: int = int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await _page(browser, width, scheme)
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.evaluate("localStorage.setItem('dot.inspector', 'on')")
                # The chat's Automations: the count beside the last result, and the plain word for a single skip.
                for chat_run, said in ((run, "Skipped 5 in a row"), (once_run, "Last firing skipped")):
                    await page.goto(f"{address}/#/runs/{chat_run.id}")
                    if width < 960:
                        await page.get_by_role("button", name="Task details").click()
                    await expect(page.locator(".inspector")).to_be_visible()
                    await page.locator(".inspector").get_by_role("tab", name="Task").click()
                    last: Locator = page.locator(".inspector-panel:not([hidden]) .automations .automation-last")
                    await expect(last).to_have_text(said)
                    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    await page.screenshot(path=str(tmp_path / f"automations-{width}-{scheme}-{chat_run.id}.png"))
                # The Schedules page: the badge from two on, beside the last status, none for a single skip.
                await page.goto(f"{address}/#/settings/schedules")
                row: Locator = page.locator(f'[data-schedule="{five.id}"]')
                badge: Locator = row.locator(".badge", has_text="Skipped 5 in a row")
                await expect(badge).to_be_visible()
                await expect(row.locator(".badge", has_text="skipped: a run is already active")).to_be_visible()
                await expect(
                    page.locator(f'[data-schedule="{once.id}"]').locator(".badge", has_text="in a row")
                ).to_have_count(0)
                await expect(page.locator(f'[data-schedule="{answered.id}"]')).to_be_visible()
                await expect(
                    page.locator(f'[data-schedule="{answered.id}"]').locator(".badge", has_text="in a row")
                ).to_have_count(0)
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await page.screenshot(path=str(tmp_path / f"schedules-{width}-{scheme}.png"), full_page=True)
                await page.close()
        finally:
            await browser.close()
