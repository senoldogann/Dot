"""A tool that raises fails its call, not its run (item 150): an error that no tool expects reaches the model as a failed
result, with its kind and words, and the run goes on to its end; ``edit_file`` keeps the line ends of a file that breaks
its lines with CRLF, and refuses a file that is not UTF-8 text, saying so, where it used to end the run.

No network and no model service is used: a scripted model calls the tool, then finishes.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool
from dotagent.tools.context import ToolContext
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Change the file."
WAIT_S: float = 60.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),)
)
# Spare finishing turns, for a run that is asked to look again before it ends.
_SPARE: int = 3


class _NoArgs(ToolArgs):
    pass


def _faulty_tool() -> RegisteredTool:
    """A tool with a fault in it: it raises what no tool expects."""

    def classify(args: _NoArgs, ctx: ToolContext) -> ActionInfo:
        return ActionInfo(tool="faulty", kind="read", risk="safe", summary="Add the numbers", target="", flags=())

    async def run(args: _NoArgs, ctx: ToolContext) -> ToolResult:
        raise ValueError("the numbers do not add up")

    return make_tool(
        name="faulty",
        description="Add the numbers.",
        args_model=_NoArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=10.0,
        classify=classify,
        run=run,
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _session(login_env: dict[str, str], call: ScriptedCall) -> AsyncIterator[tuple[Runtime, RunManager]]:
    """A runtime in a throw-away home whose model makes ``call`` and then finishes, and its runs."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            turns: list[ScriptedTurn] = [ScriptedTurn(text="", calls=(call,)), *[_FINISH] * _SPARE]
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
            manager: RunManager = RunManager(runtime)
            try:
                yield runtime, manager
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def _run(manager: RunManager) -> RunRecord:
    run: RunRecord = await manager.start(
        GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", "yolo", False, ()
    )
    async with asyncio.timeout(WAIT_S):
        return await manager.wait(run.id)


async def _results(runtime: Runtime, run: RunRecord, tool: str) -> list[JsonObject]:
    events: list[EventRecord] = await runtime.store.events_of_kind(run.id, "tool_result", 20)
    return [event.data for event in events if event.data["tool"] == tool]


async def test_an_error_no_tool_expects_fails_the_call_and_the_run_goes_on(login_env: dict[str, str]) -> None:
    async with _session(login_env, ScriptedCall(tool="faulty", arguments={})) as (runtime, manager):
        runtime.registry.tools["faulty"] = _faulty_tool()
        ended: RunRecord = await _run(manager)
        assert ended.status == "done", ended.result
        [result] = await _results(runtime, ended, "faulty")
        assert result["ok"] is False
        assert result["text"] == "faulty failed with an unexpected error (ValueError): the numbers do not add up"


async def test_an_edit_keeps_the_crlf_line_ends_of_a_file(login_env: dict[str, str]) -> None:
    edit: ScriptedCall = ScriptedCall(
        tool="edit_file", arguments={"path": "lines.txt", "old": "two\nthree", "new": "2\n3\n3.5"}
    )
    async with _session(login_env, edit) as (_, manager):
        path: Path = workspace_dir() / "lines.txt"
        await asyncio.to_thread(path.write_bytes, b"one\r\ntwo\r\nthree\r\nfour\r\n")
        ended: RunRecord = await _run(manager)
        assert ended.status == "done", ended.result
        assert await asyncio.to_thread(path.read_bytes) == b"one\r\n2\r\n3\r\n3.5\r\nfour\r\n"


async def test_an_edit_of_a_file_that_is_not_utf8_fails_saying_so_and_the_run_goes_on(
    login_env: dict[str, str],
) -> None:
    edit: ScriptedCall = ScriptedCall(
        tool="edit_file", arguments={"path": "latin.txt", "old": "au lait", "new": "noir"}
    )
    async with _session(login_env, edit) as (runtime, manager):
        path: Path = workspace_dir() / "latin.txt"
        await asyncio.to_thread(path.write_bytes, b"caf\xe9 au lait\n")
        ended: RunRecord = await _run(manager)
        assert ended.status == "done", ended.result
        [result] = await _results(runtime, ended, "edit_file")
        assert result["ok"] is False
        assert str(result["text"]).startswith("the file is not UTF-8 text")
        assert await asyncio.to_thread(path.read_bytes) == b"caf\xe9 au lait\n"
