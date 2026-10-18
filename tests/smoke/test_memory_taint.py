"""A note keeps where it came from, in the real loop and store (item 384). A note saved while its run had read content
third parties can author is marked tainted; a clean run that finds it with memory_search counts as reading such content
again, so the run is tainted and its next memory_save asks the owner first. No network and no model service is used: the
scripted model plays the run.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import WORKING_STATUSES, ApprovalRecord, EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Look the shelf check up and save what you learn."
NOTE: str = "The shelf check prints the number of shelves."
SAVE: str = "The checkout till is by the front door."
WAIT_S: float = 30.0
# What a run that read outside content is asked before it saves a note.
TAINT_REASON: str = "untrusted content is in context"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def _turn(tool: str, arguments: dict[str, str | bool | list[str]]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


_FINISH: ScriptedTurn = _turn(FINISH_TOOL, {"answer": "Done.", "success": True})
_SEARCH: ScriptedTurn = _turn("memory_search", {"query": "shelf check"})
_SAVE: ScriptedTurn = _turn("memory_save", {"content": SAVE})


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config() -> AppConfig:
    """The configuration of an eval run, whose runs wait for the answers to their questions."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(update={"agent": config.agent.model_copy(update={"questions": "wait"})})


@asynccontextmanager
async def _session(login_env: dict[str, str], turns: list[ScriptedTurn]) -> AsyncIterator[tuple[Runtime, RunManager]]:
    """A runtime of the environment's home whose model plays ``turns``, and its runs."""
    runtime: Runtime = await open_runtime(_config(), login_env)
    try:
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
        manager: RunManager = RunManager(runtime)
        try:
            yield runtime, manager
        finally:
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


async def _start(manager: RunManager) -> RunRecord:
    """A run of the owner's message in a new chat."""
    return await manager.start(
        GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "ask", False, ()
    )


async def _asked_or_ended(runtime: Runtime, run_id: str) -> ApprovalRecord | None:
    """What the run ``run_id`` asks the owner, or ``None`` when it ends without asking."""
    deadline: float = time.monotonic() + WAIT_S
    while True:
        asked: list[ApprovalRecord] = [
            record for record in await runtime.store.list_approvals("pending", 20) if record.run_id == run_id
        ]
        if asked:
            return asked[0]
        if (await runtime.store.get_run(run_id)).status not in WORKING_STATUSES:
            return None
        assert time.monotonic() < deadline, f"the run {run_id} neither asked nor ended in {WAIT_S} s"
        await asyncio.sleep(0.05)


async def _ended(manager: RunManager, run_id: str) -> RunRecord:
    async with asyncio.timeout(WAIT_S):
        return await manager.wait(run_id)


async def _results(runtime: Runtime, run_id: str) -> list[tuple[str, bool]]:
    """The tools of the run's results, each with whether the run was tainted after it."""
    events: list[EventRecord] = await runtime.store.events_of_kind(run_id, "tool_result", 10)
    return [(str(event.data["tool"]), event.data.get("tainted") is True) for event in events]


async def test_a_search_that_returns_a_tainted_note_taints_the_run_and_its_next_save_asks(
    login_env: dict[str, str],
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _session(login_env, [_SEARCH, _SAVE, _FINISH]) as (runtime, manager):
            # A note an earlier run saved while it had read content third parties can author.
            await runtime.store.add_memory(NOTE, [], "run_earlier", None, None, tainted=True)
            run: RunRecord = await _start(manager)
            asked: ApprovalRecord | None = await _asked_or_ended(runtime, run.id)
            assert asked is not None, "the note of the run that read the tainted note was saved without asking"
            assert (asked.tool, TAINT_REASON in asked.summary) == ("memory_save", True)
            # The search result itself says the run is tainted now; it started untainted.
            assert ("memory_search", True) in await _results(runtime, run.id)
            await manager.decide(asked.id, False, UI_SOURCE, "")
            assert (await _ended(manager, run.id)).status == "done"
