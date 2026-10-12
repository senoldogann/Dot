"""A reply the model service cut short is not the answer of a run (item 137): the model is told how the reply ended and
asked again, and a run whose replies keep being cut short fails saying so. The scripted model plays the runs and marks
the replies it is told to as cut short; no network and no model service is used.
"""

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, FinishReason, Message, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.endings import PREFIX_CUT_SHORT, classify_ending
from dotagent.store.models import RunMode, RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "What is the answer?"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 30.0
_NOTE_START: str = "[system note] Your last reply was cut short"


class _CutShort(ScriptedProvider):
    """The scripted model, whose replies at the given places in its script the model service ended early, each with
    the finish and the service's own reason given for it."""

    def __init__(self, turns: Sequence[ScriptedTurn], cut: Mapping[int, tuple[FinishReason, str]]) -> None:
        super().__init__(_PROVIDER, turns)
        self._cut: dict[int, tuple[FinishReason, str]] = dict(cut)
        self._replies: int = 0

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        reply: ModelReply = await super().complete(request, sink)
        index: int = self._replies
        self._replies += 1
        if index not in self._cut:
            return reply
        finish, raw_finish = self._cut[index]
        return reply.model_copy(update={"finish": finish, "raw_finish": raw_finish})


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s)


@asynccontextmanager
async def _manager(login_env: dict[str, str], model: _CutShort) -> AsyncIterator[tuple[Runtime, RunManager]]:
    """A run manager in a throw-away home whose model is `model`."""
    config: AppConfig = eval_config(load_default_config())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            try:
                yield runtime, manager
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def _ended(manager: RunManager, mode: RunMode) -> RunRecord:
    run: RunRecord = await manager.start(GOAL, mode, SCRIPTED_MODEL, None, UI_SOURCE, None, None, "yolo", False, ())
    async with asyncio.timeout(_TIME_LIMIT_S):
        return await manager.wait(run.id)


async def _notes(runtime: Runtime, run: RunRecord) -> list[str]:
    """The notes the run's model was sent about replies cut short."""
    messages: list[Message] = await runtime.store.load_messages(run.thread_id, 0)
    return [message.content for message in messages if message.content.startswith(_NOTE_START)]


async def test_a_chat_reply_cut_short_is_asked_again_and_the_complete_one_is_the_answer(
    login_env: dict[str, str],
) -> None:
    model: _CutShort = _CutShort(
        [ScriptedTurn(text="The answer is", calls=()), ScriptedTurn(text="The answer is 42.", calls=())],
        {0: ("length", "length")},
    )
    async with _manager(login_env, model) as (runtime, manager):
        ended: RunRecord = await _ended(manager, "chat")
        assert (ended.status, ended.result) == ("done", "The answer is 42.")
        notes: list[str] = await _notes(runtime, ended)
        assert len(notes) == 1
        assert "it ended with 'length'" in notes[0]


async def test_a_run_whose_replies_keep_being_cut_short_fails_saying_so(login_env: dict[str, str]) -> None:
    nudges: int = load_default_config().agent.finish_nudges
    turns: list[ScriptedTurn] = [
        ScriptedTurn(text=f"Part {index} of the answer", calls=()) for index in range(nudges + 1)
    ]
    model: _CutShort = _CutShort(turns, {index: ("other", "content_filter") for index in range(nudges + 1)})
    async with _manager(login_env, model) as (runtime, manager):
        ended: RunRecord = await _ended(manager, "task")
        assert ended.status == "failed"
        assert ended.result.startswith(PREFIX_CUT_SHORT)
        assert "'content_filter'" in ended.result
        assert classify_ending(ended.status, ended.result) == "cut_short"
        assert len(await _notes(runtime, ended)) == nudges
