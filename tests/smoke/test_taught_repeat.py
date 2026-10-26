"""A task the owner taught repeats by itself (item 666), through the real daemon's routes, a real scheduler and a
stand-in model: the teach panel's Repeat makes a schedule in the chat it was taught in, whose runs start with the taught
skill picked, its instructions in the run's first request; an edit of the schedule on the Schedules page keeps the
skill; a task nobody taught, a chat that is not there and a rhythm the scheduler refuses are refused, and make
nothing."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.scheduler.runner import Scheduler
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.store.models import RunRecord, ScheduleRecord
from dotagent.teach import TaughtSkill, save_taught_skill

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "taught-repeat-token"
OPUS: str = "anthropic:claude-opus-5-5"
_WAIT_S: float = 60.0
# A step of the taught skill no prompt of Dot's holds, so that a request that holds it holds the skill.
_STEP: str = "Open the invoices page of example.com and download each invoice as a PDF."
SKILL: TaughtSkill = TaughtSkill(
    name="taught-invoices",
    kind="invoices",
    host="example.com",
    description="Pay the invoices of the month.",
    when="At the start of a month.",
    steps=(_STEP, "Pay each one from the bank's page."),
    parameters=(),
    shown=("Opened https://example.com/invoices",),
)


class Worker:
    """A provider that finishes every step with "Done." and keeps the whole text of each request it was sent."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.tools: list[set[str]] = []

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append("\n".join(str(message.content) for message in request.messages))
        self.tools.append({spec.name for spec in request.tools})
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
        f'[browser]\nheadless = true\n\n[models]\nmain = "{OPUS}"\n\n[agent]\nreflection_time = "off"\n',
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


async def _ended(manager: RunManager, run: RunRecord) -> RunRecord:
    async with asyncio.timeout(_WAIT_S):
        return await manager.wait(run.id)


async def test_a_taught_task_repeats_in_its_chat_with_its_skill_picked(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient
) -> None:
    name: str = await asyncio.to_thread(save_taught_skill, SKILL)
    WORKER.tools.clear()
    first: RunRecord = await manager.start(
        "Pay this month's invoices", "task", OPUS, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    assert (await _ended(manager, first)).status == "done"
    # The owner's message may get Dot's reaction (item 664); a firing, which no message of theirs started, may not.
    assert "react" in WORKER.tools[0]
    made: httpx.Response = await client.post(
        f"/api/teach/{first.thread_id}/repeat", json={"name": name, "cron": "0 9 * * 1"}
    )
    assert made.status_code == 201, made.text
    assert made.json()["cron"] == "0 9 * * 1" and made.json()["name"] == "Pay the invoices of the month."
    record: ScheduleRecord = await runtime.store.get_schedule(made.json()["id"])
    assert record.options.skills == (name,) and record.thread_id == first.thread_id and record.mode == "task"
    assert record.goal == "Pay the invoices of the month." and record.enabled
    # The chat's own choices go with it, as a schedule a run of the chat makes has them.
    assert record.options.approvals == "yolo" and record.options.same_chat

    # Edited on the Schedules page, whose form has no field for it, the schedule keeps its skill.
    edited: httpx.Response = await client.patch(
        f"/api/schedules/{record.id}",
        json={
            "name": "Invoices",
            "goal": record.goal,
            "cron": "0 10 * * 1",
            "at": None,
            "mode": "task",
            "model": record.options.model,
            "effort": record.options.effort,
            "approvals": record.options.approvals,
            "goal_mode": record.options.goal_mode,
            "timezone": None,
            "enabled": True,
        },
    )
    assert edited.status_code == 200, edited.text
    assert (await runtime.store.get_schedule(record.id)).options.skills == (name,)

    # Its run, started by hand as a firing is, opens with the skill's instructions.
    WORKER.requests.clear()
    WORKER.tools.clear()
    started: httpx.Response = await client.post(f"/api/schedules/{record.id}/run")
    assert started.status_code == 201, started.text
    run: RunRecord = RunRecord.model_validate(started.json())
    # Run now starts it in a chat of its own, named so (item 455); its firings go on in the taught chat.
    assert run.source == SCHEDULE_SOURCE
    assert (await _ended(manager, run)).status == "done"
    assert WORKER.requests and _STEP in WORKER.requests[0], WORKER.requests[:1]
    assert "react" not in WORKER.tools[-1]


async def test_what_cannot_repeat_is_refused_and_makes_nothing(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient
) -> None:
    run: RunRecord = await manager.start(
        "Something else", "task", OPUS, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    assert (await _ended(manager, run)).status == "done"
    before: int = len(await runtime.store.list_schedules())
    nobody: httpx.Response = await client.post(
        f"/api/teach/{run.thread_id}/repeat", json={"name": "taught-nothing", "cron": "0 9 * * 1"}
    )
    assert nobody.status_code == 404, nobody.text
    nowhere: httpx.Response = await client.post(
        "/api/teach/thr_missing/repeat", json={"name": "taught-invoices", "cron": "0 9 * * 1"}
    )
    assert nowhere.status_code == 404, nowhere.text
    wrong: httpx.Response = await client.post(
        f"/api/teach/{run.thread_id}/repeat", json={"name": "taught-invoices", "cron": "not a cron"}
    )
    assert wrong.status_code == 422, wrong.text
    assert len(await runtime.store.list_schedules()) == before
