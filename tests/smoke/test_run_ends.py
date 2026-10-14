"""How a run ends when the end itself is under pressure (item 136): a stop that comes before the run's first step, a
message sent while the end is being recorded, and a store that fails to record it. a run's end. A database that refuses the event of
a run's end keeps none of that end, and a delegated run whose end was not kept is an error for the run that delegated
it (item 179). An end the database refused is stored once it takes it again, a run whose start failed ends with the event
the pages wait for, the error of a failed start stays the one raised when its cleanup fails too, the refusal to delete a
chat names its runs, and the daemon's half-minute check outlives a write the database refuses (item 183). The scripted
model plays the runs; no network and no model service is used.
"""

import asyncio
import secrets
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent import manager as manager_module
from dotagent.agent.attachments import UserInput
from dotagent.agent.gate import RunGate
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import RunConflict, StoreError
from dotagent.core.paths import db_path
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.endings import RESULT_STOPPED
from dotagent.store.models import EventRecord, RunRecord, RunStatus, ThreadRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Say that the work is done."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 30.0
# The pause of the daemon's half-minute check in these tests, and how long an end refused before may take to be
# stored once the database takes it again.
_CHECK_S: float = 0.05
_STORED_WITHIN_S: float = 3.0
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "The work is done.", "success": True}),)
)


class _SteeredAtTheEnd(RunManager):
    """Sends the run a message as its end starts to be recorded, as a user who sends one at that moment does, and
    keeps whether it was refused."""

    def __init__(self, runtime: Runtime) -> None:
        super().__init__(runtime)
        self.refused: list[bool] = []

    async def _record_end(
        self,
        run: RunRecord,
        status: RunStatus,
        result: str,
        needs: str,
        error: JsonObject | None,
        inbox: asyncio.Queue[UserInput],
    ) -> None:
        try:
            self.steer(run.id, "One more thing.", ())
        except RunConflict:
            self.refused.append(True)
        else:
            self.refused.append(False)
        await super()._record_end(run, status, result, needs, error, inbox)


class _StoreFailsAtTheEnd(RunManager):
    """A store that fails as the end of a run is recorded."""

    async def _record_end(
        self,
        run: RunRecord,
        status: RunStatus,
        result: str,
        needs: str,
        error: JsonObject | None,
        inbox: asyncio.Queue[UserInput],
    ) -> None:
        raise StoreError("the database is locked", {"run_id": run.id})


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _runtime(login_env: dict[str, str]) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home whose model is the scripted one, finishing at once."""
    config: AppConfig = eval_config(load_default_config())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_FINISH]))
            yield runtime
        finally:
            await close_runtime(runtime)


async def _start(manager: RunManager) -> RunRecord:
    return await manager.start(GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", "yolo", False, ())


async def _ended(manager: RunManager, run: RunRecord) -> RunRecord:
    async with asyncio.timeout(_TIME_LIMIT_S):
        return await manager.wait(run.id)


async def test_a_run_stopped_before_its_first_step_ends_as_stopped(login_env: dict[str, str]) -> None:
    async with _runtime(login_env) as runtime:
        manager: RunManager = RunManager(runtime)
        try:
            run: RunRecord = await _start(manager)
            # Nothing awaits between the start and the stop: the run's task has not taken a step yet.
            manager.cancel(run.id)
            ended: RunRecord = await _ended(manager, run)
            assert (ended.status, ended.result) == ("cancelled", RESULT_STOPPED)
            assert manager.active_run_ids() == []
        finally:
            await manager.shutdown()


async def test_a_message_sent_while_the_end_is_recorded_is_refused(login_env: dict[str, str]) -> None:
    async with _runtime(login_env) as runtime:
        manager: _SteeredAtTheEnd = _SteeredAtTheEnd(runtime)
        try:
            ended: RunRecord = await _ended(manager, await _start(manager))
            assert ended.status == "done"
            assert manager.refused == [True]
        finally:
            await manager.shutdown()


async def test_a_store_that_fails_to_record_the_end_still_frees_the_run_and_the_task_ends_quietly(
    login_env: dict[str, str],
) -> None:
    async with _runtime(login_env) as runtime:
        manager: _StoreFailsAtTheEnd = _StoreFailsAtTheEnd(runtime)
        try:
            run: RunRecord = await _start(manager)
            task: asyncio.Task[object] = next(task for task in asyncio.all_tasks() if task.get_name() == run.id)
            await _ended(manager, run)
            assert manager.active_run_ids() == []
            # The failure was logged by the run's task, which nothing awaits: the task itself ends without it.
            assert await asyncio.wait_for(task, _TIME_LIMIT_S) is None
            with pytest.raises(RunConflict):
                manager.steer(run.id, "Are you there?", ())
        finally:
            await manager.shutdown()


def _refuse_ends(database: Path) -> None:
    """Make the database at ``database`` refuse the event of every run's end, as a full disk refuses a write."""
    connection: sqlite3.Connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TRIGGER refuse_ends BEFORE INSERT ON events WHEN NEW.kind = 'run_finished'"
            " BEGIN SELECT RAISE(ABORT, 'database or disk is full'); END"
        )
        connection.commit()
    finally:
        connection.close()


async def test_an_end_the_store_refuses_in_part_is_not_kept_at_all(login_env: dict[str, str]) -> None:
    """Item 179: the status of a run's end, the scores of its notes and its ``run_finished`` event are one write, so a
    run is never over in the store without the event a page waits for. Before, the status was kept and the event
    lost."""
    async with _runtime(login_env) as runtime:
        await asyncio.to_thread(_refuse_ends, db_path())
        manager: RunManager = RunManager(runtime)
        try:
            ended: RunRecord = await _ended(manager, await _start(manager))
            assert (ended.status, ended.finished_at) == ("running", None)
            assert await runtime.store.last_event(ended.id, "run_finished") is None
        finally:
            await manager.shutdown()


async def test_a_delegated_run_whose_end_was_not_kept_is_an_error_for_the_run_that_delegated(
    login_env: dict[str, str],
) -> None:
    """Item 179: the run that delegated is told that the end was not recorded; before, it read the delegated run as the
    store had it while it worked, as an outcome."""
    async with _runtime(login_env) as runtime:
        thread: ThreadRecord = await runtime.store.create_thread("delegating", UI_SOURCE)
        parent: RunRecord = await runtime.store.create_run(
            thread.id,
            GOAL,
            "task",
            SCRIPTED_MODEL,
            None,
            UI_SOURCE,
            "medium",
            "standard",
            "yolo",
            False,
            None,
            None,
            None,
        )
        await runtime.store.finish_run(parent.id, "done", "Delegated.")
        await asyncio.to_thread(_refuse_ends, db_path())
        manager: RunManager = RunManager(runtime)
        try:
            with pytest.raises(StoreError, match="could not record its end") as refused:
                await manager.delegate(parent.id, GOAL, "task", None, None)
            child: RunRecord = await runtime.store.get_run(str(refused.value.context["run_id"]))
            assert (child.parent_run_id, child.status) == (parent.id, "running")
        finally:
            await manager.shutdown()


def _allow_ends(database: Path) -> None:
    """Let the database at ``database`` take the ends of runs again (:func:`_refuse_ends`)."""
    connection: sqlite3.Connection = sqlite3.connect(database)
    try:
        connection.execute("DROP TRIGGER refuse_ends")
        connection.commit()
    finally:
        connection.close()


def _refuse_closing(database: Path) -> None:
    """Make the database at ``database`` refuse every change of a run's status, as a full disk refuses a write."""
    connection: sqlite3.Connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TRIGGER refuse_closing BEFORE UPDATE OF status ON runs"
            " BEGIN SELECT RAISE(ABORT, 'database or disk is full'); END"
        )
        connection.commit()
    finally:
        connection.close()


def _gate_refused(*args: object) -> RunGate:
    raise StoreError("the gate of the run could not be made", {})


async def _end_refused(*args: object) -> None:
    raise StoreError("the end of the run could not be stored", {})


async def _stop(watcher: asyncio.Task[None]) -> None:
    watcher.cancel()
    await asyncio.gather(watcher, return_exceptions=True)


async def test_an_end_the_store_refused_is_stored_once_it_takes_it_again(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 183: the daemon's half-minute check stores an end the database refused, as it was to be stored, once the
    database takes it, and the run's chat can be deleted; before, the run stayed at work until the daemon's next
    start."""
    monkeypatch.setattr(manager_module, "COMMAND_CHECK_S", _CHECK_S)
    async with _runtime(login_env) as runtime:
        await asyncio.to_thread(_refuse_ends, db_path())
        manager: RunManager = RunManager(runtime)
        watcher: asyncio.Task[None] = asyncio.create_task(manager.watch_commands())
        try:
            run: RunRecord = await _ended(manager, await _start(manager))
            assert run.status == "running"
            with pytest.raises(RunConflict, match="its end is stored as soon as the database takes it"):
                manager.cancel(run.id)
            async with runtime.bus.subscribe(run.id) as subscription:
                await asyncio.to_thread(_allow_ends, db_path())
                async with asyncio.timeout(_STORED_WITHIN_S):
                    told: EventRecord = await subscription.queue.get()
                    while told.kind != "run_finished":
                        told = await subscription.queue.get()
            stored: RunRecord = await runtime.store.get_run(run.id)
            assert (stored.status, stored.result) == ("done", "The work is done.")
            assert await runtime.store.last_event(run.id, "run_finished") is not None
            assert await runtime.store.delete_threads([run.thread_id]) == [run.thread_id]
        finally:
            await _stop(watcher)
            await manager.shutdown()


async def test_a_run_whose_start_failed_ends_with_the_event_the_pages_wait_for(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 183: the pages were told the run was queued, so its end is stored with its ``run_finished`` event and told
    to them; before, the run was stored failed without one, and a page waited for it."""
    monkeypatch.setattr(manager_module, "RunGate", _gate_refused)
    async with _runtime(login_env) as runtime:
        manager: RunManager = RunManager(runtime)
        try:
            async with runtime.bus.subscribe(None) as subscription:
                with pytest.raises(StoreError, match="the gate of the run could not be made"):
                    await _start(manager)
                kinds: list[str] = [subscription.queue.get_nowait().kind for _ in range(subscription.queue.qsize())]
            [run] = await runtime.store.list_runs(1, None)
            assert run.status == "failed"
            assert "run_finished" in kinds
            finished = await runtime.store.last_event(run.id, "run_finished")
            assert finished is not None and finished.data["result"] == run.result
        finally:
            await manager.shutdown()


async def test_the_error_of_a_failed_campaign_start_stays_when_its_cleanup_fails_too(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 183: when the failed start of a campaign's opening session cannot be stored either, the error of the start
    is the one raised and no campaign is left; before, the refused delete of the campaign's thread replaced it."""
    monkeypatch.setattr(manager_module, "RunGate", _gate_refused)
    async with _runtime(login_env) as runtime:
        monkeypatch.setattr(runtime.store, "end_run", _end_refused)
        monkeypatch.setattr(runtime.store, "finish_run", _end_refused)
        manager: RunManager = RunManager(runtime)
        try:
            with pytest.raises(StoreError, match="the gate of the run could not be made"):
                await manager.start_campaign(
                    GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, "standard", "yolo", None, (), None
                )
            assert await runtime.store.list_campaigns() == []
        finally:
            await manager.shutdown()


async def test_the_refusal_to_delete_a_chat_names_its_runs(login_env: dict[str, str]) -> None:
    """Item 183: the run manager's refusal names the runs, as the store's does (item 179); before, it named the chats
    alone."""
    async with _runtime(login_env) as runtime:
        manager: RunManager = RunManager(runtime)
        try:
            run: RunRecord = await _start(manager)
            # Nothing awaits between the start and the delete: the run is under way.
            with pytest.raises(RunConflict) as refused, manager.deleting_threads([run.thread_id]):
                pass
            assert refused.value.context == {"thread_ids": [run.thread_id], "run_ids": [run.id]}
            await _ended(manager, run)
        finally:
            await manager.shutdown()


async def test_the_half_minute_check_outlives_a_write_the_database_refuses(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 183: a write the database refuses is one of Dot's errors, so the check logs it and goes on; before, the
    database's own error ended the check for the daemon's life."""
    monkeypatch.setattr(manager_module, "COMMAND_CHECK_S", _CHECK_S)
    async with _runtime(login_env) as runtime:
        thread: ThreadRecord = await runtime.store.create_thread("left by a command", UI_SOURCE)
        # A run whose command is gone, with the lock of its owner: the check closes it, if the database lets it.
        await runtime.store.create_run(
            thread.id,
            GOAL,
            "task",
            SCRIPTED_MODEL,
            None,
            UI_SOURCE,
            "medium",
            "standard",
            "yolo",
            False,
            None,
            None,
            secrets.token_hex(16),
        )
        await asyncio.to_thread(_refuse_closing, db_path())
        manager: RunManager = RunManager(runtime)
        watcher: asyncio.Task[None] = asyncio.create_task(manager.watch_commands())
        try:
            await asyncio.sleep(_CHECK_S * 6)
            assert not watcher.done()
        finally:
            await _stop(watcher)
            await manager.shutdown()
