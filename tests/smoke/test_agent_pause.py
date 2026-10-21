"""Pausing one of the owner's agents (item 465), through a real scheduler and the daemon's routes with a stand-in
model: a paused agent's repeating schedule is skipped to its next time and counts nothing, a one-time one waits, still
due, and fires once the agent is resumed; Run now and the owner's own messages still reach the agent; an agent that
cannot be read fires nothing; the pause is kept by the editor's Save; and the agents and schedules pages say it."""

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
from dotagent.scheduler import SCHEDULE_MISSED_EVENT
from dotagent.scheduler.runner import Scheduler
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import AgentRecord, EventRecord, RunRecord, ScheduleRecord

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "agent-pause-token"
OPUS: str = "anthropic:claude-opus-5-5"
_WAIT_S: float = 60.0
# The one tool the agents of these tests may use, besides the core ones every agent has.
_TOOL: str = "read_file"


class Offered:
    """A provider that finishes every step at once, and keeps the names of the tools each request offered."""

    def __init__(self) -> None:
        self.tools: list[frozenset[str]] = []

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.tools.append(frozenset(spec.name for spec in request.tools))
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


MODEL: Offered = Offered()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    # Yolo is the configured mode: a schedule and an agent that both leave the approvals open run yolo.
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{OPUS}"\n\n[policy]\napprovals = "yolo"\n\n'
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


async def _agent(client: httpx.AsyncClient, name: str) -> AgentRecord:
    body: JsonObject = {
        "name": name,
        "role": "Reads files",
        "instructions": "Answer in one word.",
        "model": "",
        "effort": None,
        "approvals": None,
        "tools": [_TOOL],
    }
    made: httpx.Response = await client.post("/api/agents", json=body)
    assert made.status_code == 201, made.text
    return AgentRecord.model_validate(made.json())


async def _paused(client: httpx.AsyncClient, agent: AgentRecord, paused: bool) -> AgentRecord:
    switched: httpx.Response = await client.post(f"/api/agents/{agent.id}/{'pause' if paused else 'resume'}")
    assert switched.status_code == 200, switched.text
    return AgentRecord.model_validate(switched.json())


async def _schedule(client: httpx.AsyncClient, body: JsonObject) -> ScheduleRecord:
    created: httpx.Response = await client.post("/api/schedules", json=body)
    assert created.status_code == 201, created.text
    return ScheduleRecord.model_validate(created.json()["record"])


class Missed:
    """The firings told as missed while it listens."""

    def __init__(self) -> None:
        self.schedules: list[str] = []

    async def listen(self, runtime: Runtime, ready: asyncio.Event) -> None:
        async with runtime.bus.subscribe(SYSTEM_RUN_ID) as events:
            ready.set()
            while True:
                event: EventRecord = await events.queue.get()
                if event.kind == SCHEDULE_MISSED_EVENT:
                    self.schedules.append(str(event.data["schedule_id"]))


@pytest_asyncio.fixture(loop_scope="module")
async def missed(runtime: Runtime) -> AsyncIterator[Missed]:
    listener: Missed = Missed()
    ready: asyncio.Event = asyncio.Event()
    task: asyncio.Task[None] = asyncio.create_task(listener.listen(runtime, ready))
    await ready.wait()
    try:
        yield listener
    finally:
        task.cancel()


async def _ended(manager: RunManager, scheduler: Scheduler, run_id: str) -> RunRecord:
    async with asyncio.timeout(_WAIT_S):
        ended: RunRecord = await manager.wait(run_id)
        await scheduler.drain()
    return ended


async def test_pause_and_resume_an_agent_but_not_dot(client: httpx.AsyncClient) -> None:
    scout: AgentRecord = await _agent(client, "Pausable")
    paused: AgentRecord = await _paused(client, scout, True)
    assert paused.paused_at is not None
    # The editor's Save keeps the pause.
    saved: httpx.Response = await client.put(
        f"/api/agents/{scout.id}",
        json={
            "name": "Pausable",
            "role": "Reads more",
            "instructions": "",
            "model": "",
            "effort": None,
            "approvals": None,
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["paused_at"] == paused.paused_at
    assert (await _paused(client, scout, False)).paused_at is None
    for switch in ("pause", "resume"):
        refused: httpx.Response = await client.post(f"/api/agents/dot/{switch}")
        assert refused.status_code == 422, refused.text


async def test_a_paused_agents_repeating_schedule_is_skipped_counting_nothing_and_the_owner_still_reaches_it(
    runtime: Runtime, manager: RunManager, scheduler: Scheduler, client: httpx.AsyncClient, missed: Missed
) -> None:
    scout: AgentRecord = await _agent(client, "Scout")
    schedule: ScheduleRecord = await _schedule(
        client, {"name": "news", "goal": "Look.", "cron": "*/5 * * * *", "agent_id": scout.id}
    )
    assert schedule.next_run_at is not None
    await _paused(client, scout, True)
    chats_before: int = len(await runtime.store.list_threads(1000))
    await scheduler.tick(schedule.next_run_at + 1)
    assert schedule.id not in scheduler._in_flight
    skipped: ScheduleRecord = await runtime.store.get_schedule(schedule.id)
    assert skipped.last_status == "skipped: Scout is paused"
    assert skipped.enabled and skipped.next_run_at is not None and skipped.next_run_at > schedule.next_run_at
    assert skipped.runs_failed_in_row == 0
    assert len(await runtime.store.list_threads(1000)) == chats_before
    assert schedule.id not in missed.schedules
    # The owner is there: Run now, and a message of theirs to the agent, still go.
    by_hand: httpx.Response = await client.post(f"/api/schedules/{schedule.id}/run")
    assert by_hand.status_code == 201, by_hand.text
    assert (await _ended(manager, scheduler, by_hand.json()["id"])).agent_id == scout.id
    message: httpx.Response = await client.post(
        "/api/runs", json={"goal": "Say hi.", "model": OPUS, "thread_id": None, "agent_id": scout.id}
    )
    assert message.status_code == 201, message.text
    assert (await _ended(manager, scheduler, message.json()["id"])).agent_id == scout.id
    # Resumed, the schedule fires at its next time as the agent.
    await _paused(client, scout, False)
    due: ScheduleRecord = await runtime.store.get_schedule(schedule.id)
    assert due.next_run_at is not None
    await scheduler.tick(due.next_run_at + 1)
    fired: RunRecord = await _ended(manager, scheduler, scheduler._in_flight[schedule.id])
    assert (fired.agent_id, fired.source) == (scout.id, "schedule")


async def test_a_paused_agents_one_time_schedule_waits_due_and_fires_once_resumed(
    runtime: Runtime, manager: RunManager, scheduler: Scheduler, client: httpx.AsyncClient, missed: Missed
) -> None:
    keeper: AgentRecord = await _agent(client, "Keeper")
    moment: str = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    once: ScheduleRecord = await _schedule(
        client, {"name": "once", "goal": "Look.", "cron": None, "at": moment, "agent_id": keeper.id}
    )
    assert once.next_run_at is not None
    await _paused(client, keeper, True)
    await scheduler.tick(once.next_run_at + 1)
    waiting: ScheduleRecord = await runtime.store.get_schedule(once.id)
    assert (waiting.last_status, waiting.enabled, waiting.next_run_at) == (
        "waiting: Keeper is paused",
        True,
        once.next_run_at,
    )
    assert once.id not in scheduler._in_flight and once.id not in missed.schedules
    await _paused(client, keeper, False)
    await scheduler.tick(once.next_run_at + 2)
    fired: RunRecord = await _ended(manager, scheduler, scheduler._in_flight[once.id])
    assert fired.agent_id == keeper.id


async def test_an_agent_that_cannot_be_read_fires_nothing(
    runtime: Runtime, scheduler: Scheduler, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    unread: AgentRecord = await _agent(client, "Unread")
    schedule: ScheduleRecord = await _schedule(
        client, {"name": "unread", "goal": "Look.", "cron": "*/5 * * * *", "agent_id": unread.id}
    )
    assert schedule.next_run_at is not None

    async def broken(agent_id: str) -> AgentRecord:
        raise StoreError("the store is gone", {"agent_id": agent_id})

    with monkeypatch.context() as patch:
        patch.setattr(runtime.store, "get_agent", broken)
        await scheduler.tick(schedule.next_run_at + 1)
    assert schedule.id not in scheduler._in_flight
    assert (await runtime.store.get_schedule(schedule.id)).last_status.startswith("failed to fire")


@pytest.mark.browser
async def test_the_pages_say_an_agent_is_paused_and_pause_turns_into_resume(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    resting: AgentRecord = await _agent(client, "Rester")
    schedule: ScheduleRecord = await _schedule(
        client, {"name": "rests", "goal": "Look.", "cron": "0 9 * * *", "agent_id": resting.id}
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
                await page.goto(f"http://127.0.0.1:{port}/#/agents/{resting.id}")
                await page.get_by_role("button", name="Pause").click()
                await expect(page.get_by_role("button", name="Resume")).to_be_visible()
                await expect(page.locator(".agent-paused")).to_contain_text("your messages here still go through")
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await page.screenshot(path=str(tmp_path / f"paused-{width}-{scheme}.png"), full_page=True)
                await page.goto(f"http://127.0.0.1:{port}/#/agents")
                row: Locator = page.locator(f'[data-agent="{resting.id}"]')
                await expect(row.locator(".badge", has_text="Paused")).to_be_visible()
                await page.goto(f"http://127.0.0.1:{port}/#/schedules")
                scheduled: Locator = page.locator(f'[data-schedule="{schedule.id}"]')
                await expect(scheduled.locator(".badge", has_text="Rester is paused")).to_be_visible()
                await page.goto(f"http://127.0.0.1:{port}/#/agents/{resting.id}")
                await page.get_by_role("button", name="Resume").click()
                await expect(page.get_by_role("button", name="Pause")).to_be_visible()
                await expect(page.locator(".agent-paused")).to_have_count(0)
                await page.close()
        finally:
            await browser.close()
