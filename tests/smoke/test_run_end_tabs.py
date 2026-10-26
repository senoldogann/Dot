"""The end of a run tidies its chat's tabs (item 696): the manager tells the browsers, as the run's end is stored and
before the chat's queue moves on, that a helper's chat may close all its tabs and another chat all but its current one.

The runs play in the real loop with the scripted model; the browsers are only asked, no Chrome starts.
"""

import asyncio

import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import RunRecord
from dotagent.tools.context import DelegateOutcome
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 60.0
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
)


async def test_a_runs_end_keeps_its_current_tab_and_a_helpers_end_closes_all(monkeypatch: pytest.MonkeyPatch) -> None:
    config: AppConfig = eval_config(load_default_config())
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s, ())
    told: list[tuple[str, bool]] = []
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_FINISH, _FINISH]))

            async def end_of_run(thread_id: str, *, helper: bool) -> None:
                told.append((thread_id, helper))

            monkeypatch.setattr(runtime.browsers, "end_of_run", end_of_run)
            manager: RunManager = RunManager(runtime)
            try:
                parent: RunRecord = await manager.start(
                    "Look at the page", "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "standard", None, "ask",
                    False, (),
                )  # fmt: skip
                async with asyncio.timeout(_RUN_WAIT_S):
                    parent = await manager.wait(parent.id)
                    helped: DelegateOutcome = await manager.delegate(parent.id, "Read a page", "task", None, None)
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)
    assert parent.status == "done", parent
    assert helped.run.status == "done", helped.run
    assert helped.run.thread_id != parent.thread_id
    assert told == [(parent.thread_id, False), (helped.run.thread_id, True)]
