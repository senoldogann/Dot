"""`GET /runs/{id}/events?tail=N` (item 1371): the newest N events of a run, oldest first, without the loop's snapshots,
and the header that says a daemon knows `tail` (a phone that slept through a long run reads the end of it and not the
run from its first event). A daemon without it would answer the run's first events, so the header is how a client tells
the two. The API of a real in-process server over a runtime in a throw-away home; no model is called."""

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
from dotagent.store.models import RunRecord, ThreadRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "events-tail-token"
_HEADERS: dict[str, str] = {"Authorization": f"Bearer {TOKEN}"}
_STEPS: int = 9


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


async def _run(runtime: Runtime) -> RunRecord:
    thread: ThreadRecord = await runtime.store.create_thread("Tail", "test")
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


async def test_tail_reads_the_newest_events_oldest_first_without_the_snapshots_and_says_it_knows_tail(
    world: tuple[FastAPI, Runtime],
) -> None:
    app, runtime = world
    run: RunRecord = await _run(runtime)
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as http:
        everything: httpx.Response = await http.get(
            f"/api/runs/{run.id}/events", params={"after": 0, "limit": 2000}, headers=_HEADERS
        )
        tailed: httpx.Response = await http.get(f"/api/runs/{run.id}/events", params={"tail": 4}, headers=_HEADERS)
        huge: httpx.Response = await http.get(f"/api/runs/{run.id}/events", params={"tail": 2000}, headers=_HEADERS)
        refused: httpx.Response = await http.get(f"/api/runs/{run.id}/events", params={"tail": 0}, headers=_HEADERS)
    stored: list[dict[str, object]] = everything.json()
    newest: list[dict[str, object]] = tailed.json()
    assert tailed.status_code == 200, tailed.text
    assert tailed.headers["x-dot-events-tail"] == "4"
    # The same events a page ends with, in the same order: what a client that read up to the gap goes on from.
    assert [event["id"] for event in newest] == [event["id"] for event in stored[-4:]]
    ids: list[int] = [int(str(event["id"])) for event in newest]
    assert ids == sorted(ids)
    assert LOOP_STATE_EVENT not in {event["kind"] for event in newest}
    # More than stored: all of them, not an error.
    assert [event["id"] for event in huge.json()] == [event["id"] for event in stored]
    assert refused.status_code == 422
    # A read without `tail` does not carry the header: it is how a client tells a daemon that ignores `tail`.
    assert "x-dot-events-tail" not in everything.headers


async def test_a_tail_of_a_run_with_few_events_is_all_of_them(world: tuple[FastAPI, Runtime]) -> None:
    app, runtime = world
    thread: ThreadRecord = await runtime.store.create_thread("Empty", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        "Nothing",
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
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as http:
        answered: httpx.Response = await http.get(f"/api/runs/{run.id}/events", params={"tail": 5}, headers=_HEADERS)
    assert answered.status_code == 200
    assert answered.headers["x-dot-events-tail"] == "5"
    assert [event["kind"] for event in answered.json()] == [
        event.kind for event in await runtime.store.list_events(run.id, 0, 5)
    ]
