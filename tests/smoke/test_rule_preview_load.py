"""A rule tried on many past steps (item 469): over 20,000 steps the try-out reads them all in pages within its budget
of time, and the daemon, whose event loop it shares, answers other requests all the while."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.store.models import RunRecord, ThreadRecord

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "rule-preview-load-token"
OPUS: str = "anthropic:claude-opus-5-5"
STEPS: int = 20_000
# The try-out's budget of time (server/rule_replay.py, _BUDGET_S) and what a slow machine may add to it.
BUDGET_S: float = 2.0
SLACK_S: float = 1.0
# The longest the daemon may take to answer a request while a try-out reads.
ANSWER_S: float = 0.5


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(f'[browser]\nheadless = true\n\n[models]\nmain = "{OPUS}"\n', encoding="utf-8")
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
async def client(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
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
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30.0
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


def _result(index: int) -> JsonObject:
    """A step as the executor writes it, every other one a push, with a text as long as a step's can be."""
    command: str = f"git push origin branch-{index}" if index % 2 == 0 else f"ls -la folder-{index}"
    return {
        "tool": "shell",
        "ok": True,
        "text": "x" * 4000,
        "kind": "shell",
        "risk": "medium",
        "summary": f"Run: {command}",
        "target": command,
        "decision": None,
        "flags": [],
    }


async def test_a_try_out_over_twenty_thousand_steps_reads_them_all_and_the_daemon_answers_meanwhile(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    thread: ThreadRecord = await runtime.store.create_thread("Many steps", "ui")
    run: RunRecord = await runtime.store.create_run(
        thread.id, "Many steps", "task", OPUS, None, "ui", "high", "standard", None, "ask", False, None, None, None
    )
    first: float = time.time() - 3600.0
    rows: list[tuple[str, float, str, str]] = [
        (run.id, first + index * 0.1, "tool_result", json.dumps(_result(index))) for index in range(STEPS)
    ]
    # Seeded in one write: through add_event, one commit a step, it would take longer than the test.
    await runtime.store._db.executemany("INSERT INTO events(run_id, ts, kind, data) VALUES(?, ?, ?, ?)", rows)
    await runtime.store._db.commit()
    started: float = time.monotonic()
    trying: asyncio.Task[httpx.Response] = asyncio.create_task(
        client.post(
            "/api/rules/preview",
            json={"behavior": "ask", "tool_glob": "shell", "match_regex": r"\bgit push\b", "days": 30},
        )
    )
    answers: list[float] = []
    while not trying.done():
        asked: float = time.monotonic()
        health: httpx.Response = await client.get("/api/health")
        assert health.status_code == 200, health.text
        answers.append(time.monotonic() - asked)
    tried: httpx.Response = await trying
    took: float = time.monotonic() - started
    assert tried.status_code == 200, tried.text
    view = tried.json()
    assert (view["scanned"], view["partial"], view["fits"], view["would_decide"]) == (
        STEPS,
        False,
        STEPS // 2,
        STEPS // 2,
    )
    assert len(view["examples"]) == 20
    assert took < BUDGET_S + SLACK_S, took
    assert len(answers) > 1 and max(answers) < ANSWER_S, (len(answers), max(answers))
