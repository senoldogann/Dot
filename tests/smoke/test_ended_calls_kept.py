"""A step that is cut keeps the results of the calls that had ended (items 159 and 552), in the real loop and store with
a scripted model: a stop cut it, and so did the desktop's fail-safe and a store that could not write, and the thread
the next run reads says what the first call did instead of letting it be made again."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import FailSafeTriggered, StoreError
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.events import EventBus
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import Message, ToolSpec
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.base import ALL_MODES, PreparedCall, RegisteredTool, ToolResult, ok
from dotagent.tools.context import ToolContext
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

WAIT_S: float = 60.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
FIRST: str = "sends_message"
SECOND: str = "hits_corner"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _tool(name: str, run: Callable[[], Awaitable[ToolResult]]) -> RegisteredTool:
    """A tool of the test: a call of it runs ``run``."""

    async def prepare(raw: JsonObject, ctx: ToolContext) -> PreparedCall:
        async def go(context: ToolContext) -> ToolResult:
            return await run()

        action: ActionInfo = ActionInfo(
            tool=name, kind="read", risk="low", summary=f"call {name}", target=name, flags=()
        )
        return PreparedCall(action=action, run=go)

    return RegisteredTool(
        spec=ToolSpec(name=name, description=f"A call of the test: {name}.", parameters={"type": "object"}),
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=10.0,
        prepare=prepare,
    )


async def _sends() -> ToolResult:
    return ok("the message was sent")


async def _hits_the_corner() -> ToolResult:
    raise FailSafeTriggered("the pointer is in the corner of the screen", {})


@asynccontextmanager
async def _session(login_env: dict[str, str], turns: list[ScriptedTurn]) -> AsyncIterator[tuple[Runtime, RunManager]]:
    config: AppConfig = eval_config(load_default_config())
    runtime: Runtime = await open_runtime(config, login_env)
    try:
        runtime.registry.tools[FIRST] = _tool(FIRST, _sends)
        runtime.registry.tools[SECOND] = _tool(SECOND, _hits_the_corner)
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
        manager: RunManager = RunManager(runtime)
        try:
            yield runtime, manager
        finally:
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


def _store_fails_at_the_second_call(bus: EventBus, monkeypatch: pytest.MonkeyPatch) -> None:
    """The store cannot write the event of the second call, which is outside the call's own error handling."""
    emit: Callable[[str, str, JsonObject, bool], Awaitable[EventRecord]] = bus.emit

    async def failing(run_id: str, kind: str, data: JsonObject, persist: bool) -> EventRecord:
        if kind == "tool_call" and data.get("tool") == SECOND:
            raise StoreError("the disk is full", {})
        return await emit(run_id, kind, data, persist)

    monkeypatch.setattr(bus, "emit", failing)


@pytest.mark.parametrize("cut_by", ["fail-safe", "store"])
async def test_the_results_of_the_calls_that_had_ended_stay_in_the_thread_of_a_step_that_was_cut(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, cut_by: str
) -> None:
    turns: list[ScriptedTurn] = [
        ScriptedTurn(
            text="",
            calls=(ScriptedCall(tool=FIRST, arguments={}), ScriptedCall(tool=SECOND, arguments={})),
        )
    ]
    with scratch_home() as home, eval_environment(home, {}):
        async with _session(login_env, turns) as (runtime, manager):
            if cut_by == "store":
                _store_fails_at_the_second_call(runtime.bus, monkeypatch)
            run: RunRecord = await manager.start(
                "Send it and click.",
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
            assert ended.status in ("cancelled", "failed"), ended
            thread: list[Message] = await runtime.store.load_messages(run.thread_id, 0)
            answers: dict[str, str] = {
                message.tool_name or "": message.content for message in thread if message.role == "tool"
            }
            # The call that had ended is answered with what it did; the one that was cut has no answer to keep.
            assert answers == {FIRST: "the message was sent"}, thread


async def test_the_results_stay_when_the_step_is_cut_while_its_images_are_described(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 879: every call had ended, and the step was cut while a model that cannot see had their images
    described (a stop, or the vision model failing): the results stay as well."""
    from dotagent.agent import loop

    async def describing(*_args: object) -> list[Message]:
        raise StoreError("the vision model was being asked when the run stopped", {})

    monkeypatch.setattr(loop, "_tool_messages", describing)
    turns: list[ScriptedTurn] = [ScriptedTurn(text="", calls=(ScriptedCall(tool=FIRST, arguments={}),))]
    with scratch_home() as home, eval_environment(home, {}):
        async with _session(login_env, turns) as (runtime, manager):
            run: RunRecord = await manager.start(
                "Send it.",
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
            assert ended.status in ("cancelled", "failed"), ended
            thread: list[Message] = await runtime.store.load_messages(run.thread_id, 0)
            answers: dict[str, str] = {
                message.tool_name or "": message.content for message in thread if message.role == "tool"
            }
            assert answers == {FIRST: "the message was sent"}, thread
