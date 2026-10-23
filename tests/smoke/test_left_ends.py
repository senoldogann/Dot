"""A run whose end the store refused keeps its chat until the end is stored (item 553), in the real manager and store
with a scripted model: the store still has the run at work, and a run started beside it would work on the chat with it
and, once the late end was stored, move the chat's queue as if it had just ended."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.core.errors import RunConflict, StoreError
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

WAIT_S: float = 60.0
GOAL: str = "Say hello."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Hello.", "success": True}),)
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _session(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, RunManager]]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_FINISH, _FINISH]))
            manager: RunManager = RunManager(runtime)
            try:
                yield runtime, manager
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def _send(manager: RunManager, thread_id: str | None) -> RunRecord:
    return await manager.start(
        GOAL, "task", SCRIPTED_MODEL, thread_id, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
    )


async def test_a_chat_whose_last_run_has_an_end_the_store_refused_takes_no_run_until_it_is_stored(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _session(login_env) as (runtime, manager):
        store: Store = runtime.store
        end_run = store.end_run
        refused: list[str] = []

        async def refusing_once(run_id: str, *args: object, **kwargs: object) -> EventRecord:
            if not refused:
                refused.append(run_id)
                raise StoreError("the disk is full", {})
            return await end_run(run_id, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(store, "end_run", refusing_once)
        first: RunRecord = await _send(manager, None)
        async with asyncio.timeout(WAIT_S):
            await manager.wait(first.id)
        # The store still has the run at work: it was refused its end.
        assert refused == [first.id]
        assert (await store.get_run(first.id)).status in ("queued", "running")
        with pytest.raises(RunConflict, match="its end is stored as soon as the database takes it"):
            await _send(manager, first.thread_id)
        # Stored, its chat takes the next run.
        assert await manager.store_left_ends() == 1
        assert (await store.get_run(first.id)).status == "done"
        second: RunRecord = await _send(manager, first.thread_id)
        async with asyncio.timeout(WAIT_S):
            assert (await manager.wait(second.id)).status == "done"
