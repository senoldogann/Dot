"""The schedules and loops of a chat (item 1333): ``GET /api/threads/{id}/automations`` lists the schedules whose
chat it is, its own loop among them, switched-on ones first and soonest first, and nothing of another chat; a chat
that does not exist is a 404. The side panel's switch and Run now are the schedule's own routes: switching one off
keeps the daemon's scheduler from finding it due, switching it on again puts it back, and neither touches any other
schedule. Served by a real server over a real store; no model is called."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.store.models import ScheduleOptions, ScheduleRecord, ThreadRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "thread-automations-token"
_DAY_S: float = 86400.0
_PLAIN: ScheduleOptions = ScheduleOptions(
    model=None,
    effort=None,
    speed=None,
    approvals=None,
    goal_mode=None,
    timezone=None,
    history_runs=None,
    same_chat=None,
    stop_after_failures=None,
    notify=None,
    agent_id=None,
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home; no run is started in it."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\ngoal_mode = false',
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


@pytest_asyncio.fixture(loop_scope="module")
async def client(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
    """A client of a real server over the runtime."""
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
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}
        ) as made:
            yield made
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


async def _schedule(
    runtime: Runtime, name: str, chat: ThreadRecord | None, *, cron: str | None, enabled: bool, next_in_s: float
) -> ScheduleRecord:
    """A schedule of ``chat`` (of none, when ``chat`` is ``None``) that is next due in ``next_in_s`` seconds."""
    at: float = time.time() + next_in_s
    return await runtime.store.add_schedule(
        name,
        cron,
        None if cron is not None else at,
        f"{name}: look again",
        "task",
        at if enabled else None,
        _PLAIN,
        None if chat is None else chat.id,
        enabled,
    )


async def test_a_chat_lists_its_own_schedules_and_its_loop_and_nothing_of_another_chat(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    chat: ThreadRecord = await runtime.store.create_thread("Ship the dictation work", "test")
    other: ThreadRecord = await runtime.store.create_thread("Plan the trip", "test")
    once: ScheduleRecord = await _schedule(runtime, "Reddit comments", chat, cron=None, enabled=True, next_in_s=_DAY_S)
    paused: ScheduleRecord = await _schedule(
        runtime, "iPhone build check", chat, cron="0 */6 * * *", enabled=False, next_in_s=_DAY_S / 2
    )
    loop: ScheduleRecord = await _schedule(
        runtime, "Loop: check the build", chat, cron="0 9 * * *", enabled=True, next_in_s=_DAY_S / 4
    )
    await _schedule(runtime, "Book the train", other, cron=None, enabled=True, next_in_s=_DAY_S)
    await _schedule(runtime, "Morning brief", None, cron="0 8 * * *", enabled=True, next_in_s=_DAY_S)

    listed: httpx.Response = await client.get(f"/api/threads/{chat.id}/automations")
    assert listed.status_code == 200, listed.text
    body: list[JsonObject] = listed.json()
    # Switched-on first and soonest first; the paused one last.
    assert [item["schedule"]["record"]["id"] for item in body] == [loop.id, once.id, paused.id]  # type: ignore[index]
    assert [item["loop"] for item in body] == [True, False, False]
    # What the Schedules page shows is there, so the panel needs no second read: the time in words, the local next time.
    assert body[0]["schedule"]["when"] == "cron 0 9 * * *"  # type: ignore[index]
    assert body[1]["schedule"]["when"].startswith("once, ")  # type: ignore[index, union-attr]
    assert body[2]["schedule"]["record"]["enabled"] is False  # type: ignore[index]
    assert body[0]["schedule"]["next_local"] is not None  # type: ignore[index]

    # Another chat has its own; a chat with none has an empty list; no chat is a 404.
    theirs: httpx.Response = await client.get(f"/api/threads/{other.id}/automations")
    assert [item["schedule"]["record"]["name"] for item in theirs.json()] == ["Book the train"]
    empty: ThreadRecord = await runtime.store.create_thread("Read the brief", "test")
    assert (await client.get(f"/api/threads/{empty.id}/automations")).json() == []
    missing: httpx.Response = await client.get("/api/threads/thread_nobody/automations")
    assert missing.status_code == 404, missing.text


async def test_switching_a_chats_schedule_off_and_on_takes_effect_at_the_next_firing_and_touches_no_other(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    chat: ThreadRecord = await runtime.store.create_thread("Watch the build", "test")
    mine: ScheduleRecord = await _schedule(
        runtime, "Loop: watch the build", chat, cron="*/30 * * * *", enabled=True, next_in_s=600
    )
    sibling: ScheduleRecord = await _schedule(
        runtime, "Check the logs", chat, cron="0 9 * * *", enabled=True, next_in_s=600
    )
    later: float = time.time() + _DAY_S

    async def due() -> set[str]:
        return {record.id for record in await runtime.store.due_schedules(later)}

    assert {mine.id, sibling.id} <= await due()
    # Off: the scheduler no longer finds it due; the sibling stands as it was.
    off: httpx.Response = await client.post(f"/api/schedules/{mine.id}/disable")
    assert off.status_code == 200, off.text
    assert mine.id not in await due()
    assert sibling.id in await due()
    listed: list[JsonObject] = (await client.get(f"/api/threads/{chat.id}/automations")).json()
    states: dict[str, bool] = {
        item["schedule"]["record"]["id"]: item["schedule"]["record"]["enabled"] for item in listed
    }  # type: ignore[index]
    assert states == {mine.id: False, sibling.id: True}
    # On: it has a next firing again, in the future, and is due once that comes.
    on: httpx.Response = await client.post(f"/api/schedules/{mine.id}/enable")
    assert on.status_code == 200, on.text
    again: ScheduleRecord = await runtime.store.get_schedule(mine.id)
    assert again.enabled and again.next_run_at is not None and again.next_run_at > time.time()
    assert mine.id in await due()
    # Run now needs the daemon's scheduler; a server without one says so and changes nothing.
    refused: httpx.Response = await client.post(f"/api/schedules/{mine.id}/run")
    assert refused.status_code == 409, refused.text
    assert (await runtime.store.get_schedule(mine.id)).next_run_at == again.next_run_at
