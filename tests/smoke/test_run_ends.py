"""How a run ends when the end itself is under pressure (item 136): a stop that comes before the run's first step, a
message sent while the end is being recorded, and a store that fails to record it. The scripted model plays the runs;
no network and no model service is used.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.attachments import UserInput
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import RunConflict, StoreError
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.endings import RESULT_STOPPED
from dotagent.store.models import RunRecord, RunStatus
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Say that the work is done."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 30.0
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
        self, run: RunRecord, status: RunStatus, result: str, needs: str, inbox: asyncio.Queue[UserInput]
    ) -> None:
        try:
            self.steer(run.id, "One more thing.", ())
        except RunConflict:
            self.refused.append(True)
        else:
            self.refused.append(False)
        await super()._record_end(run, status, result, needs, inbox)


class _StoreFailsAtTheEnd(RunManager):
    """A store that fails as the end of a run is recorded."""

    async def _record_end(
        self, run: RunRecord, status: RunStatus, result: str, needs: str, inbox: asyncio.Queue[UserInput]
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
    return await manager.start(GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "yolo", False, ())


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
