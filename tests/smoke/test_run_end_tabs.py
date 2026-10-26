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
from dotagent.scheduler import SCHEDULE_SOURCE
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


async def test_a_run_nobody_watches_is_marked_for_the_browser_while_it_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Item 699: a schedule's run, and a helper of one, is a run nobody watches for as long as it works (its calls do
    not wait again for an Allow of Chrome nobody clicked); the owner's run is not."""
    config: AppConfig = eval_config(load_default_config())
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s, ())
    marks: list[tuple[str, bool]] = []
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_FINISH, _FINISH]))
            marking = runtime.browsers.unattended

            def unattended(thread_id: str, nobody_watches: bool) -> None:
                marks.append((thread_id, nobody_watches))
                marking(thread_id, nobody_watches)

            monkeypatch.setattr(runtime.browsers, "unattended", unattended)
            manager: RunManager = RunManager(runtime)
            try:
                scheduled: RunRecord = await manager.start(
                    "Check the page", "task", SCRIPTED_MODEL, None, SCHEDULE_SOURCE, None, None, "standard", None,
                    "ask", False, (),
                )  # fmt: skip
                owners: RunRecord = await manager.start(
                    "Look at the page", "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "standard", None, "ask",
                    False, (),
                )  # fmt: skip
                async with asyncio.timeout(_RUN_WAIT_S):
                    scheduled = await manager.wait(scheduled.id)
                    owners = await manager.wait(owners.id)
                # A helper of a schedule's run at work is one nobody watches too.
                runtime.browsers.unattended(scheduled.thread_id, True)
                helper: RunRecord = scheduled.model_copy(
                    update={"id": "run_helper", "parent_run_id": scheduled.id, "source": "delegate"}
                )
                assert await manager._nobody_watches(helper)
                runtime.browsers.unattended(scheduled.thread_id, False)
                assert not await manager._nobody_watches(helper)
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)
    assert marks.index((scheduled.thread_id, True)) < marks.index((scheduled.thread_id, False))
    assert (owners.thread_id, False) in marks and (owners.thread_id, True) not in marks
