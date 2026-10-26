"""A run that calls finish in the same reply as a call that did not work is sent back once to read what happened (item
552), in the real loop and store with a scripted model: its answer was written before it saw the result, and a run
that ends on it reports work that did not happen as done. The plan's bookkeeping is not such a call."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.loop import FINISH_AFTER_FAILURE_EVENT
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL, MARK_PLAN_STEP_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

WAIT_S: float = 60.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
FIRST: str = "I read the file; it says hello."
SECOND: str = "The file does not exist, so I could not read it."
READ_MISSING: ScriptedCall = ScriptedCall(tool="read_file", arguments={"path": "does-not-exist.txt"})
BAD_MARK: ScriptedCall = ScriptedCall(tool=MARK_PLAN_STEP_TOOL, arguments={"step": 7, "status": "done"})


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _finish(answer: str) -> ScriptedCall:
    return ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True})


@asynccontextmanager
async def _session(login_env: dict[str, str], turns: list[ScriptedTurn]) -> AsyncIterator[tuple[Runtime, RunManager]]:
    config: AppConfig = eval_config(load_default_config())
    runtime: Runtime = await open_runtime(config, login_env)
    try:
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
        manager: RunManager = RunManager(runtime)
        try:
            yield runtime, manager
        finally:
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


async def _run(
    login_env: dict[str, str], turns: list[ScriptedTurn]
) -> tuple[RunRecord, list[EventRecord], list[EventRecord]]:
    """The run the script plays once it has ended, the events of it that sent it back, and the results of its calls."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _session(login_env, turns) as (runtime, manager):
            run: RunRecord = await manager.start(
                "Read the file.",
                "task",
                SCRIPTED_MODEL,
                None,
                UI_SOURCE,
                None,
                None,
                "standard",
                None,
                "yolo",
                False,
                (),
            )
            async with asyncio.timeout(WAIT_S):
                ended: RunRecord = await manager.wait(run.id)
            sent_back: list[EventRecord] = await runtime.store.events_of_kind(run.id, FINISH_AFTER_FAILURE_EVENT, 10)
            return ended, sent_back, await runtime.store.events_of_kind(run.id, "tool_result", 20)


async def test_a_finish_in_the_reply_of_a_failed_call_is_sent_back_once(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [
        ScriptedTurn(text="", calls=(READ_MISSING, _finish(FIRST))),
        # Sent back: the model has read the failure, and ends the run again, with a call that fails once more; it is
        # sent back once and no more.
        ScriptedTurn(text="", calls=(READ_MISSING, _finish(SECOND))),
    ]
    ended, sent_back, results = await _run(login_env, turns)
    assert (ended.status, ended.result) == ("done", SECOND), ended
    assert [event.data["calls"] for event in sent_back] == [["read_file"]], sent_back
    assert [(event.data["tool"], event.data["ok"]) for event in results] == [
        ("read_file", False),
        (FINISH_TOOL, True),
        ("read_file", False),
        (FINISH_TOOL, True),
    ]


async def test_a_finish_that_read_what_happened_ends_the_run_as_it_says(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [
        ScriptedTurn(text="", calls=(READ_MISSING,)),
        ScriptedTurn(text="", calls=(_finish(SECOND),)),
    ]
    ended, sent_back, _ = await _run(login_env, turns)
    assert (ended.status, ended.result, sent_back) == ("done", SECOND, []), ended


async def test_a_plan_call_that_failed_does_not_hold_back_a_finish(login_env: dict[str, str]) -> None:
    ended, sent_back, results = await _run(login_env, [ScriptedTurn(text="", calls=(BAD_MARK, _finish(FIRST)))])
    assert (ended.status, ended.result, sent_back) == ("done", FIRST, []), ended
    assert [(event.data["tool"], event.data["ok"]) for event in results] == [
        (MARK_PLAN_STEP_TOOL, False),
        (FINISH_TOOL, True),
    ]


async def test_a_finish_before_other_calls_ends_nothing_and_those_calls_run(login_env: dict[str, str]) -> None:
    """The mail of ``[finish, send_email]`` is sent, and the run ends on the finish that comes after reading it (item
    685)."""
    turns: list[ScriptedTurn] = [
        ScriptedTurn(
            text="", calls=(_finish(FIRST), ScriptedCall(tool="shell", arguments={"command": "echo sent-42"}))
        ),
        ScriptedTurn(text="", calls=(_finish(SECOND),)),
    ]
    ended, sent_back, results = await _run(login_env, turns)
    assert (ended.status, ended.result, sent_back) == ("done", SECOND, []), ended
    assert [(event.data["tool"], event.data["ok"]) for event in results] == [
        (FINISH_TOOL, False),
        ("shell", True),
        (FINISH_TOOL, True),
    ]
    assert "finish must be the last call of a reply" in str(results[0].data["text"])
    assert "(shell)" in str(results[0].data["text"])
    assert "sent-42" in str(results[1].data["text"])
