"""The events of a run a page reads leave out the loop's snapshots (item 1170), which no page draws and which
weighed 100 to 330 KB a run: through `GET /runs/{id}/events`, page after page, every other event comes, each page full
while more are stored, none of the snapshots; through the run's stream neither; and the loop still reads its newest
snapshot back from the store. The API of a real in-process server over a runtime in a throw-away home; no model is
called."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from dotagent.agent.loop_state import LOOP_STATE_EVENT
from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.models import EventRecord, RunRecord, ThreadRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "unread-events-token"
_HEADERS: dict[str, str] = {"Authorization": f"Bearer {TOKEN}"}
# Five steps, each a said line and a snapshot; read three events at a time, so that a page whose snapshots were left
# out after the read would come short while more are stored.
_STEPS: int = 5
_PAGE: int = 3


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[tuple[FastAPI, Runtime]]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[agent]\nplaybooks = false\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    manager: RunManager = RunManager(runtime)
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=8765,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    try:
        yield create_app(context), runtime
    finally:
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


async def _run_with_snapshots(runtime: Runtime) -> RunRecord:
    thread: ThreadRecord = await runtime.store.create_thread("Snapshots", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        "Keep going",
        "task",
        SCRIPTED_MODEL,
        None,
        "test",
        "high",
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )
    for step in range(_STEPS):
        await runtime.store.add_event(run.id, "assistant", {"step": step, "text": f"Step {step}.", "tool_calls": []})
        await runtime.store.add_event(run.id, LOOP_STATE_EVENT, {"step": step})
    await runtime.store.finish_run(run.id, "done", "Done.")
    return run


async def test_a_pages_read_of_a_run_leaves_out_the_loops_snapshots_and_the_loop_still_reads_them(
    world: tuple[FastAPI, Runtime],
) -> None:
    app, runtime = world
    run: RunRecord = await _run_with_snapshots(runtime)
    read: list[dict[str, object]] = []
    pages: list[int] = []
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as http:
        after: int = 0
        while True:
            answered: httpx.Response = await http.get(
                f"/api/runs/{run.id}/events", params={"after": after, "limit": _PAGE}, headers=_HEADERS
            )
            assert answered.status_code == 200, answered.text
            page: list[dict[str, object]] = answered.json()
            pages.append(len(page))
            read.extend(page)
            if len(page) < _PAGE:
                break
            last: object = page[-1]["id"]
            assert isinstance(last, int)
            after = last
        streamed: httpx.Response = await http.get(
            f"/api/runs/{run.id}/stream", params={"after": 0, "stored": True}, headers=_HEADERS
        )
    said: list[object] = [event["data"] for event in read if event["kind"] == "assistant"]
    assert len(said) == _STEPS, pages
    assert all(size == _PAGE for size in pages[:-1]), pages
    assert LOOP_STATE_EVENT not in {event["kind"] for event in read}
    assert streamed.status_code == 200, streamed.text
    assert "event: assistant" in streamed.text
    assert f"event: {LOOP_STATE_EVENT}" not in streamed.text
    # What the loop reads a snapshot back by (`load_loop_state`): the newest, from the store.
    kept: EventRecord | None = await runtime.store.last_event(run.id, LOOP_STATE_EVENT)
    assert kept is not None
    assert kept.data == {"step": _STEPS - 1}
