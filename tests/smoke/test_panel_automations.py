"""The Automations of a chat in its side panel (item 1334): the Task tab lists the schedules and loops of this chat, and
no other chat's, each a calendar-clock mark with a green dot while it is on, its title over how it repeats or when it
runs and how it stands ("Every 6 hours · paused", "Once, Thursday at 19:31", "Loops every day at 09:00 · next ..."), a
pencil that opens the schedule's editor on the Schedules page, once (the address forgets it, a reload does not open it
again), a play that runs it now, and a switch that pauses it or puts it back, which the scheduler reads at the
schedule's next firing; the head has a way to all the schedules. A chat with none shows no section. Served by a real
server with a real scheduler over a real store, beside the conversation and as a
sheet on a phone, light and dark; the model finishes at once, nothing else is called.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler.runner import Scheduler
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, ScheduleOptions, ScheduleRecord, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "panel-automations-token"
OPUS: str = "anthropic:claude-opus-5-5"
_WAIT_S: float = 60.0
_DAY_S: float = 86400.0
_PLAIN: ScheduleOptions = ScheduleOptions(
    model=None,
    effort=None,
    speed=None,
    approvals=None,
    goal_mode=None,
    timezone=None,
    history_runs=None,
    # A schedule a run made in the chat keeps that chat (item 291): its firings, Run now too, go on in it.
    same_chat=True,
    stop_after_failures=None,
    notify=None,
    agent_id=None,
)
_SHOTS: tuple[tuple[Literal["chromium", "webkit"], int, Literal["light", "dark"]], ...] = (
    ("chromium", 1280, "light"),
    ("chromium", 1280, "dark"),
    ("webkit", 390, "light"),
    ("webkit", 390, "dark"),
)


class _Finishes:
    """A model that finishes every task at once."""

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        answer: JsonObject = {"answer": "Done.", "success": True}
        finish: ScriptedTurn = ScriptedTurn(text="", calls=(ScriptedCall(tool="finish", arguments=answer),))
        return await ScriptedProvider(self.name, [finish]).complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return ModelInfo(
            id=model,
            vision=True,
            tools=True,
            thinking=True,
            context_length=1_000_000,
            own_context_length=None,
            window_source="provider",
            efforts=("low", "medium", "high"),
            free=False,
        )

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return (await self.model_info("claude-opus-5-5"),)

    async def aclose(self) -> None:
        return None


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
    await built.router.register("anthropic", _Finishes())
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[tuple[RunManager, Scheduler, str]]:
    """The run manager, the scheduler and the address of a real server over them."""
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
        yield manager, scheduler, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await scheduler.drain()
        await manager.shutdown()


async def _chat(runtime: Runtime, title: str) -> tuple[ThreadRecord, RunRecord]:
    """A chat with one task that ended."""
    thread: ThreadRecord = await runtime.store.create_thread(title, "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id, title, "task", OPUS, None, "test", "medium", "standard", None, "yolo", False, None, None, None
    )
    await runtime.store.end_run(run.id, "done", "Done.", {"status": "done", "result": "Done.", "needs": ""})
    return thread, run


async def _schedule(
    runtime: Runtime, name: str, thread: ThreadRecord, *, cron: str | None, enabled: bool, next_in_s: float
) -> ScheduleRecord:
    at: float = time.time() + next_in_s
    return await runtime.store.add_schedule(
        name,
        cron,
        None if cron is not None else at,
        f"{name}: look again",
        "task",
        at if enabled else None,
        _PLAIN,
        thread.id,
        enabled,
    )


async def _open(
    browser: Browser, address: str, run_id: str, width: int, scheme: Literal["light", "dark"], touch: bool
) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900},
        color_scheme=scheme,
        reduced_motion="reduce",
        has_touch=touch,
        is_mobile=touch,
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await page.evaluate("localStorage.setItem('dot.inspector', 'on')")
    await page.goto(f"{address}/#/runs/{run_id}")
    if width < 960:
        await page.get_by_role("button", name="Task details").click()
    await expect(page.locator(".inspector")).to_be_visible()
    await page.locator(".inspector").get_by_role("tab", name="Task").click()
    return page


async def test_the_task_tab_lists_the_chats_schedules_and_loops_and_switches_and_runs_them(
    runtime: Runtime, served: tuple[RunManager, Scheduler, str], tmp_path: Path
) -> None:
    manager, scheduler, address = served
    chat, run = await _chat(runtime, "Ship the dictation work")
    other, _ = await _chat(runtime, "Plan the trip")
    loop: ScheduleRecord = await _schedule(
        runtime, "Loop: check the build", chat, cron="0 9 * * *", enabled=True, next_in_s=_DAY_S / 4
    )
    once: ScheduleRecord = await _schedule(runtime, "Reddit comments", chat, cron=None, enabled=True, next_in_s=_DAY_S)
    paused: ScheduleRecord = await _schedule(
        runtime, "iPhone build check", chat, cron="0 */6 * * *", enabled=False, next_in_s=_DAY_S / 2
    )
    await _schedule(runtime, "Book the train", other, cron=None, enabled=True, next_in_s=_DAY_S)
    lonely, lonely_run = await _chat(runtime, "Read the brief")
    assert lonely.id != chat.id
    async with async_playwright() as playwright:
        for index, (engine, width, scheme) in enumerate(_SHOTS):
            browser: Browser = await getattr(playwright, engine).launch(headless=True)
            try:
                page: Page = await _open(browser, address, run.id, width, scheme, engine == "webkit")
                section: Locator = page.locator(".inspector-panel:not([hidden]) .automations")
                await expect(section.locator(".panel-section-title")).to_have_text("Automations3")
                rows: Locator = section.locator(".automation-row")
                # Those of this chat and no other's.
                titles: list[str] = await rows.locator(".automation-title").all_text_contents()
                assert sorted(titles) == ["Reddit comments", "check the build", "iPhone build check"], titles
                if index == 0:
                    # Switched-on ones first and soonest first.
                    await expect(rows.locator(".automation-title")).to_have_text(
                        ["check the build", "Reddit comments", "iPhone build check"]
                    )
                    await expect(rows.locator(".automation-line")).to_have_text(
                        [
                            re.compile(r"^Loops every day at 09:00 · next "),
                            re.compile(r"^Once, "),
                            "Every 6 hours · paused",
                        ]
                    )
                    states: list[str] = [
                        await dot.get_attribute("data-state") or ""
                        for dot in await rows.locator(".automation-dot").all()
                    ]
                    assert states == ["on", "on", "off"], states
                    assert [
                        await s.get_attribute("aria-checked") for s in await rows.locator(".automation-switch").all()
                    ] == [
                        "true",
                        "true",
                        "false",
                    ]
                    # The pencil leads to the editor page of that schedule, the head's button to all (Settings ›
                    # Scheduled tasks).
                    assert await rows.nth(0).locator("a.automation-edit").get_attribute("href") == (
                        f"#/settings/schedules/{loop.id}"
                    )
                    await expect(section.locator("a.automation-manage")).to_have_attribute(
                        "href", "#/settings/schedules"
                    )
                await page.screenshot(path=str(tmp_path / f"automations-{index}.png"))
                if index == 0:
                    # The list is read again after each switch and its order follows the next firings: rows are
                    # found by their titles.
                    def by_name(name: str, rows: Locator = rows, page: Page = page) -> Locator:
                        return rows.filter(has=page.locator(".automation-title", has_text=name))

                    # The paused one is put back: its switch, line and dot say it, and so does the store.
                    await by_name("iPhone build check").locator(".automation-switch").click()
                    await expect(by_name("iPhone build check").locator(".automation-switch")).to_have_attribute(
                        "aria-checked", "true"
                    )
                    await expect(by_name("iPhone build check").locator(".automation-line")).not_to_contain_text(
                        "paused"
                    )
                    await expect(by_name("iPhone build check").locator(".automation-dot")).to_have_attribute(
                        "data-state", "on"
                    )
                    back: ScheduleRecord = await runtime.store.get_schedule(paused.id)
                    assert back.enabled and back.next_run_at is not None and back.next_run_at > time.time()
                    # The loop is paused: the scheduler no longer finds it due, and nothing else changed.
                    await by_name("check the build").locator(".automation-switch").click()
                    await expect(by_name("check the build").locator(".automation-switch")).to_have_attribute(
                        "aria-checked", "false"
                    )
                    await expect(by_name("check the build").locator(".automation-line")).to_contain_text("paused")
                    await expect(by_name("check the build").locator(".automation-dot")).to_have_attribute(
                        "data-state", "off"
                    )
                    later: float = time.time() + 2 * _DAY_S
                    due: set[str] = {record.id for record in await runtime.store.due_schedules(later)}
                    assert loop.id not in due and {once.id, paused.id} <= due, due
                    assert (await runtime.store.get_schedule(once.id)).enabled
                    # Run now starts a run of the schedule, as its time would, and leaves its time and its switch.
                    before: ScheduleRecord = await runtime.store.get_schedule(once.id)
                    await by_name("Reddit comments").get_by_role("button", name="Run Reddit comments now").click()
                    await expect(page.locator(".toast.ok")).to_contain_text("Started: Reddit comments")
                    async with asyncio.timeout(_WAIT_S):
                        started: list[RunRecord] = [
                            r for r in await runtime.store.list_runs(20, None) if r.goal.startswith("Reddit comments")
                        ]
                        while not started:
                            await asyncio.sleep(0.1)
                            started = [
                                r
                                for r in await runtime.store.list_runs(20, None)
                                if r.goal.startswith("Reddit comments")
                            ]
                        await manager.wait(started[0].id)
                        await scheduler.drain()
                    after: ScheduleRecord = await runtime.store.get_schedule(once.id)
                    assert after.enabled == before.enabled and after.next_run_at == before.next_run_at
                    # Back on again for the looks that follow.
                    async with httpx.AsyncClient(
                        base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}
                    ) as client:
                        assert (await client.post(f"/api/schedules/{loop.id}/enable")).status_code == 200
                    # The pencil is followed: its address is the editor page of the schedule (Settings).
                    await by_name("check the build").locator("a.automation-edit").click()
                    await expect(page).to_have_url(re.compile(rf"#/settings/schedules/{loop.id}$"))
                    await expect(page.get_by_label("Name", exact=True)).to_have_value("Loop: check the build")
                    assert "/runs" not in page.url, page.url
                await page.close()
            finally:
                await browser.close()
        # A chat with no schedule of its own shows no section, and no other chat's.
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await _open(browser, address, lonely_run.id, 1280, "light", False)
            await expect(page.locator(".inspector-panel[data-tab='run'] .automations")).to_be_hidden()
            await expect(page.locator(".automation-row")).to_have_count(0)
        finally:
            await browser.close()
