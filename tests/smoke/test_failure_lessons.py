"""A call that fails is answered with what earlier runs learned about its tool (item 1111), in the real loop and store:
the scripted model plays the run, and no network or model service is used."""

import asyncio

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_LESSON: str = "Give `read_file` a path that exists: list the folder with list_dir first."


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


class _LearningProvider(ScriptedProvider):
    """The scripted model, which keeps the lessons in the store as it is first asked: after the run's start, which
    hands a run every note of a store this small, so that only the failure can hand them."""

    def __init__(self, turns: list[ScriptedTurn], store: Store) -> None:
        super().__init__(_PROVIDER, turns)
        self._store: Store = store
        self._kept: bool = False

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if not self._kept:
            self._kept = True
            await self._store.add_memory(_LESSON, ("lesson",), None, None, None, False)
            await self._store.add_memory("`read_file` from a page.", ("lesson",), None, None, None, True)
        return await super().complete(request, sink)


def _turn(tool: str, arguments: dict[str, str | bool]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


async def test_a_failed_call_is_answered_with_the_lessons_that_name_its_tool_once(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [
        _turn("read_file", {"path": "missing-1.txt"}),
        _turn("read_file", {"path": "missing-2.txt"}),
        _turn(FINISH_TOOL, {"answer": "Done.", "success": True}),
    ]
    with scratch_home() as home, eval_environment(home, {}):
        # An eval's config keeps lessons off, so that one task's lessons reach no other: they are on here.
        config = eval_config(load_default_config())
        config = config.model_copy(update={"agent": config.agent.model_copy(update={"lessons": "auto"})})
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, _LearningProvider(turns, runtime.store))
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    "Read my notes.", "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo",
                    False, (),
                )  # fmt: skip
                async with asyncio.timeout(60.0):
                    ended: RunRecord = await manager.wait(run.id)
                assert ended.status == "done", ended
                results: list[EventRecord] = await runtime.store.events_of_kind(run.id, "tool_result", 10)
                first, second = results[0], results[1]
                assert first.data["ok"] is False and _LESSON in str(first.data["text"]), first.data
                # A lesson written after outside content is never handed this way, and one handed is not handed twice.
                assert "from a page" not in str(first.data["text"])
                assert second.data["ok"] is False and _LESSON not in str(second.data["text"]), second.data
                assert run.id in {use for use in await _handed_failure(runtime, run.id)}
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def _handed_failure(runtime: Runtime, run_id: str) -> set[str]:
    rows = await runtime.store._all(  # the record of a use is read as the store keeps it
        "SELECT run_id FROM memory_uses WHERE run_id = ? AND how = 'failure'", (run_id,)
    )
    return {str(row["run_id"]) for row in rows}


async def test_the_calls_of_one_step_that_fail_at_once_are_handed_a_lesson_once(login_env: dict[str, str]) -> None:
    """The pre-push review of item 1111: the calls of a step run at once, and each was handed the same lesson."""
    turns: list[ScriptedTurn] = [
        ScriptedTurn(
            text="",
            calls=tuple(ScriptedCall(tool="read_file", arguments={"path": f"missing-{n}.txt"}) for n in range(3)),
        ),
        _turn(FINISH_TOOL, {"answer": "Done.", "success": True}),
    ]
    with scratch_home() as home, eval_environment(home, {}):
        config = eval_config(load_default_config())
        config = config.model_copy(update={"agent": config.agent.model_copy(update={"lessons": "auto"})})
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, _LearningProvider(turns, runtime.store))
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    "Read my notes.", "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo",
                    False, (),
                )  # fmt: skip
                async with asyncio.timeout(60.0):
                    await manager.wait(run.id)
                results: list[EventRecord] = await runtime.store.events_of_kind(run.id, "tool_result", 10)
                told: list[EventRecord] = await runtime.store.events_of_kind(run.id, "memories", 10)
                assert sum(_LESSON in str(result.data["text"]) for result in results[:3]) == 1, results
                assert [event.data.get("tool") for event in told if "tool" in event.data] == ["read_file"]
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)
