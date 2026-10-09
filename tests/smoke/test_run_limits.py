"""Open-ended goals in the real loop: the targets of a plan outlive its changes and reach the goal check, and a run
past its limit is told to finish, may only finish or plan, and ends there whatever it replies.

No network and no model service is used: the scripted model plays the run and answers for the review model too.
"""

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.limits import LIMIT_EVENT, LIMIT_TOOLS
from dotagent.agent.manager import RunManager
from dotagent.agent.review import GOAL_TOOL
from dotagent.config import AppConfig, load_default_config
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.endings import PREFIX_LIMIT, classify_ending
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL, MARK_PLAN_STEP_TOOL, UPDATE_PLAN_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Collect as many leads as you can into leads.csv."
TARGETS: str = "20 leads in leads.csv, counted with wc -l"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_MAX_EVENTS: int = 10_000


class RecordingProvider:
    """The scripted model, keeping every request it is sent."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


class Played(FrozenModel):
    """A run that is over: the workspace it worked in, its record and events, and the requests of its steps and of its
    goal checks."""

    workspace: Path
    record: RunRecord
    events: tuple[EventRecord, ...]
    steps: tuple[ChatRequest, ...]
    checks: tuple[ChatRequest, ...]

    def data(self, kind: str) -> tuple[JsonObject, ...]:
        return tuple(event.data for event in self.events if event.kind == kind)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s)


def _config(max_minutes: float, max_tokens: int) -> AppConfig:
    """The configuration of an eval run with these limits, and the scripted model as the review model too."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"run_max_minutes": max_minutes, "run_max_tokens": max_tokens}),
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
        }
    )


async def _play(config: AppConfig, login_env: dict[str, str], goal_mode: bool, turns: Sequence[ScriptedTurn]) -> Played:
    """``GOAL`` as an unattended run of a model that plays ``turns``, in a throw-away home."""
    model: RecordingProvider = RecordingProvider(ScriptedProvider(_PROVIDER, turns))
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "ask", goal_mode, ()
                )
                async with asyncio.timeout(_TIME_LIMIT_S):
                    record: RunRecord = await manager.wait(run.id)
            finally:
                await manager.shutdown()
            events: list[EventRecord] = await runtime.store.list_events(run.id, 0, _MAX_EVENTS)
        finally:
            await close_runtime(runtime)
    return Played(
        workspace=runtime.workspace,
        record=record,
        events=tuple(events),
        steps=tuple(request for request in model.requests if request.session_key == record.thread_id),
        checks=tuple(request for request in model.requests if request.session_key == f"{record.thread_id}:goal"),
    )


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _call(tool: str, arguments: JsonObject) -> ScriptedCall:
    return ScriptedCall(tool=tool, arguments=arguments)


def _plan(statuses: Sequence[str], targets: str | None) -> ScriptedCall:
    steps: list[JsonObject] = [
        {"text": f"Round {number}", "status": status} for number, status in enumerate(statuses, start=1)
    ]
    return _call(UPDATE_PLAN_TOOL, {"steps": steps} if targets is None else {"steps": steps, "targets": targets})


def _write(path: str) -> ScriptedCall:
    return _call("write_file", {"path": path, "content": "name,email\n"})


def _finish(answer: str, success: bool) -> ScriptedTurn:
    return _turn(_call(FINISH_TOOL, {"answer": answer, "success": success}))


def _offered(request: ChatRequest) -> frozenset[str]:
    return frozenset(spec.name for spec in request.tools)


async def test_the_targets_outlive_the_plan_changes_and_reach_the_goal_check(login_env: dict[str, str]) -> None:
    played: Played = await _play(
        _config(0, 0),
        login_env,
        True,
        [
            _turn(_plan(["in_progress", "pending"], TARGETS)),
            # A new plan without targets keeps them; marking a step keeps them.
            _turn(_write("leads.csv"), _plan(["done", "in_progress"], None)),
            _turn(_call(MARK_PLAN_STEP_TOOL, {"step": 2, "status": "done"})),
            _finish("leads.csv has 20 leads; wc -l counts 21 lines with the header.", True),
            _turn(_call(GOAL_TOOL, {"decision": "achieved", "reason": "The count is in the digest."})),
        ],
    )
    assert played.record.status == "done", played.record
    assert [plan["targets"] for plan in played.data("plan")] == [TARGETS, TARGETS, TARGETS]
    assert f"Plan recorded (2/2 done):\n1. [done] Round 1\n2. [done] Round 2\nTargets: {TARGETS}" in "\n".join(
        message.content for message in played.steps[-1].messages
    )
    assert len(played.checks) == 1
    system, prompt = (message.content for message in played.checks[0].messages)
    assert "Targets are the agent's own measurable reading of a goal" in system
    assert "<<TARGETS (the agent's own, from its plan) " in prompt
    assert f"\n{TARGETS}\n<<end TARGETS (the agent's own, from its plan) " in prompt
    assert played.data(LIMIT_EVENT) == ()


async def test_a_run_past_its_token_limit_may_only_finish_and_ends_at_the_limit_when_it_does_not(
    login_env: dict[str, str],
) -> None:
    # Every scripted reply counts 1000 input and 50 output tokens: the limit is passed by the third step.
    played: Played = await _play(
        _config(0, 2500),
        login_env,
        False,
        [
            _turn(_plan(["in_progress", "pending"], TARGETS)),
            _turn(_write("leads.csv")),
            _turn(_call("read_file", {"path": "leads.csv"})),
            # Told to finish, the model goes on working: the call is refused and the run ends.
            _turn(_write("more.csv")),
        ],
    )
    assert played.record.status == "failed", played.record
    assert classify_ending(played.record.status, played.record.result) == "limit"
    assert played.record.result.startswith(
        f"{PREFIX_LIMIT} (3,150 of 2,500 tokens) and did not finish when it was told to."
    ), played.record.result
    assert f"Its plan:\n1. [in_progress] Round 1\n2. [pending] Round 2\nTargets: {TARGETS}" in played.record.result
    (limit,) = played.data(LIMIT_EVENT)
    assert (limit["tokens"], limit["max_minutes"], limit["max_tokens"]) == (3150, 0.0, 2500)
    assert len(played.steps) == 4
    assert [_offered(request) == LIMIT_TOOLS for request in played.steps] == [False, False, False, True]
    assert (
        played.steps[3]
        .messages[-1]
        .content.startswith(
            "[system note] The limit of this run is reached: it has used 3,150 of 2,500 tokens. Finish now"
        )
    )
    assert not (played.workspace / "more.csv").exists()


async def test_a_run_that_finishes_past_its_time_limit_ends_as_it_says_and_is_not_sent_back(
    login_env: dict[str, str],
) -> None:
    # A limit of a few microseconds is passed before the first step. Goal mode is on and the plan has open steps, yet
    # neither the goal check nor the plan reminder sends the run back: the script has no turn for either.
    answer: str = "No leads yet: the limit came first. Missing: all 20; go on by sending a message here."
    played: Played = await _play(
        _config(1e-7, 0),
        login_env,
        True,
        [_turn(_plan(["in_progress", "pending"], TARGETS), _call(FINISH_TOOL, {"answer": answer, "success": False}))],
    )
    assert (played.record.status, played.record.result) == ("failed", answer), played.record
    assert classify_ending(played.record.status, played.record.result) == "failed"
    assert len(played.steps) == 1
    assert _offered(played.steps[0]) == LIMIT_TOOLS
    assert played.steps[0].messages[-1].content.startswith("[system note] The limit of this run is reached")
    assert [limit["max_minutes"] for limit in played.data(LIMIT_EVENT)] == [1e-7]
    # The plan tools still run in the step past the limit, so the plan can be closed with the finish.
    assert [plan["targets"] for plan in played.data("plan")] == [TARGETS]
    assert played.data("plan_reminder") == ()
    assert played.data("goal_check") == ()
    assert played.checks == ()
