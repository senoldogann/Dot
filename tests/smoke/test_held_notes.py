"""Item 1865 in the real loop: a scheduled task, a watcher's task or a helper writes no lasting note on its own. Its
``memory_save`` keeps the note on the run for the owner, its ``memory_update`` waits as a proposed change and the
lessons drawn from it stay proposed under ``auto``; the Memory page's API lists what waits, and the owner's Save writes
the note as the run would have (tags, subject) and clean, with the run's origin. A run of a chat saves as before.

No model service is used: the scripted model plays the runs and answers for the review model too, and the memory has
no embeddings (see ``eval_config``).
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.lessons import run_lessons
from dotagent.agent.manager import RunManager
from dotagent.agent.review import LESSONS_TOOL
from dotagent.config import AppConfig, load_default_config
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.memory.held import run_held_notes
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.models import MemoryRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Check the mailbox and remember what matters."
FACT: str = "The landlord answers mail only on weekdays."
OTHER: str = "The bank's statements come on the first of the month."
LESSON: str = "read_file fails on a file that does not exist yet; create notes.txt with write_file first."
TOKEN: str = "MARKER-held-notes"
PORT: int = 18767
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0


@dataclass(frozen=True)
class Home:
    runtime: Runtime
    manager: RunManager


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config() -> AppConfig:
    """An eval run's configuration with lessons ``auto`` and the scripted model as the review model."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"lessons": "auto"}),
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
        }
    )


async def _played(turns: Sequence[ScriptedTurn], runtime: Runtime) -> Sequence[ScriptedTurn]:
    return turns


@asynccontextmanager
async def _home(login_env: dict[str, str], turns: Sequence[ScriptedTurn]) -> AsyncIterator[Home]:
    async with _home_made(login_env, partial(_played, turns)) as home:
        yield home


@asynccontextmanager
async def _home_made(
    login_env: dict[str, str], make: Callable[[Runtime], Awaitable[Sequence[ScriptedTurn]]]
) -> AsyncIterator[Home]:
    """A runtime in a throw-away home whose scripted model plays the turns ``make`` writes once the store is open."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(), login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, await make(runtime)))
            manager: RunManager = RunManager(runtime)
            try:
                yield Home(runtime=runtime, manager=manager)
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


@asynccontextmanager
async def _client(home: Home) -> AsyncIterator[httpx.AsyncClient]:
    context: ServerContext = ServerContext(
        runtime=home.runtime,
        manager=home.manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=PORT,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(context)),
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        yield client


async def _run(home: Home, source: str) -> RunRecord:
    """``GOAL`` as a task run of ``source``, once it ended done and its lessons are drawn."""
    run: RunRecord = await home.manager.start(
        GOAL, "task", SCRIPTED_MODEL, None, source, None, None, "standard", None, "ask", False, ()
    )
    async with asyncio.timeout(_TIME_LIMIT_S):
        record: RunRecord = await home.manager.wait(run.id)
        await home.manager.learned(run.id)
    assert record.status == "done", record
    return record


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _save(content: str, subject: str | None) -> ScriptedTurn:
    return _turn(ScriptedCall(tool="memory_save", arguments={"content": content, "tags": ["mail"], "subject": subject}))


FINISH: ScriptedTurn = _turn(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}))


async def test_a_scheduled_task_holds_its_notes_for_the_owner_who_saves_one_as_the_run_would_have(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [_save(FACT, "landlord-mail"), _save(OTHER, None), FINISH]
    async with _home(login_env, turns) as home, _client(home) as client:
        run: RunRecord = await _run(home, SCHEDULE_SOURCE)
        assert await home.runtime.store.count_memories() == 0
        held = await run_held_notes(home.runtime.store, run.id)
        assert [(note.index, note.content, note.tags, note.subject, note.state) for note in held] == [
            (0, FACT, ("mail",), "landlord-mail", "proposed"),
            (1, OTHER, ("mail",), None, "proposed"),
        ]
        waiting = (await client.get("/api/memories/waiting")).json()
        assert [(item["kind"], item["index"], item["text"], item["origin"]) for item in waiting] == [
            ("note", 0, FACT, "schedule"),
            ("note", 1, OTHER, "schedule"),
        ]
        saved = await client.post(f"/api/memories/waiting/{run.id}/0/save")
        assert saved.status_code == 200, saved.text
        assert [item["index"] for item in saved.json()] == [1]
        note: MemoryRecord = (await home.runtime.store.list_memories(10, 0, "changed"))[0]
        assert (note.content, note.tags, note.subject, note.tainted, note.origin, note.source_run) == (
            FACT,
            ("mail",),
            "landlord-mail",
            False,
            "schedule",
            run.id,
        )
        assert (await client.post(f"/api/memories/waiting/{run.id}/0/save")).status_code == 409
        dismissed = await client.post(f"/api/memories/waiting/{run.id}/1/dismiss")
        assert dismissed.status_code == 200 and dismissed.json() == []
        assert await home.runtime.store.count_memories() == 1


async def test_a_run_of_a_chat_saves_its_note_at_once(login_env: dict[str, str]) -> None:
    async with _home(login_env, [_save(FACT, None), FINISH]) as home:
        run: RunRecord = await _run(home, EVAL_SOURCE)
        notes: list[MemoryRecord] = await home.runtime.store.list_memories(10, 0, "changed")
        assert [(note.content, note.origin) for note in notes] == [(FACT, "chat")]
        assert await run_held_notes(home.runtime.store, run.id) == []


async def _update_turns(runtime: Runtime) -> Sequence[ScriptedTurn]:
    """A note of a run that is gone, and the turns of a run that rewrites it."""
    note: MemoryRecord = await runtime.store.add_memory(FACT, (), "run_gone", None, None, False)
    update: ScriptedCall = ScriptedCall(
        tool="memory_update", arguments={"memory_id": note.id, "content": OTHER, "tags": ["mail"]}
    )
    return [_turn(update), FINISH]


async def test_a_scheduled_task_s_change_of_a_note_waits_for_the_owner(login_env: dict[str, str]) -> None:
    async with _home_made(login_env, _update_turns) as home:
        run: RunRecord = await _run(home, SCHEDULE_SOURCE)
        after: MemoryRecord = (await home.runtime.store.list_memories(10, 0, "changed"))[0]
        assert after.content == FACT
        assert after.proposed is not None
        assert (after.proposed.content, after.proposed.run_id) == (OTHER, run.id)


async def test_a_scheduled_task_s_lessons_stay_proposed_under_auto_and_wait_on_the_memory_page(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [
        _turn(ScriptedCall(tool="read_file", arguments={"path": "notes.txt"})),
        _turn(ScriptedCall(tool="write_file", arguments={"path": "notes.txt", "content": "hello\n"})),
        _turn(ScriptedCall(tool="read_file", arguments={"path": "notes.txt"})),
        FINISH,
        _turn(ScriptedCall(tool=LESSONS_TOOL, arguments={"lessons": [LESSON]})),
    ]
    async with _home(login_env, turns) as home, _client(home) as client:
        run: RunRecord = await _run(home, SCHEDULE_SOURCE)
        lessons = await run_lessons(home.runtime.store, run.id)
        assert lessons is not None
        assert [(lesson.text, lesson.state) for lesson in lessons.lessons] == [(LESSON, "proposed")]
        assert await home.runtime.store.count_memories() == 0
        waiting = (await client.get("/api/memories/waiting")).json()
        assert [(item["kind"], item["index"], item["text"]) for item in waiting] == [("lesson", 0, LESSON)]
        saved = await client.post(f"/api/runs/{run.id}/lessons/0/save")
        assert saved.status_code == 200, saved.text
        assert (await client.get("/api/memories/waiting")).json() == []
        notes: list[MemoryRecord] = await home.runtime.store.list_memories(10, 0, "changed")
        assert [(note.content, note.origin) for note in notes] == [(LESSON, "schedule")]
