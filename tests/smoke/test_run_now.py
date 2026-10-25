"""Run now (item 455): a schedule's run started by hand, through the real daemon's routes and a real scheduler, with a
stand-in model. It runs switched on or off, due or not, as a firing runs (unattended, with the schedule's approvals),
in a chat whose title says it was started by hand; its time and its switch stay as they were; it does not start twice,
nor while the emergency stop is engaged; one that cannot start leaves nothing behind and is no missed firing; and the
schedule's Firings list it, at work and ended, beside the runs that fired at their time."""

import asyncio
import re
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
from dotagent.core.errors import RunConflict
from dotagent.core.paths import failsafe_path
from dotagent.core.types import JsonObject
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.scheduler.failures import firing_failure
from dotagent.scheduler.runner import Scheduler
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, ScheduleRecord

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "run-now-token"
OPUS: str = "anthropic:claude-opus-5-5"
_WAIT_S: float = 60.0
_BY_HAND: str = "(Started by hand at "


class Worker:
    """A provider that finishes every step with "Done.", keeps the first of the owner's messages each step was sent,
    and, while ``held`` is set, waits for it to be released before it answers."""

    def __init__(self) -> None:
        self.opened: list[str] = []
        self.held: asyncio.Event | None = None

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.opened.append(next(message.content for message in request.messages if message.role == "user"))
        if self.held is not None:
            await self.held.wait()
        finish: ScriptedTurn = ScriptedTurn(
            text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),)
        )
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


WORKER: Worker = Worker()


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
    await built.router.register("anthropic", WORKER)
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
    """A real scheduler that is not ticking by itself: a test ticks it when it wants a firing at its time."""
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


async def _run_by_hand(client: httpx.AsyncClient, schedule_id: str) -> RunRecord:
    started: httpx.Response = await client.post(f"/api/schedules/{schedule_id}/run")
    assert started.status_code == 201, started.text
    return RunRecord.model_validate(started.json())


async def _ended(manager: RunManager, run: RunRecord) -> RunRecord:
    async with asyncio.timeout(_WAIT_S):
        return await manager.wait(run.id)


async def _settled(runtime: Runtime, scheduler: Scheduler, schedule_id: str) -> ScheduleRecord:
    """The schedule once the watches of its ended runs have written how they ended."""
    async with asyncio.timeout(_WAIT_S):
        await scheduler.drain()
    return await runtime.store.get_schedule(schedule_id)


async def test_a_switched_off_schedule_runs_by_hand_as_its_firing_runs_and_keeps_its_time(
    runtime: Runtime, manager: RunManager, scheduler: Scheduler, client: httpx.AsyncClient
) -> None:
    created: ScheduleRecord = await _schedule(
        client, {"name": "weekly report", "goal": "Write the weekly report.", "cron": "0 9 * * 1"}
    )
    off: httpx.Response = await client.post(f"/api/schedules/{created.id}/disable")
    before: ScheduleRecord = ScheduleRecord.model_validate(off.json()["record"])
    run: RunRecord = await _run_by_hand(client, created.id)
    assert run.source == SCHEDULE_SOURCE and run.goal == "Write the weekly report.", run
    chat_title: str = (await runtime.store.get_thread(run.thread_id)).title
    assert chat_title.startswith("weekly report · ") and chat_title.endswith(" · by hand"), chat_title
    assert (await _ended(manager, run)).status == "done"
    assert any(_BY_HAND in opened and "Write the weekly report." in opened for opened in WORKER.opened), WORKER.opened
    after: ScheduleRecord = await _settled(runtime, scheduler, created.id)
    assert (after.enabled, after.next_run_at, after.last_status) == (False, before.next_run_at, "done")
    assert after.thread_id == run.thread_id and after.last_run_at is not None
    # Nothing of a missed firing: the owner was looking.
    assert await firing_failure(runtime.store, created.id) is None


async def test_a_one_time_schedule_run_by_hand_is_still_due_at_its_time(
    runtime: Runtime, manager: RunManager, scheduler: Scheduler, client: httpx.AsyncClient
) -> None:
    moment: str = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    created: ScheduleRecord = await _schedule(
        client, {"name": "renewal", "goal": "Check the renewal.", "cron": None, "at": moment}
    )
    assert created.enabled and created.next_run_at is not None
    run: RunRecord = await _run_by_hand(client, created.id)
    assert (await _ended(manager, run)).status == "done"
    after: ScheduleRecord = await _settled(runtime, scheduler, created.id)
    assert (after.enabled, after.next_run_at, after.last_status) == (True, created.next_run_at, "done")


async def test_a_one_time_schedule_whose_moment_comes_during_its_run_by_hand_fires_once_that_run_has_ended(
    runtime: Runtime, manager: RunManager, scheduler: Scheduler, client: httpx.AsyncClient
) -> None:
    """A one-time schedule has no next time to skip to: due while its run by hand works, it waits, still due, and fires
    once that run has ended (the pre-push review's finding: it was switched off for good)."""
    moment: str = (datetime.now() + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M")
    created: ScheduleRecord = await _schedule(
        client, {"name": "once soon", "goal": "Check once.", "cron": None, "at": moment}
    )
    assert created.next_run_at is not None
    WORKER.held = asyncio.Event()
    try:
        by_hand: RunRecord = await _run_by_hand(client, created.id)
        await scheduler.tick(created.next_run_at + 1)
        waiting: ScheduleRecord = await runtime.store.get_schedule(created.id)
        assert (waiting.enabled, waiting.next_run_at) == (True, created.next_run_at)
    finally:
        WORKER.held.set()
        WORKER.held = None
    assert (await _ended(manager, by_hand)).status == "done"
    await _settled(runtime, scheduler, created.id)
    await scheduler.tick(created.next_run_at + 2)
    firings: list[JsonObject] = (await client.get(f"/api/schedules/{created.id}/firings")).json()
    assert len(firings) == 2, firings
    fired: RunRecord = RunRecord.model_validate(firings[0]["run"])
    assert fired.id != by_hand.id and (await _ended(manager, fired)).status == "done"
    after: ScheduleRecord = await _settled(runtime, scheduler, created.id)
    assert (after.enabled, after.next_run_at) == (False, None)


async def test_a_schedule_does_not_run_twice_at_once_by_hand_or_at_its_time(
    runtime: Runtime, manager: RunManager, scheduler: Scheduler, client: httpx.AsyncClient
) -> None:
    created: ScheduleRecord = await _schedule(client, {"name": "five-minutely", "goal": "Look.", "cron": "*/5 * * * *"})
    assert created.next_run_at is not None
    WORKER.held = asyncio.Event()
    try:
        run: RunRecord = await _run_by_hand(client, created.id)
        again: httpx.Response = await client.post(f"/api/schedules/{created.id}/run")
        assert again.status_code == 409, again.text
        # Its time comes while the run by hand works: the firing is skipped, as for a run still going.
        await scheduler.tick(created.next_run_at + 1)
        firings: httpx.Response = await client.get(f"/api/schedules/{created.id}/firings")
        assert [firing["run"]["id"] for firing in firings.json()] == [run.id]
        assert firings.json()[0]["run"]["status"] in ("queued", "running")
    finally:
        WORKER.held.set()
        WORKER.held = None
    assert (await _ended(manager, run)).status == "done"
    skipped: ScheduleRecord = await runtime.store.get_schedule(created.id)
    assert skipped.next_run_at is not None and skipped.next_run_at > created.next_run_at
    # A click and a tick at the same moment start one run between them.
    raced: ScheduleRecord = await _settled(runtime, scheduler, created.id)
    assert raced.next_run_at is not None
    WORKER.held = asyncio.Event()
    try:
        clicked, _ = await asyncio.gather(
            client.post(f"/api/schedules/{created.id}/run"), scheduler.tick(raced.next_run_at + 1)
        )
        listed: list[JsonObject] = (await client.get(f"/api/schedules/{created.id}/firings")).json()
        at_work: list[JsonObject] = [firing for firing in listed if firing["run"]["status"] in ("queued", "running")]
        assert len(at_work) == 1, listed
        assert clicked.status_code in (201, 409), clicked.text
    finally:
        WORKER.held.set()
        WORKER.held = None
    for firing in at_work:
        await _ended(manager, RunRecord.model_validate(firing["run"]))


async def test_no_run_starts_by_hand_while_the_emergency_stop_is_engaged_and_one_that_cannot_start_leaves_nothing(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: ScheduleRecord = await _schedule(client, {"name": "held", "goal": "Look.", "cron": "0 9 * * *"})
    chats: int = len(await runtime.store.list_threads(500))
    await asyncio.to_thread(failsafe_path().touch)
    try:
        refused: httpx.Response = await client.post(f"/api/schedules/{created.id}/run")
        assert refused.status_code == 409 and "emergency stop" in refused.json()["message"], refused.text
    finally:
        await asyncio.to_thread(failsafe_path().unlink, True)
    # A start the manager refuses is the caller's: the chat made for it goes, and it is no missed firing.
    refusing: ScheduleRecord = await _schedule(client, {"name": "refused", "goal": "Look.", "cron": "0 9 * * *"})

    async def refuse(*_: object) -> RunRecord:
        raise RunConflict("the manager takes no run now", {})

    monkeypatch.setattr(manager, "start_firing", refuse)
    failed: httpx.Response = await client.post(f"/api/schedules/{refusing.id}/run")
    monkeypatch.undo()
    assert failed.status_code == 409 and "takes no run" in failed.json()["message"], failed.text
    assert len(await runtime.store.list_threads(500)) == chats
    assert await firing_failure(runtime.store, refusing.id) is None
    assert (await runtime.store.get_schedule(refusing.id)).last_status == ""
    missing: httpx.Response = await client.post("/api/schedules/sch_missing/run")
    assert missing.status_code == 404, missing.text


async def test_a_yolo_schedule_runs_by_hand_as_yolo(manager: RunManager, client: httpx.AsyncClient) -> None:
    created: ScheduleRecord = await _schedule(
        client, {"name": "free", "goal": "Look.", "cron": "0 9 * * *", "approvals": "yolo"}
    )
    run: RunRecord = await _run_by_hand(client, created.id)
    assert run.approvals == "yolo", run
    assert (await _ended(manager, run)).status == "done"


@pytest.mark.browser
async def test_run_now_starts_the_run_its_page_opens_and_the_firings_list_it(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Item 455, on a wide window and a phone (a touch screen), in light and dark: the button says what it starts and
    with which approvals, a click opens the run's page at once, and the schedule's Firings list the run."""
    created: ScheduleRecord = await _schedule(
        client, {"name": "page check", "goal": "Look at the page.", "cron": "0 9 * * *", "approvals": "yolo"}
    )
    port: int | None = client.base_url.port
    assert port is not None
    address: str = f"http://127.0.0.1:{port}"
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
                await page.goto(f"{address}/#/schedules")
                button: Locator = page.get_by_role("button", name="Run page check now")
                await expect(button).to_be_visible()
                await expect(button).to_have_attribute("title", re.compile(r"approvals: YOLO.*asking nothing", re.I))
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await page.screenshot(path=str(tmp_path / f"schedules-{width}-{scheme}.png"), full_page=True)
                await button.click()
                await expect(page).to_have_url(re.compile(r"#/runs/run_"))
                run_id: str = page.url.rsplit("/", 1)[-1]
                await _ended(manager, await runtime.store.get_run(run_id))
                await page.goto(f"{address}/#/schedules")
                toggle: Locator = page.get_by_role("button", name="History of page check")
                await toggle.click()
                await expect(toggle).to_have_attribute("aria-expanded", "true")
                listed: Locator = page.locator(f'[data-firings="{created.id}"] .firing-title').first
                await expect(listed).to_have_attribute("href", f"#/runs/{run_id}")
                await expect(listed).to_have_text(re.compile(r"page check · .* · by hand"))
                await page.close()
        finally:
            await browser.close()


@pytest.mark.browser
async def test_past_its_third_schedule_the_list_scrolls_inside_and_keeps_its_place_when_painted_again(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Item 634, the owner's word of 2026-10-25: Your schedules shows its first three rows and the top of the fourth,
    which says that more lie under them, and scrolls inside past them, on a wide window and on a phone, where it is
    never taller than most of the screen; three or fewer are not cut. A list painted again, as a switch or the end of a
    run paints it, keeps where it was scrolled to."""
    for listed in (await client.get("/api/schedules")).json():
        assert (await client.delete(f"/api/schedules/{listed['record']['id']}")).status_code == 200
    for number in range(1, 7):
        await _schedule(client, {"name": f"list check {number}", "goal": "Look at the list.", "cron": "0 9 * * *"})
    port: int | None = client.base_url.port
    assert port is not None
    address: str = f"http://127.0.0.1:{port}"
    measure: str = (
        "(list) => { const top = list.getBoundingClientRect().top;"
        " const rows = [...list.children].map((row) => row.getBoundingClientRect());"
        " return { shown: list.clientHeight, whole: list.scrollHeight, third: rows[2].bottom - top,"
        " fourth: rows[3].top - top, fifth: rows[4].top - top, screen: window.innerHeight }; }"
    )
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
                await page.goto(f"{address}/#/schedules")
                schedules: Locator = page.locator(".schedule-list")
                await expect(schedules.locator(":scope > .list-row")).to_have_count(6)
                await expect(schedules).to_have_class(re.compile(r"\bcut\b"))
                box: JsonObject = await schedules.evaluate(measure)
                assert box["whole"] > box["shown"], box
                if phone:
                    assert box["shown"] <= 0.6 * box["screen"] + 1, box
                else:
                    # The three rows whole and the top of the fourth, not the fifth.
                    assert abs(box["shown"] - (box["third"] + 32)) <= 2, box
                    assert box["fourth"] < box["shown"] < box["fifth"], box
                await page.screenshot(path=str(tmp_path / f"schedule-list-{width}-{scheme}.png"), full_page=True)
                # Scrolled to its end, the list shows its last schedule; switched off there, it is painted again and
                # stays where it was.
                await schedules.evaluate("(list) => { list.scrollTop = list.scrollHeight; }")
                scrolled: float = await schedules.evaluate("(list) => list.scrollTop")
                assert scrolled > 0
                await page.get_by_role("switch", name="Switch off list check 6").click()
                await expect(page.get_by_role("switch", name="Switch on list check 6")).to_be_visible()
                assert abs(await schedules.evaluate("(list) => list.scrollTop") - scrolled) <= 1
                await page.get_by_role("switch", name="Switch on list check 6").click()
                await expect(page.get_by_role("switch", name="Switch off list check 6")).to_be_visible()
                await page.close()
            # Three are not cut.
            for listed in (await client.get("/api/schedules")).json()[3:]:
                assert (await client.delete(f"/api/schedules/{listed['record']['id']}")).status_code == 200
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            await page.goto(f"{address}/#/schedules")
            await expect(page.locator(".schedule-list > .list-row")).to_have_count(3)
            await expect(page.locator(".schedule-list")).not_to_have_class(re.compile(r"\bcut\b"))
            await page.close()
        finally:
            await browser.close()
