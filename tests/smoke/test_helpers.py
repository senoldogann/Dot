"""Helper models in the real loop: a run asks a helper for advice and reads the answer fenced, up to its budget; a
helper whose quota is spent is paused and the run is told; a delegated run starts on a helper; a run whose model cannot
answer moves to its standby models, once each and in their order, with the tools of the step it was at, and finishes
there; without one that can take it over it fails with the error.

No network and no model service is used: scripted models play the run's own model (under ``ollama``) and the helper
(under ``anthropic``, with a stand-in key in the environment so that the provider counts as keyed).
"""

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.helpers import HELPER_UNAVAILABLE_EVENT, MODEL_SWITCHED_EVENT
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import ProviderQuotaError
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home, seed_workspace
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import FileSeed, ScriptedCall, ScriptedTurn
from dotagent.llm.calls import CALL_EVENT
from dotagent.llm.types import ChatProvider, ChatRequest, DeltaSink, Effort, ModelInfo, ModelReply, Usage
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.consult import CONSULT_TOOL
from dotagent.tools.delegate import DELEGATE_TOOL
from dotagent.tools.meta import FINISH_TOOL, MARK_PLAN_STEP_TOOL, UPDATE_PLAN_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Pick a file format for the export of the contacts."
HELPER: str = "anthropic:scripted-helper"
SPENT_MAIN: str = "anthropic:scripted-spent"
SPENT_OTHER: str = "anthropic:scripted-spent-too"
# A model of the spent provider that cannot call tools.
BLIND: str = "anthropic:scripted-blind"
NOTES: FileSeed = FileSeed(path="notes.txt", content="Export the contacts as CSV.\n", encoding="text")
QUESTION: str = "CSV or JSON for 40 contacts that a spreadsheet opens?"
_STAND_IN_KEY: str = "stand-in-key-of-the-test"
_TIME_LIMIT_S: float = 60.0
_MAX_EVENTS: int = 10_000


class RecordingProvider:
    """A scripted model, keeping every request it is sent."""

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


class SpendingProvider:
    """A provider whose quota runs out: its first ``answered`` calls are answered by ``script``, and every later one
    fails the way the connector reports a spent quota, after its retries. Its models offer three reasoning levels,
    and all of them but ``BLIND`` can call tools."""

    def __init__(self, script: ScriptedProvider, answered: int) -> None:
        self._script: ScriptedProvider = script
        self._answered: int = answered
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        if len(self.requests) <= self._answered:
            return await self._script.complete(request, sink)
        raise ProviderQuotaError("credit balance is too low", 400, None, {"model": request.model})

    async def model_info(self, model: str) -> ModelInfo:
        return ModelInfo(
            id=model,
            vision=False,
            tools=model != BLIND.partition(":")[2],
            thinking=False,
            context_length=128_000,
            own_context_length=None,
            window_source="assumed",
            efforts=("low", "medium", "high"),
            free=False,
        )

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return (await self.model_info("spent"),)

    async def aclose(self) -> None:
        return None


class FilteringProvider(SpendingProvider):
    """A provider whose content filter stops every reply at once, as OpenCode Go's DeepSeek did on a long chat
    (2026-10-20): no words, no calls, and the end 'content_filter' in the service's own words."""

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return ModelReply(
            model=request.model,
            text="",
            thinking="",
            tool_calls=(),
            usage=Usage(input_tokens=339_358, output_tokens=0, cached_tokens=0),
            finish="other",
            raw_finish="content_filter",
            raw=(),
        )


class Played(FrozenModel):
    """A run that is over, with its events."""

    record: RunRecord
    events: tuple[EventRecord, ...]

    def data(self, kind: str) -> tuple[JsonObject, ...]:
        return tuple(event.data for event in self.events if event.kind == kind)

    def results(self, tool: str) -> tuple[JsonObject, ...]:
        return tuple(data for data in self.data("tool_result") if data["tool"] == tool)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config(helpers: Sequence[str], standby: Sequence[str], consult_max_per_run: int) -> AppConfig:
    """An eval run's configuration with these helper and standby models; the scripted model reviews."""
    config: AppConfig = eval_config(load_default_config())
    models: JsonObject = {"review": SCRIPTED_MODEL, "helpers": list(helpers), "standby": list(standby)}
    return config.model_copy(
        update={
            "models": config.models.model_copy(update=models),
            "agent": config.agent.model_copy(update={"consult_max_per_run": consult_max_per_run}),
        }
    )


@asynccontextmanager
async def _session(
    config: AppConfig, login_env: dict[str, str], providers: Mapping[str, ChatProvider]
) -> AsyncIterator[tuple[Runtime, RunManager]]:
    """A runtime in a throw-away home whose providers are ``providers``, and its runs."""
    with scratch_home() as home, eval_environment(home, {config.providers.anthropic.api_key_env: _STAND_IN_KEY}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            for name, provider in providers.items():
                await runtime.router.register(name, provider)
            manager: RunManager = RunManager(runtime)
            try:
                yield runtime, manager
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


def _spent() -> SpendingProvider:
    """A provider whose quota is spent from its first call on."""
    return SpendingProvider(ScriptedProvider("anthropic", []), 0)


async def _play(runtime: Runtime, manager: RunManager, model: str, effort: Effort | None) -> Played:
    run: RunRecord = await manager.start(
        GOAL, "task", model, None, EVAL_SOURCE, None, effort, "standard", None, "ask", False, ()
    )
    async with asyncio.timeout(_TIME_LIMIT_S):
        record: RunRecord = await manager.wait(run.id)
    return Played(record=record, events=tuple(await runtime.store.list_events(record.id, 0, _MAX_EVENTS)))


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _consult(arguments: JsonObject) -> ScriptedTurn:
    return _turn(ScriptedCall(tool=CONSULT_TOOL, arguments=arguments))


def _finish(answer: str) -> ScriptedTurn:
    return _turn(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True}))


def _says(text: str) -> ScriptedTurn:
    return ScriptedTurn(text=text, calls=())


def _offered(request: ChatRequest) -> frozenset[str]:
    return frozenset(spec.name for spec in request.tools)


async def test_a_run_asks_its_helper_and_reads_the_answer_fenced_as_advice(login_env: dict[str, str]) -> None:
    main: RecordingProvider = RecordingProvider(
        ScriptedProvider("ollama", [_consult({"question": QUESTION}), _finish("CSV.")])
    )
    helper: RecordingProvider = RecordingProvider(
        ScriptedProvider("anthropic", [_says("CSV: a spreadsheet opens it.")])
    )
    # The run's own model leads the helpers: it is neither named nor asked.
    config: AppConfig = _config([SCRIPTED_MODEL, HELPER], [], 5)
    async with _session(config, login_env, {"ollama": main, "anthropic": helper}) as (runtime, runs):
        played: Played = await _play(runtime, runs, SCRIPTED_MODEL, None)
    assert played.record.status == "done", played.record
    assert CONSULT_TOOL in _offered(main.requests[0])
    assert (
        f"# Helpers\n- The user set up other models to help you: {HELPER}.\n- consult asks one of them"
        in main.requests[0].messages[0].content
    )
    [result] = played.results(CONSULT_TOOL)
    assert result["ok"] is True
    text: str = str(result["text"])
    assert text.startswith(f"Advice from {HELPER}: weigh it against what you see")
    assert (
        '<helper_advice nonce="' in text
        and f'" model="{HELPER}">\nCSV: a spreadsheet opens it.\n</helper_advice' in text
    )
    [asked] = helper.requests
    assert asked.tools == ()
    system, prompt = (message.content for message in asked.messages)
    assert system.startswith("You advise an AI agent that works on a task for its user")
    assert "<<GOAL (the user's words) " in prompt and f"\n{GOAL}\n<<end GOAL (the user's words) " in prompt
    assert "<<PLAN (the agent's) " in prompt and "The agent has not written a plan." in prompt
    assert "<<RECORD OF THE RUN (oldest first) " in prompt
    assert prompt.rstrip().endswith(">>") and f"\n{QUESTION}\n<<end QUESTION (the agent's) " in prompt
    calls: list[JsonObject] = [data for data in played.data(CALL_EVENT) if data["purpose"] == "consult"]
    assert [(call["model"], call["error"]) for call in calls] == [(HELPER, None)]


async def test_a_run_asks_its_helpers_no_more_often_than_its_budget(login_env: dict[str, str]) -> None:
    main: RecordingProvider = RecordingProvider(
        ScriptedProvider(
            "ollama",
            [_consult({"question": QUESTION, "include": []}), _consult({"question": QUESTION}), _finish("CSV.")],
        )
    )
    helper: RecordingProvider = RecordingProvider(ScriptedProvider("anthropic", [_says("CSV.")]))
    async with _session(_config([HELPER], [], 1), login_env, {"ollama": main, "anthropic": helper}) as (runtime, runs):
        played: Played = await _play(runtime, runs, SCRIPTED_MODEL, None)
    assert played.record.status == "done", played.record
    first, second = played.results(CONSULT_TOOL)
    assert first["ok"] is True
    assert (second["ok"], second["text"]) == (
        False,
        "This run has asked its helper models as many questions as it may (1). Go on yourself.",
    )
    assert len(helper.requests) == 1
    # A question that includes nothing sends only the question.
    assert "<<GOAL" not in helper.requests[0].messages[1].content


async def test_a_helper_whose_quota_is_spent_is_paused_and_the_run_is_told(login_env: dict[str, str]) -> None:
    main: RecordingProvider = RecordingProvider(
        ScriptedProvider(
            "ollama", [_consult({"question": QUESTION}), _consult({"question": QUESTION}), _finish("JSON, alone.")]
        )
    )
    spent: SpendingProvider = _spent()
    async with _session(_config([HELPER], [], 5), login_env, {"ollama": main, "anthropic": spent}) as (runtime, runs):
        played: Played = await _play(runtime, runs, SCRIPTED_MODEL, None)
    assert played.record.status == "done", played.record
    [unavailable] = played.data(HELPER_UNAVAILABLE_EVENT)
    assert (unavailable["model"], unavailable["reason"]) == (HELPER, "quota exhausted")
    assert isinstance(unavailable["until"], float) and unavailable["until"] > played.record.created_at
    first, second = played.results(CONSULT_TOOL)
    assert first["ok"] is False
    assert str(first["text"]).startswith(f"{HELPER} (quota exhausted until ")
    assert str(first["text"]).endswith(") cannot answer; go on yourself.")
    # The paused helper is not asked again: the second question gets the roster instead.
    assert second["ok"] is False
    assert str(second["text"]).startswith(
        f"No helper model can answer now (available: none; cannot answer now: {HELPER}"
    )
    assert len(spent.requests) == 1


async def test_a_delegated_run_starts_on_the_helper_the_call_names(login_env: dict[str, str]) -> None:
    main: RecordingProvider = RecordingProvider(
        ScriptedProvider(
            "ollama",
            [
                _turn(ScriptedCall(tool=DELEGATE_TOOL, arguments={"goal": "Say which format.", "model": HELPER})),
                _finish("CSV, as the helper said."),
            ],
        )
    )
    helper: RecordingProvider = RecordingProvider(ScriptedProvider("anthropic", [_finish("CSV.")]))
    config: AppConfig = _config([HELPER, SCRIPTED_MODEL], [], 5)
    async with _session(config, login_env, {"ollama": main, "anthropic": helper}) as (runtime, runs):
        played: Played = await _play(runtime, runs, SCRIPTED_MODEL, None)
        [child] = [run for run in await runtime.store.list_runs(20, None) if run.parent_run_id == played.record.id]
    assert played.record.status == "done", played.record
    assert (child.model, child.status, child.result) == (HELPER, "done", "CSV.")
    # A delegated run asks no helper, although one besides its own model could answer: its parent does.
    [asked] = helper.requests
    assert CONSULT_TOOL not in _offered(asked) and "# Helpers" not in asked.messages[0].content
    [result] = played.results(DELEGATE_TOOL)
    assert result["ok"] is True and result["summary"] == f"Delegate to {HELPER}: Say which format."
    assert str(result["text"]).startswith(f"Delegated run {child.id} on {HELPER} ended as done after 1 steps.")


async def test_without_consult_the_instructions_name_the_helpers_for_delegate(login_env: dict[str, str]) -> None:
    main: RecordingProvider = RecordingProvider(ScriptedProvider("ollama", [_finish("CSV.")]))
    async with _session(_config([HELPER], [], 0), login_env, {"ollama": main}) as (runtime, runs):
        played: Played = await _play(runtime, runs, SCRIPTED_MODEL, None)
    assert played.record.status == "done", played.record
    assert CONSULT_TOOL not in _offered(main.requests[0])
    assert (
        f"# Helpers\n- The user set up other models to help you: {HELPER}.\n- delegate runs a part of the work on one "
        "of them when you name it with model.\n- A helper whose key is missing" in main.requests[0].messages[0].content
    )


async def test_a_run_does_not_take_its_own_model_for_a_helper(login_env: dict[str, str]) -> None:
    main: RecordingProvider = RecordingProvider(ScriptedProvider("ollama", [_finish("CSV.")]))
    async with _session(_config([SCRIPTED_MODEL], [], 5), login_env, {"ollama": main}) as (runtime, runs):
        played: Played = await _play(runtime, runs, SCRIPTED_MODEL, None)
    assert played.record.status == "done", played.record
    assert CONSULT_TOOL not in _offered(main.requests[0])
    assert "# Helpers" not in main.requests[0].messages[0].content


async def test_a_run_whose_provider_cannot_answer_moves_to_its_standby_model_and_finishes(
    login_env: dict[str, str],
) -> None:
    spent: SpendingProvider = _spent()
    standby: RecordingProvider = RecordingProvider(ScriptedProvider("ollama", [_finish("Done on the standby model.")]))
    config: AppConfig = _config([], [SCRIPTED_MODEL], 5)
    async with _session(config, login_env, {"anthropic": spent, "ollama": standby}) as (runtime, runs):
        played: Played = await _play(runtime, runs, SPENT_MAIN, None)
    assert (played.record.status, played.record.result) == ("done", "Done on the standby model."), played.record
    assert (played.record.model, played.record.steps) == (SCRIPTED_MODEL, 1)
    assert played.data(MODEL_SWITCHED_EVENT) == (
        {"from": SPENT_MAIN, "to": SCRIPTED_MODEL, "reason": "quota exhausted", "step": 1},
    )
    assert len(spent.requests) == 1
    [asked] = standby.requests
    assert asked.model == "scripted"
    assert CONSULT_TOOL not in _offered(asked)
    note: str = (
        f"[system note] From this step on you run on {SCRIPTED_MODEL}: {SPENT_MAIN} cannot answer (quota exhausted). "
        "Go on with the task from where it stands."
    )
    assert asked.messages[-1].content == note
    steps: list[JsonObject] = [data for data in played.data(CALL_EVENT) if data["purpose"] == "step"]
    assert [(call["model"], call["error"]) for call in steps] == [
        (SPENT_MAIN, "ProviderQuotaError"),
        (SCRIPTED_MODEL, None),
    ]


async def test_a_standby_model_chosen_while_the_daemon_runs_takes_the_next_run_over(login_env: dict[str, str]) -> None:
    """Item 478: Settings › Models chooses the standby models while the daemon runs, and the next run whose model cannot
    answer moves to the one chosen, with no restart, though the config the runtime was built with names none."""
    spent: SpendingProvider = _spent()
    standby: RecordingProvider = RecordingProvider(ScriptedProvider("ollama", [_finish("Done on the standby model.")]))
    async with _session(_config([], [], 5), login_env, {"anthropic": spent, "ollama": standby}) as (runtime, runs):
        runtime.roles.choose_standby([SCRIPTED_MODEL])
        played: Played = await _play(runtime, runs, SPENT_MAIN, None)
    assert (played.record.status, played.record.model) == ("done", SCRIPTED_MODEL), played.record
    assert played.data(MODEL_SWITCHED_EVENT) == (
        {"from": SPENT_MAIN, "to": SCRIPTED_MODEL, "reason": "quota exhausted", "step": 1},
    )


async def test_a_run_goes_through_its_standby_models_once_in_order_passing_over_one_that_cannot_take_it(
    login_env: dict[str, str],
) -> None:
    spent: SpendingProvider = _spent()
    standby: RecordingProvider = RecordingProvider(ScriptedProvider("ollama", [_finish("Done on the standby model.")]))
    config: AppConfig = _config([], [BLIND, SPENT_OTHER, SPENT_MAIN, SCRIPTED_MODEL], 5)
    # Every pause ends at once: the models the run left are still not taken again.
    config = config.model_copy(update={"models": config.models.model_copy(update={"helper_cooldown_s": 0.001})})
    async with _session(config, login_env, {"anthropic": spent, "ollama": standby}) as (runtime, runs):
        played: Played = await _play(runtime, runs, SPENT_MAIN, "high")
    assert (played.record.status, played.record.result) == ("done", "Done on the standby model."), played.record
    assert (played.record.model, played.record.steps) == (SCRIPTED_MODEL, 1)
    # The scripted model offers no reasoning levels, so the run's level follows it to none.
    assert played.record.effort is None
    assert played.data(MODEL_SWITCHED_EVENT) == (
        {"from": SPENT_MAIN, "to": SPENT_OTHER, "reason": "quota exhausted", "step": 1},
        {"from": SPENT_OTHER, "to": SCRIPTED_MODEL, "reason": "quota exhausted", "step": 1},
    )
    # The model that cannot call tools is never asked, and the run's first model is not asked again.
    assert [request.model for request in spent.requests] == ["scripted-spent", "scripted-spent-too"]
    assert [request.effort for request in spent.requests] == ["high", "high"]
    steps: list[JsonObject] = [data for data in played.data(CALL_EVENT) if data["purpose"] == "step"]
    assert [(call["model"], call["error"]) for call in steps] == [
        (SPENT_MAIN, "ProviderQuotaError"),
        (SPENT_OTHER, "ProviderQuotaError"),
        (SCRIPTED_MODEL, None),
    ]


async def test_a_run_that_no_standby_model_can_take_over_fails_saying_why(login_env: dict[str, str]) -> None:
    spent: SpendingProvider = _spent()
    async with _session(_config([], [BLIND], 5), login_env, {"anthropic": spent}) as (runtime, runs):
        played: Played = await _play(runtime, runs, SPENT_MAIN, None)
    assert played.record.status == "failed"
    assert played.record.result == (
        f"the run's model cannot answer (quota exhausted) and no standby model can take the run over: {BLIND} (it"
        " cannot call tools)"
    )
    assert played.data(MODEL_SWITCHED_EVENT) == ()
    assert [request.model for request in spent.requests] == ["scripted-spent"]


async def test_a_step_asked_again_on_the_standby_model_keeps_the_tools_a_progress_alert_allowed(
    login_env: dict[str, str],
) -> None:
    read: ScriptedTurn = _turn(ScriptedCall(tool="read_file", arguments={"path": NOTES.path}))
    # The same read eight times: a nudge after the fourth, a re-plan after the eighth, whose step may only plan or end;
    # the provider's quota runs out at that step.
    spending: SpendingProvider = SpendingProvider(ScriptedProvider("anthropic", [read] * 8), 8)
    standby: RecordingProvider = RecordingProvider(ScriptedProvider("ollama", [_finish("CSV.")]))
    config: AppConfig = _config([], [SCRIPTED_MODEL], 5)
    config = config.model_copy(update={"agent": config.agent.model_copy(update={"stall_escalation": True})})
    async with _session(config, login_env, {"anthropic": spending, "ollama": standby}) as (runtime, runs):
        await asyncio.to_thread(seed_workspace, runtime.workspace, [NOTES])
        played: Played = await _play(runtime, runs, SPENT_MAIN, None)
    assert played.record.status == "done", played.record
    assert [(stall["action"], stall["level"]) for stall in played.data("stall")] == [("nudge", 1), ("replan", 2)]
    assert _offered(spending.requests[8]) == {UPDATE_PLAN_TOOL, MARK_PLAN_STEP_TOOL, FINISH_TOOL}
    assert _offered(standby.requests[0]) == {UPDATE_PLAN_TOOL, MARK_PLAN_STEP_TOOL, FINISH_TOOL}


async def test_without_a_standby_model_the_run_fails_with_the_error_of_its_model(login_env: dict[str, str]) -> None:
    spent: SpendingProvider = _spent()
    async with _session(_config([], [], 5), login_env, {"anthropic": spent}) as (runtime, runs):
        played: Played = await _play(runtime, runs, SPENT_MAIN, None)
    assert played.record.status == "failed"
    assert played.record.result.startswith("credit balance is too low")
    assert played.data(MODEL_SWITCHED_EVENT) == ()
    assert played.record.model == SPENT_MAIN


async def test_a_reply_its_content_filter_stops_moves_the_run_to_its_standby_model(login_env: dict[str, str]) -> None:
    """Asked again with the same conversation the filter stops the model again, so the run is not nudged: it moves to
    its standby model at the first stop."""
    filtering: FilteringProvider = FilteringProvider(ScriptedProvider("anthropic", []), 0)
    standby: RecordingProvider = RecordingProvider(ScriptedProvider("ollama", [_finish("Done on the standby model.")]))
    async with _session(_config([], [SCRIPTED_MODEL], 5), login_env, {"anthropic": filtering, "ollama": standby}) as (
        runtime,
        runs,
    ):
        played: Played = await _play(runtime, runs, SPENT_MAIN, None)
    assert (played.record.status, played.record.result) == ("done", "Done on the standby model."), played.record
    assert played.data(MODEL_SWITCHED_EVENT) == (
        {"from": SPENT_MAIN, "to": SCRIPTED_MODEL, "reason": "its content filter stopped the reply", "step": 1},
    )
    assert len(filtering.requests) == 1


async def test_a_reply_its_content_filter_stops_ends_the_run_saying_so_when_no_standby_model_is_left(
    login_env: dict[str, str],
) -> None:
    filtering: FilteringProvider = FilteringProvider(ScriptedProvider("anthropic", []), 0)
    async with _session(_config([], [], 5), login_env, {"anthropic": filtering}) as (runtime, runs):
        played: Played = await _play(runtime, runs, SPENT_MAIN, None)
    assert played.record.status == "failed"
    assert played.record.result.startswith(
        "the model's service stopped its reply with its content filter ('content_filter')"
    ), played.record.result
    assert "choose another model" in played.record.result
    # One stop is enough to know: the same conversation is not sent again.
    assert len(filtering.requests) == 1
