"""A rule tried on many past steps (item 469): over 20,000 steps the try-out reads them all in pages, or says it read
the newest only once its budget of time ran out, and the daemon, whose event loop it shares, answers other requests all
the while; with a pattern slow on every step, it stops at its budget and the daemon's answers wait no longer than a
slice of its counting."""

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
from dotagent.policy.engine import match_rule
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import rule_replay
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.store.models import RuleRecord, RunRecord, ThreadRecord

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "rule-preview-load-token"
OPUS: str = "anthropic:claude-opus-5-5"
STEPS: int = 20_000
# The try-out's budget of time (server/rule_replay.py, _BUDGET_S) and what a slow machine may add to it.
BUDGET_S: float = 2.0
SLACK_S: float = 1.0
# The longest the daemon may take to answer a request while a try-out counts: ten of its 50 ms slices.
ANSWER_S: float = 0.5
# What a pattern slow on every step costs a match.
SLOW_MATCH_S: float = 0.002
_TRY: JsonObject = {"behavior": "ask", "tool_glob": "shell", "match_regex": r"\bgit push\b", "days": 30}


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


# What decided a call the gate let through, as its result keeps it since item 468.
_ALLOWED: JsonObject = {
    "verdict": "allow",
    "source": "preset",
    "reason": "the preset",
    "rule_id": None,
    "answered_by": "policy",
    "preset": "balanced",
}


def _events(run_id: str, at: float, index: int) -> list[tuple[str, float, str, str]]:
    """The events of one call, as the executor and the gate write them: its ``tool_call``; for every tenth, the policy
    event of a question the owner declined; and its ``tool_result``, with a text as long as a step's can be. Every
    other call is a push."""
    command: str = f"git push origin branch-{index}" if index % 2 == 0 else f"ls -la folder-{index}"
    summary: str = f"Run: {command}"
    declined: bool = index % 10 == 0
    call: JsonObject = {"call_id": f"call-{index}", "tool": "shell", "args": {"command": command}}
    result: JsonObject = {
        "call_id": f"call-{index}",
        "tool": "shell",
        "ok": not declined,
        "text": "x" * 4000,
        "kind": "shell",
        "risk": "medium",
        "summary": summary,
        "target": command,
        "decision": None if declined else _ALLOWED,
        "flags": [],
        "tainted": False,
    }
    asked: JsonObject = {
        "tool": "shell",
        "kind": "shell",
        "risk": "medium",
        "summary": summary,
        "target": command,
        "amount": None,
        "flags": [],
        "verdict": "ask",
        "source": "preset",
        "reason": "the preset asks",
        "rule_id": None,
    }
    events: list[tuple[str, float, str, str]] = [(run_id, at, "tool_call", json.dumps(call))]
    if declined:
        events.append((run_id, at, "policy", json.dumps(asked)))
    events.append((run_id, at, "tool_result", json.dumps(result)))
    return events


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def many(runtime: Runtime) -> RunRecord:
    """A run of 20,000 calls in the last hour."""
    thread: ThreadRecord = await runtime.store.create_thread("Many steps", "ui")
    run: RunRecord = await runtime.store.create_run(
        thread.id, "Many steps", "task", OPUS, None, "ui", "high", "standard", None, "ask", False, None, None, None
    )
    first: float = time.time() - 3600.0
    rows: list[tuple[str, float, str, str]] = [
        row for index in range(STEPS) for row in _events(run.id, first + index * 0.1, index)
    ]
    # Seeded in one write: through add_event, one commit an event, it would take longer than the test.
    await runtime.store._db.executemany("INSERT INTO events(run_id, ts, kind, data) VALUES(?, ?, ?, ?)", rows)
    await runtime.store._db.commit()
    return run


async def _try_while_asking(client: httpx.AsyncClient) -> tuple[JsonObject, float, list[float]]:
    """Try the rule while asking the daemon for its health again and again: the try-out's answer, how long it took,
    and how long each health answer took."""
    started: float = time.monotonic()
    trying: asyncio.Task[httpx.Response] = asyncio.create_task(client.post("/api/rules/preview", json=_TRY))
    answers: list[float] = []
    while not trying.done():
        asked: float = time.monotonic()
        health: httpx.Response = await client.get("/api/health")
        assert health.status_code == 200, health.text
        answers.append(time.monotonic() - asked)
    tried: httpx.Response = await trying
    assert tried.status_code == 200, tried.text
    view: JsonObject = tried.json()
    return view, time.monotonic() - started, answers


async def test_a_try_out_over_twenty_thousand_steps_reads_them_all_and_the_daemon_answers_meanwhile(
    client: httpx.AsyncClient, many: RunRecord
) -> None:
    view, took, answers = await _try_while_asking(client)
    # On this Mac it reads them all in a quarter of a second; a machine slow enough to run out of time says so.
    if view["partial"] is True:
        assert took >= BUDGET_S, took
    else:
        assert (view["scanned"], view["fits"], view["would_decide"]) == (STEPS, STEPS // 2, STEPS // 2)
        # A push the preset let through would now ask; one the owner declined was asked about already.
        assert (view["stricter"], view["same"]) == (STEPS * 2 // 5, STEPS // 10)
    examples: object = view["examples"]
    assert isinstance(examples, list) and len(examples) == 20
    assert took < BUDGET_S + SLACK_S, took
    # The daemon answered while the try-out read: more than the one request that waited for it.
    assert len(answers) > 1, answers


async def test_a_pattern_slow_on_every_step_stops_at_the_budget_and_the_daemon_answers_between_slices(
    client: httpx.AsyncClient, many: RunRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    def slow(info: ActionInfo, rules: list[RuleRecord]) -> RuleRecord | None:
        time.sleep(SLOW_MATCH_S)
        return match_rule(info, rules)

    monkeypatch.setattr(rule_replay, "match_rule", slow)
    view, took, answers = await _try_while_asking(client)
    assert view["partial"] is True and isinstance(view["scanned"], int) and view["scanned"] < STEPS, view
    assert BUDGET_S <= took < BUDGET_S + SLACK_S, took
    assert len(answers) > 1 and max(answers) < ANSWER_S, (len(answers), max(answers))
