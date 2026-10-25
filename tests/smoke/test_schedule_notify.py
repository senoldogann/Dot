"""How the end of a schedule's run is told (item 461), through a real scheduler and the daemon's routes with a
stand-in model: once per run at "always", a first failure and an end that needs the owner at "problems", nothing at
"never" but for a run started by hand, which is always told; and a schedule deleted while its run works still tells.
The schedules page says a schedule's own level on its row and offers the levels in its form."""

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.errors import StoreError
from dotagent.core.types import JsonObject
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.events import SYSTEM_RUN_ID
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler import SCHEDULE_RUN_ENDED_EVENT
from dotagent.scheduler.runner import Scheduler
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import EventRecord, RunRecord, ScheduleRecord

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "schedule-notify-token"
OPUS: str = "anthropic:claude-opus-5-5"
_WAIT_S: float = 60.0


class Outcomes:
    """A provider that finishes every step at once: done or failed as ``succeed`` says, with ``needs`` for the owner,
    and, while ``held`` is set, only once it is released."""

    def __init__(self) -> None:
        self.succeed: bool = True
        self.needs: str = ""
        self.held: asyncio.Event | None = None

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if self.held is not None:
            await self.held.wait()
        answer: JsonObject = {"answer": "Done." if self.succeed else "The site moved.", "success": self.succeed}
        if self.needs != "":
            answer["needs_from_user"] = self.needs
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


MODEL: Outcomes = Outcomes()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{OPUS}"\n\n'
        # A tick past the nightly reflection's time would start it, and drain() would wait on a model the test lacks.
        '[agent]\nreflection_time = "off"\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register("anthropic", MODEL)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def manager(runtime: Runtime) -> AsyncIterator[RunManager]:
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scheduler(runtime: Runtime, manager: RunManager) -> AsyncIterator[Scheduler]:
    built: Scheduler = Scheduler(runtime, manager)
    try:
        yield built
    finally:
        await built.drain()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client(runtime: Runtime, manager: RunManager, scheduler: Scheduler) -> AsyncIterator[httpx.AsyncClient]:
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
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_WAIT_S
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving


class Told:
    """The ends of schedules' runs told while it listens, by run."""

    def __init__(self) -> None:
        self.runs: list[str] = []

    async def listen(self, runtime: Runtime, ready: asyncio.Event) -> None:
        async with runtime.bus.subscribe(SYSTEM_RUN_ID) as events:
            ready.set()
            while True:
                event: EventRecord = await events.queue.get()
                if event.kind == SCHEDULE_RUN_ENDED_EVENT:
                    self.runs.append(str(event.data["run_id"]))


@pytest_asyncio.fixture(loop_scope="module")
async def told(runtime: Runtime) -> AsyncIterator[Told]:
    listener: Told = Told()
    ready: asyncio.Event = asyncio.Event()
    task: asyncio.Task[None] = asyncio.create_task(listener.listen(runtime, ready))
    await ready.wait()
    try:
        yield listener
    finally:
        task.cancel()


async def _schedule(client: httpx.AsyncClient, body: JsonObject) -> ScheduleRecord:
    created: httpx.Response = await client.post("/api/schedules", json=body)
    assert created.status_code == 201, created.text
    return ScheduleRecord.model_validate(created.json()["record"])


async def _fired(
    runtime: Runtime, manager: RunManager, scheduler: Scheduler, schedule_id: str, ok: bool, needs: str
) -> str:
    """Fire the schedule at its time, ending done or failed with ``needs``; the run's id once its watch is over."""
    MODEL.succeed, MODEL.needs = ok, needs
    due: ScheduleRecord = await runtime.store.get_schedule(schedule_id)
    assert due.next_run_at is not None
    await scheduler.tick(due.next_run_at + 1)
    run_id: str = scheduler._in_flight[schedule_id]
    async with asyncio.timeout(_WAIT_S):
        await manager.wait(run_id)
        await scheduler.drain()
    await asyncio.sleep(0.1)
    return run_id


async def test_each_level_tells_the_ends_it_is_for_once(
    runtime: Runtime, manager: RunManager, scheduler: Scheduler, client: httpx.AsyncClient, told: Told
) -> None:
    always: ScheduleRecord = await _schedule(
        client, {"name": "every end", "goal": "Look.", "cron": "*/5 * * * *", "approvals": "yolo"}
    )
    done: str = await _fired(runtime, manager, scheduler, always.id, True, "")
    assert told.runs.count(done) == 1, told.runs
    problems: ScheduleRecord = await _schedule(
        client, {"name": "on trouble", "goal": "Look.", "cron": "*/5 * * * *", "notify": "problems"}
    )
    first: str = await _fired(runtime, manager, scheduler, problems.id, False, "")
    second: str = await _fired(runtime, manager, scheduler, problems.id, False, "")
    fine: str = await _fired(runtime, manager, scheduler, problems.id, True, "")
    needing: str = await _fired(runtime, manager, scheduler, problems.id, True, "Sign in to the bank.")
    assert [told.runs.count(run) for run in (first, second, fine, needing)] == [1, 0, 0, 1], told.runs
    never: ScheduleRecord = await _schedule(
        client, {"name": "quiet", "goal": "Look.", "cron": "*/5 * * * *", "notify": "never"}
    )
    quiet: str = await _fired(runtime, manager, scheduler, never.id, False, "")
    assert told.runs.count(quiet) == 0
    # A run started by hand is always told: the owner asked for it.
    MODEL.succeed, MODEL.needs = True, ""
    started: httpx.Response = await client.post(f"/api/schedules/{never.id}/run")
    assert started.status_code == 201, started.text
    async with asyncio.timeout(_WAIT_S):
        by_hand: RunRecord = await manager.wait(started.json()["id"])
        await scheduler.drain()
    await asyncio.sleep(0.1)
    assert told.runs.count(by_hand.id) == 1, told.runs


async def test_a_schedule_deleted_while_its_run_works_still_tells_its_end(
    runtime: Runtime, manager: RunManager, scheduler: Scheduler, client: httpx.AsyncClient, told: Told
) -> None:
    gone: ScheduleRecord = await _schedule(client, {"name": "gone", "goal": "Look.", "cron": "*/5 * * * *"})
    assert gone.next_run_at is not None
    MODEL.succeed, MODEL.needs, MODEL.held = True, "", asyncio.Event()
    try:
        await scheduler.tick(gone.next_run_at + 1)
        run_id: str = scheduler._in_flight[gone.id]
        assert (await client.delete(f"/api/schedules/{gone.id}")).status_code == 200
    finally:
        MODEL.held.set()
        MODEL.held = None
    async with asyncio.timeout(_WAIT_S):
        await manager.wait(run_id)
        await scheduler.drain()
    await asyncio.sleep(0.1)
    assert told.runs.count(run_id) == 1, told.runs


async def _store_gone(*args: object) -> None:
    raise StoreError("the store is gone", {})


async def test_the_switch_off_and_the_notice_of_a_failed_run_are_each_done_when_the_other_fails(
    runtime: Runtime,
    manager: RunManager,
    scheduler: Scheduler,
    client: httpx.AsyncClient,
    told: Told,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limited: JsonObject = {"goal": "Look.", "cron": "*/5 * * * *", "stop_after_failures": 1}
    untold: ScheduleRecord = await _schedule(client, {"name": "untold", **limited})
    with monkeypatch.context() as patch:
        patch.setattr(scheduler, "_tell_end", _store_gone)
        await _fired(runtime, manager, scheduler, untold.id, False, "")
    assert not (await runtime.store.get_schedule(untold.id)).enabled
    kept_on: ScheduleRecord = await _schedule(client, {"name": "kept on", **limited})
    with monkeypatch.context() as patch:
        patch.setattr(scheduler, "_stop_if_failing", _store_gone)
        failed: str = await _fired(runtime, manager, scheduler, kept_on.id, False, "")
    assert told.runs.count(failed) == 1, told.runs


@pytest.mark.browser
async def test_the_row_says_its_notifications_and_a_one_time_schedule_no_switch_off(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    trouble: ScheduleRecord = await _schedule(
        client,
        {
            "name": "told on trouble",
            "goal": "Look.",
            "cron": "0 9 * * *",
            "notify": "problems",
            "stop_after_failures": 3,
        },
    )
    moment: str = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    once: ScheduleRecord = await _schedule(
        client, {"name": "just once", "goal": "Look.", "cron": None, "at": moment, "stop_after_failures": 3}
    )
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, height, scheme in ((1280, 900, "light"), (390, 844, "dark")):
                phone: bool = width < 720
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": height},
                    color_scheme=scheme,
                    is_mobile=phone,
                    has_touch=phone,
                    reduced_motion="reduce",
                )
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.goto(f"http://127.0.0.1:{port}/#/schedules")
                row: Locator = page.locator(f'[data-schedule="{trouble.id}"]')
                await expect(
                    row.locator(".chip", has_text="Notifications: only when it fails or needs me")
                ).to_be_visible()
                await expect(row.locator(".chip", has_text="Switch off: after 3 failed tasks in a row")).to_be_visible()
                # A one-time schedule is never switched off by its failures (item 458), so its row says no limit.
                single: Locator = page.locator(f'[data-schedule="{once.id}"]')
                await expect(single).to_be_visible()
                await expect(single.locator(".chip", has_text="Switch off")).to_have_count(0)
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await page.screenshot(path=str(tmp_path / f"notify-{width}-{scheme}.png"), full_page=True)
                await page.get_by_role("button", name="Edit told on trouble").click()
                level: Locator = page.get_by_role("combobox", name="Notifications")
                await expect(level).to_have_value("problems")
                await expect(level.locator("option")).to_have_text(
                    ["Default (Always)", "Always", "Only when it fails or needs me", "Never"]
                )
                await page.close()
        finally:
            await browser.close()
