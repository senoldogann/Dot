"""A repeating schedule whose runs fail so many times in a row is switched off and the owner told once (item 458),
through the real daemon's routes and a real scheduler, with a stand-in model whose runs end done or failed as asked."""

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect
from row_menu import choose

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.events import SYSTEM_RUN_ID
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler import SCHEDULE_STOPPED_EVENT
from dotagent.scheduler.plans import ScheduleRequest, edit_schedule
from dotagent.scheduler.runner import Scheduler
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import EventRecord, RunRecord, ScheduleRecord

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "schedule-stop-token"
OPUS: str = "anthropic:claude-opus-5-5"
_WAIT_S: float = 60.0


class Outcomes:
    """A provider that finishes every step at once, done or failed as ``succeed`` says."""

    def __init__(self) -> None:
        self.succeed: bool = False

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        answer: JsonObject = {"answer": "Done." if self.succeed else "It did not work.", "success": self.succeed}
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


async def _schedule(client: httpx.AsyncClient, body: JsonObject) -> ScheduleRecord:
    created: httpx.Response = await client.post("/api/schedules", json=body)
    assert created.status_code == 201, created.text
    return ScheduleRecord.model_validate(created.json()["record"])


async def _ran(
    runtime: Runtime, manager: RunManager, scheduler: Scheduler, client: httpx.AsyncClient, schedule_id: str, ok: bool
) -> ScheduleRecord:
    """Run the schedule once by hand, ending done or failed, and the schedule once its watch has written the end."""
    MODEL.succeed = ok
    started: httpx.Response = await client.post(f"/api/schedules/{schedule_id}/run")
    assert started.status_code == 201, started.text
    async with asyncio.timeout(_WAIT_S):
        ended: RunRecord = await manager.wait(started.json()["id"])
        await scheduler.drain()
    assert ended.status == ("done" if ok else "failed"), ended
    return await runtime.store.get_schedule(schedule_id)


async def test_a_repeating_schedule_failing_its_limit_in_a_row_is_switched_off_and_told_once(
    runtime: Runtime, manager: RunManager, scheduler: Scheduler, client: httpx.AsyncClient
) -> None:
    created: ScheduleRecord = await _schedule(
        client,
        {"name": "flaky", "goal": "Try.", "cron": "*/5 * * * *", "stop_after_failures": 3, "approvals": "yolo"},
    )
    assert created.options.stop_after_failures == 3
    told: list[EventRecord] = []
    async with runtime.bus.subscribe(SYSTEM_RUN_ID) as events:

        async def listen() -> None:
            while True:
                event: EventRecord = await events.queue.get()
                if event.kind == SCHEDULE_STOPPED_EVENT:
                    told.append(event)

        listening: asyncio.Task[None] = asyncio.create_task(listen())
        try:
            # Two failed, then one done: the row starts again; a yolo schedule's runs count as any other's.
            assert [
                (await _ran(runtime, manager, scheduler, client, created.id, ok)).runs_failed_in_row
                for ok in (False, False, True)
            ] == [1, 2, 0]
            # Its time comes: a timed firing's failure counts as a run by hand's does.
            MODEL.succeed = False
            due: ScheduleRecord = await runtime.store.get_schedule(created.id)
            assert due.next_run_at is not None
            await scheduler.tick(due.next_run_at + 1)
            fired: str = scheduler._in_flight[created.id]
            async with asyncio.timeout(_WAIT_S):
                assert (await manager.wait(fired)).status == "failed"
                await scheduler.drain()
            assert (await runtime.store.get_schedule(created.id)).runs_failed_in_row == 1
            for expected in (2, 3):
                after: ScheduleRecord = await _ran(runtime, manager, scheduler, client, created.id, False)
                assert after.runs_failed_in_row == expected
            assert (after.enabled, after.next_run_at) == (False, None)
            assert after.last_status == "switched off: its last 3 tasks failed"
            await asyncio.sleep(0.2)
        finally:
            listening.cancel()
    assert [event.data for event in told] == [{"schedule_id": created.id, "name": "flaky", "failures": 3}]
    # Switched on again from its form (the whole schedule, PATCH), its row starts afresh and its status is cleared.
    whole: JsonObject = {
        "name": "flaky",
        "goal": "Try.",
        "cron": "*/5 * * * *",
        "at": None,
        "mode": "task",
        "model": None,
        "effort": None,
        "speed": None,
        "approvals": "yolo",
        "goal_mode": None,
        "timezone": None,
        "history_runs": None,
        "same_chat": None,
        "stop_after_failures": 3,
        "enabled": True,
    }
    on: httpx.Response = await client.patch(f"/api/schedules/{created.id}", json=whole)
    assert on.status_code == 200, on.text
    assert (on.json()["record"]["enabled"], on.json()["record"]["runs_failed_in_row"]) == (True, 0)
    assert on.json()["record"]["last_status"] == ""


async def test_a_one_time_schedule_and_one_without_a_limit_are_never_switched_off(
    runtime: Runtime, manager: RunManager, scheduler: Scheduler, client: httpx.AsyncClient
) -> None:
    moment: str = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    once: ScheduleRecord = await _schedule(
        client, {"name": "once", "goal": "Try.", "cron": None, "at": moment, "stop_after_failures": 1}
    )
    unlimited: ScheduleRecord = await _schedule(client, {"name": "patient", "goal": "Try.", "cron": "0 9 * * *"})
    for schedule in (once, unlimited):
        for _ in range(2):
            ran: ScheduleRecord = await _ran(runtime, manager, scheduler, client, schedule.id, False)
        assert (ran.enabled, ran.runs_failed_in_row) == (True, 2), ran


@pytest.mark.browser
async def test_the_row_says_how_many_runs_failed_in_a_row_and_the_form_offers_the_limit(
    runtime: Runtime, manager: RunManager, scheduler: Scheduler, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    created: ScheduleRecord = await _schedule(
        client, {"name": "watched", "goal": "Try.", "cron": "0 9 * * *", "stop_after_failures": 5}
    )
    await _ran(runtime, manager, scheduler, client, created.id, False)
    await _ran(runtime, manager, scheduler, client, created.id, False)
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
                row: Locator = page.locator(f'[data-schedule="{created.id}"]')
                badge: Locator = row.locator(".badge", has_text="2 failed in a row")
                await expect(badge).to_be_visible()
                await expect(badge).to_have_attribute("title", "It switches itself off at 5 failed tasks in a row")
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await page.screenshot(path=str(tmp_path / f"stop-{width}-{scheme}.png"), full_page=True)
                await choose(page.locator("[data-schedule]", has_text="watched").first, "Edit")
                limit: Locator = page.get_by_role("combobox", name="Switch off after")
                await expect(limit).to_have_value("5")
                await expect(limit.locator("option")).to_contain_text(["Default (Never)", "Never"])
                await page.close()
        finally:
            await browser.close()


async def test_an_edit_that_leaves_the_switch_alone_keeps_the_schedule_on_or_off_as_it_is(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    """Item 723: the form sent the switch as it was when it opened, so a schedule switched on in the list meanwhile was
    switched off again by a save of its goal, and one switched off for its failures was switched on by one. Left out,
    on or off stays as it is."""
    created: ScheduleRecord = await _schedule(client, {"name": "kept", "goal": "Look.", "cron": "0 9 * * *"})
    whole: JsonObject = {"name": "kept", "goal": "Look again.", "cron": "0 9 * * *", "at": None, "mode": "task"}
    whole |= dict.fromkeys(("model", "effort", "speed", "approvals", "goal_mode", "timezone"), None)
    for switched in (False, True):
        side: str = "enable" if switched else "disable"
        assert (await client.post(f"/api/schedules/{created.id}/{side}")).status_code == 200
        saved: httpx.Response = await client.patch(f"/api/schedules/{created.id}", json=whole)
        assert saved.status_code == 200, saved.text
        assert (saved.json()["record"]["enabled"], saved.json()["record"]["goal"]) == (switched, "Look again.")
    # Named, it is as the form says.
    off: httpx.Response = await client.patch(f"/api/schedules/{created.id}", json={**whole, "enabled": False})
    assert off.json()["record"]["enabled"] is False


async def test_a_one_time_schedule_due_and_waiting_can_have_its_goal_edited(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    """Item 723: a one-time schedule whose moment passed while it waited (its agent paused, the emergency stop, a start
    tried again) was refused an edit of its goal as having no run left; it keeps waiting, due."""
    at: datetime = datetime.now(runtime.schedule_zone).replace(microsecond=0) + timedelta(hours=1)
    created: ScheduleRecord = await _schedule(
        client, {"name": "waits", "goal": "Once.", "at": at.strftime("%Y-%m-%dT%H:%M")}
    )
    assert created.next_run_at is not None
    edited: ScheduleRecord = await edit_schedule(
        runtime.store,
        runtime.config.scheduler,
        runtime.schedule_zone,
        created.id,
        ScheduleRequest(
            name="waits",
            goal="Once, said better.",
            cron=None,
            at=at.strftime("%Y-%m-%dT%H:%M"),
            mode="task",
            options=created.options,
        ),
        True,
        created.next_run_at + 60,
    )
    assert (edited.enabled, edited.next_run_at, edited.goal) == (True, created.next_run_at, "Once, said better.")


@pytest.mark.browser
async def test_a_one_time_schedule_that_ran_reads_ran_once_not_switched_off(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Item 723: a one-time schedule that fired read "switched off", as one the owner had switched off, and its switch,
    pressed, was refused ("no run left"). It reads "ran once" and has no switch to press; one switched off by hand
    before its time still reads "switched off"."""
    at: datetime = datetime.now(runtime.schedule_zone).replace(microsecond=0) + timedelta(hours=1)
    when: str = at.strftime("%Y-%m-%dT%H:%M")
    ran: ScheduleRecord = await _schedule(client, {"name": "ran", "goal": "Once.", "at": when})
    held: ScheduleRecord = await _schedule(client, {"name": "held", "goal": "Once.", "at": when})
    assert ran.next_run_at is not None
    await runtime.store.record_schedule_fire(ran.id, ran.next_run_at, None, "thr_ran", "done")
    assert (await client.post(f"/api/schedules/{held.id}/disable")).status_code == 200
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            await page.goto(f"http://127.0.0.1:{port}/#/schedules")
            done: Locator = page.locator(f'[data-schedule="{ran.id}"]')
            await expect(done.locator(".badge", has_text="ran once")).to_be_visible()
            await expect(done.get_by_role("switch")).to_be_disabled()
            off: Locator = page.locator(f'[data-schedule="{held.id}"]')
            await expect(off.locator(".badge", has_text="switched off")).to_be_visible()
            await expect(off.get_by_role("switch")).to_be_enabled()
            await page.screenshot(path=str(tmp_path / "ran-once-390.png"), full_page=True)
        finally:
            await browser.close()


async def test_only_a_one_time_schedule_that_ran_at_its_moment_reads_ran_once(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    """Item 723, pre-push review round 1: "ran once" was guessed by the page from a firing having happened, so a
    schedule given a new time after it fired, or one whose start failed, read "ran once" once switched off by hand, and
    could not be switched on. The daemon says it, from a firing at its moment that started its run."""
    at: datetime = datetime.now(runtime.schedule_zone).replace(microsecond=0) + timedelta(hours=1)
    when: str = at.strftime("%Y-%m-%dT%H:%M")

    async def ran_once(schedule_id: str) -> bool:
        view: httpx.Response = await client.get(f"/api/schedules/{schedule_id}")
        return bool(view.json()["ran_once"])

    ran: ScheduleRecord = await _schedule(client, {"name": "fired", "goal": "Once.", "at": when})
    assert ran.next_run_at is not None
    await runtime.store.record_schedule_fire(ran.id, ran.next_run_at, None, "thr_fired", "done")
    assert await ran_once(ran.id)
    # Given a later time, then switched off by hand before it: an earlier firing is not this moment's.
    later: str = (at + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    whole: JsonObject = {"name": "fired", "goal": "Once.", "cron": None, "at": later, "mode": "task", "enabled": True}
    whole |= dict.fromkeys(("model", "effort", "speed", "approvals", "goal_mode", "timezone"), None)
    assert (await client.patch(f"/api/schedules/{ran.id}", json=whole)).status_code == 200
    assert (await client.post(f"/api/schedules/{ran.id}/disable")).status_code == 200
    assert not await ran_once(ran.id)
    # A start that failed, then switched off by hand.
    failed: ScheduleRecord = await _schedule(client, {"name": "unstarted", "goal": "Once.", "at": when})
    assert failed.next_run_at is not None
    await runtime.store.record_schedule_fire(failed.id, failed.next_run_at, None, "thr_un", "starting")
    await runtime.store.set_schedule_status(failed.id, "failed to start: the model is not available")
    assert not await ran_once(failed.id)
