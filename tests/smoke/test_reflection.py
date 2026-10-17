"""The nightly reflection in the real loop (item 70): four scripted runs end (done, failed, stopped, and done after a
stumble, which the loop asks about at its end), two lesson notes that say the same and one stale lesson are in the
memory, and the scheduler's tick at the reflection's time fires it. The reflection asks the review model about the
failed and the stopped run only, saves their lessons once the second look keeps them (``auto``), folds the duplicates
into one, rests the stale lesson
(a search no longer finds it), writes the day's journal note and stands in the Activity list; the next tick does not
fire it again.

No model service is used: the scripted model plays the runs and answers for the review model too (see ``eval_config``).
"""

import asyncio
import sqlite3
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.lessons import LESSON_TAG, LESSONS_PROPOSED
from dotagent.agent.manager import RunManager
from dotagent.agent.reflection import last_reflection, reflection_cron
from dotagent.agent.reflection_record import REFLECTION_KEY, Reflection, summary_line
from dotagent.agent.review import LESSONS_TOOL, OWNER_TOOL, VET_TOOL
from dotagent.config import AppConfig, load_default_config
from dotagent.core.paths import db_path
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.memory.service import (
    JOURNAL_TAG,
    REFLECTION_SOURCE,
    REST_TAG,
    MemoryHit,
    OwnerSection,
    owner_section,
    recall,
)
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.scheduler.runner import Scheduler
from dotagent.scheduler.timing import next_firing
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, MemoryRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "reflection-test-token"
PORT: int = 18767
NOTES: str = "notes.txt"
DRAFT: str = "draft.txt"
LESSON_STUMBLE: str = "read_file fails on a file that does not exist yet; create notes.txt with write_file first."
LESSON_FAILED: str = "Check that draft.txt exists with list_dir before the goal depends on reading it."
LESSON_STOPPED: str = "A command that sleeps for long holds the whole run; prefer short commands and poll."
TWIN_OLDER: str = "pdftotext keeps the columns of a table only with the -layout flag."
TWIN_NEWER: str = "pdftotext keeps the columns of a table only with the -layout flag set."
STALE: str = "Large CSV files load faster with the csv module than with pandas on this Mac."
HURT: str = "Paste the whole page into the answer instead of reading it first."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_STEP_WAIT_S: float = 20.0
_DAY_S: float = 86400.0


class RecordingProvider:
    """The scripted model, keeping every request it is sent."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


@dataclass(frozen=True)
class Home:
    runtime: Runtime
    manager: RunManager
    model: RecordingProvider


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config() -> AppConfig:
    """An eval configuration with ``auto`` lessons and the scripted model as the review model too."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"lessons": "auto"}),
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
        }
    )


@asynccontextmanager
async def _home(login_env: dict[str, str], turns: Sequence[ScriptedTurn]) -> AsyncIterator[Home]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(), login_env)
        try:
            model: RecordingProvider = RecordingProvider(ScriptedProvider(_PROVIDER, turns))
            await runtime.router.register(_PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            try:
                yield Home(runtime=runtime, manager=manager, model=model)
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


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _read(name: str) -> ScriptedCall:
    return ScriptedCall(tool="read_file", arguments={"path": name})


def _write(name: str) -> ScriptedCall:
    return ScriptedCall(tool="write_file", arguments={"path": name, "content": "hello\n"})


def _finish(answer: str, success: bool) -> ScriptedTurn:
    return _turn(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": success}))


OWNER_LINE_LANGUAGE: str = "Writes goals in English and keeps notes in notes.txt."
OWNER_LINE_FILES: str = "Works in a scratch workspace with small text files."
OWNER_OWN_LINE: str = "I read Turkish; answer me in Turkish."


def _owner(*lines: str) -> ScriptedTurn:
    """The review model's answer to the owner writer's question."""
    return _turn(ScriptedCall(tool=OWNER_TOOL, arguments={"lines": list(lines)}))


def _lessons(*lessons: str) -> ScriptedTurn:
    return _turn(ScriptedCall(tool=LESSONS_TOOL, arguments={"lessons": list(lessons)}))


def _kept() -> ScriptedTurn:
    """The second look's answer that keeps the one lesson it is asked about."""
    return _turn(
        ScriptedCall(tool=VET_TOOL, arguments={"verdicts": [{"lesson": 1, "keep": True, "reason": "know-how"}]})
    )


async def _start(home: Home, goal: str, approvals: str) -> RunRecord:
    return await home.manager.start(
        goal, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, approvals, False, ()
    )  # type: ignore[arg-type]


async def _ended(home: Home, run: RunRecord) -> RunRecord:
    async with asyncio.timeout(_TIME_LIMIT_S):
        record: RunRecord = await home.manager.wait(run.id)
        await home.manager.learned(run.id)
    return record


async def _first_tool_call(store: Store, run_id: str) -> None:
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        calls: list[EventRecord] = await store.events_of_kind(run_id, "tool_call", 1)
        if calls:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} made no tool call in {_STEP_WAIT_S} s")


def _lesson_prompts(home: Home, run: RunRecord) -> list[str]:
    return [
        request.messages[-1].content
        for request in home.model.requests
        if request.session_key == f"{run.thread_id}:lessons"
    ]


def _lesson_systems(home: Home, run: RunRecord) -> list[str]:
    return [
        request.messages[0].content
        for request in home.model.requests
        if request.session_key == f"{run.thread_id}:lessons"
    ]


def _backdate(memory_id: str, created_at: float) -> None:
    """Make a note look as if an older daemon had written it: the store writes ``now`` and nothing else."""
    connection: sqlite3.Connection = sqlite3.connect(db_path())
    try:
        connection.execute(
            "UPDATE memories SET created_at = ?, updated_at = ? WHERE id = ?", (created_at, created_at, memory_id)
        )
        connection.commit()
    finally:
        connection.close()


async def test_the_reflection_learns_from_the_runs_the_loop_did_not_ask_tidies_the_lessons_and_writes_the_journal(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [
        # A: done, nothing went wrong.
        _finish("Nothing to do.", True),
        # B: failed after a read of a file that does not exist.
        _turn(_read(DRAFT)),
        _finish("Could not read the draft.", False),
        # C: stopped while a command sleeps.
        _turn(ScriptedCall(tool="shell", arguments={"command": "sleep 30"})),
        # D: done after a stumble; the loop asks about it at its end.
        _turn(_read(NOTES)),
        _turn(_write(NOTES)),
        _turn(_read(NOTES)),
        _finish("The file holds hello.", True),
        _lessons(LESSON_STUMBLE),
        _kept(),
        # The reflection's questions, about B and then C, each with its second look.
        _lessons(LESSON_FAILED),
        _kept(),
        _lessons(LESSON_STOPPED),
        _kept(),
        # The owner writer's answer: what Dot knows about the owner after the day.
        _owner(OWNER_LINE_LANGUAGE, OWNER_LINE_FILES),
    ]
    async with _home(login_env, turns) as home:
        store: Store = home.runtime.store
        clean: RunRecord = await _ended(home, await _start(home, "Do nothing.", "ask"))
        failed: RunRecord = await _ended(home, await _start(home, "Read the draft.", "ask"))
        sleeping: RunRecord = await _start(home, "Wait a while.", "yolo")
        await _first_tool_call(store, sleeping.id)
        home.manager.cancel(sleeping.id)
        stopped: RunRecord = await _ended(home, sleeping)
        stumbled: RunRecord = await _ended(home, await _start(home, "Keep hello in notes.txt.", "ask"))
        assert [run.status for run in (clean, failed, stopped, stumbled)] == ["done", "failed", "cancelled", "done"]
        assert await store.last_event(stumbled.id, LESSONS_PROPOSED) is not None
        # Two lesson notes that say the same, and one that no run was handed for over sixty days.
        older: MemoryRecord = await store.add_memory(TWIN_OLDER, (LESSON_TAG,), "run_old", None, None)
        newer: MemoryRecord = await store.add_memory(TWIN_NEWER, (LESSON_TAG, "pdf"), "run_new", None, None)
        stale: MemoryRecord = await store.add_memory(STALE, (LESSON_TAG,), "run_stale", None, None)
        _backdate(older.id, time.time() - 2 * _DAY_S)
        _backdate(stale.id, time.time() - 61 * _DAY_S)
        # And one handed to three runs that all failed: its score retires it.
        hurt: MemoryRecord = await store.add_memory(HURT, (LESSON_TAG,), "run_hurt", None, None)
        for run_id in ("run_h1", "run_h2", "run_h3"):
            await store.mark_memories_used(run_id, [hurt.id], time.time(), "start")
            await store.close_memory_uses(run_id, "failed")
        assert await store.kv_get(REFLECTION_KEY) is None

        scheduler: Scheduler = Scheduler(home.runtime, home.manager)
        fire_at: float | None = next_firing(reflection_cron("03:30"), time.time(), home.runtime.schedule_zone)
        assert fire_at is not None
        await scheduler.tick(fire_at + 1)
        assert scheduler.reflecting()
        async with asyncio.timeout(_TIME_LIMIT_S):
            await scheduler.drain()
        reflection: Reflection | None = await last_reflection(store)
        assert reflection is not None
        assert (
            reflection.at,
            reflection.runs_read,
            reflection.lessons_written,
            reflection.merged,
            reflection.retired,
        ) == (
            fire_at + 1,
            4,
            2,
            1,
            2,
        )
        assert summary_line(reflection) == "Reflected on 4 runs: 2 lessons, 1 merged, 2 retired."

        # The review model was asked once about the failed and the stopped run, with their ending as a signal, and not
        # about the clean run nor again about the one the loop asked at its end.
        failed_prompts: list[str] = _lesson_prompts(home, failed)
        stopped_prompts: list[str] = _lesson_prompts(home, stopped)
        assert len(failed_prompts) == 1 and "The run ended failed: Could not read the draft." in failed_prompts[0]
        assert f"- read_file {home.runtime.workspace}/{DRAFT} failed (" in failed_prompts[0]
        assert len(stopped_prompts) == 1 and "The run was stopped before it finished" in stopped_prompts[0]
        assert _lesson_prompts(home, clean) == []
        assert len(_lesson_prompts(home, stumbled)) == 1
        # Item 154: the failed and the stopped run are asked about in words that say they did not reach their goal.
        for system in (*_lesson_systems(home, failed), *_lesson_systems(home, stopped)):
            assert "did not reach its goal" in system and "reached its goal, but" not in system
        assert "reached its goal, but not at the first try" in _lesson_systems(home, stumbled)[0]
        lessons: list[MemoryRecord] = await store.memories_with_tag(LESSON_TAG, 50)
        assert {
            note.content: note.source_run
            for note in lessons
            if note.content.startswith(("read_file", "Check", "A command"))
        } == {
            LESSON_STUMBLE: stumbled.id,
            LESSON_FAILED: failed.id,
            LESSON_STOPPED: stopped.id,
        }

        # The older twin was folded into the newer, which kept both notes' tags, and rests naming it (item 306); the
        # stale lesson rests and a search no longer finds it, while the Memory page still lists it.
        twin: MemoryRecord = await store.get_memory(older.id)
        assert REST_TAG in twin.tags and twin.replaced_by == newer.id
        assert (await store.get_memory(newer.id)).tags == ("lesson", "pdf")
        rested: MemoryRecord = await store.get_memory(stale.id)
        assert rested.tags == (LESSON_TAG, REST_TAG)
        assert (await store.get_memory(hurt.id)).tags == (LESSON_TAG, REST_TAG)
        hits: list[MemoryHit] = await recall(home.runtime.memory, STALE, 5)
        assert stale.id not in {hit.record.id for hit in hits}
        assert stale.id in {note.id for note in await store.list_memories(50, 0)}

        # The journal note is the reflection's own, not the owner's, and says what the day held.
        journals: list[MemoryRecord] = await store.memories_with_tag(JOURNAL_TAG, 5)
        assert len(journals) == 1 and journals[0].id == reflection.journal
        assert journals[0].source_run == REFLECTION_SOURCE
        assert "I worked on 4 runs (2 done, 1 failed, 1 cancelled). Learned: " in journals[0].content
        assert "Tidied the lessons: 1 merged, 2 retired." in journals[0].content
        assert LESSON_FAILED in journals[0].content

        # Activity lists the reflection among what ended today; the next tick does not fire it again.
        async with _client(home) as client:
            view: httpx.Response = await client.get("/api/activity")
        assert view.status_code == 200, view.text
        rows: list[dict[str, object]] = [row for row in view.json()["recent"] if row["kind"] == "reflection"]
        assert [(row["title"], row["href"], row["since"]) for row in rows] == [
            (summary_line(reflection), "#/memory", fire_at + 1)
        ]
        await scheduler.tick(fire_at + 60)
        assert not scheduler.reflecting()
        assert await last_reflection(store) == reflection
        assert sum(1 for request in home.model.requests if request.session_key.endswith(":lessons")) == 3

        # The owner section was written from the day: the writer saw the goals, and the API shows and takes the
        # owner's own lines, which the section keeps beside Dot's.
        assert reflection.owner_updated
        section: OwnerSection = await owner_section(store)
        assert section.dot_lines == (OWNER_LINE_LANGUAGE, OWNER_LINE_FILES) and section.owner_lines == ()
        asked_owner: list[str] = [
            request.messages[-1].content for request in home.model.requests if request.session_key.endswith(":owner")
        ]
        assert len(asked_owner) == 1 and "Read the draft." in asked_owner[0]
        async with _client(home) as client:
            shown: httpx.Response = await client.get("/api/memories/owner")
            assert shown.status_code == 200 and shown.json()["dot_lines"] == [OWNER_LINE_LANGUAGE, OWNER_LINE_FILES]
            put: httpx.Response = await client.put("/api/memories/owner", json={"lines": [OWNER_OWN_LINE]})
            assert put.status_code == 200, put.text
            assert put.json()["owner_lines"] == [OWNER_OWN_LINE]
        kept: OwnerSection = await owner_section(store)
        assert kept.owner_lines == (OWNER_OWN_LINE,) and kept.dot_lines == section.dot_lines
        assert EVAL_SOURCE not in {run.source for run in (clean, failed, stopped, stumbled)}
