"""The open plan reaches the model as a note after the history when it changes (item 825), not at the end of the system
message (item 87): the system message of every step is the same, and every step's messages begin with the previous
step's, so that a provider's cache of them holds across a change of the plan. A plan that closes is said to be closed.
A scripted model plays the run; no model is called and nothing leaves the Mac."""

import itertools
from collections.abc import Sequence

import pytest

from dotagent.agent.loop import PLAN_CLOSED_NOTE, PLAN_NOTE, plan_due
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, Message, ModelInfo, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import RunRecord
from dotagent.tools.shell import read_login_env

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


class _Recording:
    """The scripted model, keeping every request it was sent."""

    def __init__(self, turns: Sequence[ScriptedTurn]) -> None:
        self._script: ScriptedProvider = ScriptedProvider(_PROVIDER, turns)
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


def _call(tool: str, **arguments: object) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=dict(arguments)),))


def test_a_plan_is_due_when_it_changed_or_its_note_is_old() -> None:
    assert not plan_due(None, None, 0, 5)
    assert plan_due("a", None, 0, 0)
    assert not plan_due("a", "a", 3, 10)
    assert plan_due("b", "a", 3, 4)
    assert plan_due("a", "a", 3, 23)


@pytest.mark.asyncio
async def test_a_change_of_the_plan_leaves_the_system_message_and_the_history_before_it_as_they_were() -> None:
    turns: list[ScriptedTurn] = [
        _call(
            "update_plan",
            steps=[{"text": "Read the notes", "status": "in_progress"}, {"text": "Write it", "status": "pending"}],
        ),
        _call("mark_plan_step", step=1, status="done", evidence="memory_search read the notes: none saved"),
        _call("memory_search", query="the notes"),
        _call("mark_plan_step", step=2, status="done", evidence="the answer says it: no notes to summarise"),
        _call("finish", answer="Done.", success=True),
    ]
    recording: _Recording = _Recording(turns)
    config: AppConfig = eval_config(load_default_config())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, await read_login_env(10.0, ()))
        manager: RunManager = RunManager(runtime)
        try:
            await runtime.router.register(_PROVIDER, recording)
            run: RunRecord = await manager.start(
                "Summarise the notes.", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo",
                False, (),
            )  # fmt: skip
            assert (await manager.wait(run.id)).status == "done"
        finally:
            await manager.shutdown()
            await close_runtime(runtime)
    requests: list[tuple[Message, ...]] = [request.messages for request in recording.requests]
    assert len(requests) == 5
    # The system message of every step is the first step's.
    assert {messages[0] for messages in requests} == {requests[0][0]}
    assert PLAN_NOTE not in requests[0][0].content
    # Each step sends the step before it as it was, and something after it.
    for before, after in itertools.pairwise(requests):
        assert after[: len(before)] == before and len(after) > len(before)
    lasts: list[str] = [messages[-1].content for messages in requests]
    # Written, the plan is handed; marked, it is handed again; unchanged, it is not; closed, it is said to be.
    assert lasts[1].startswith(PLAN_NOTE) and "[in_progress] Read the notes" in lasts[1]
    assert lasts[2].startswith(PLAN_NOTE) and "[done] Read the notes" in lasts[2]
    assert not lasts[3].startswith(PLAN_NOTE) and all(PLAN_NOTE not in message.content for message in requests[3][-2:])
    assert lasts[4] == PLAN_CLOSED_NOTE
