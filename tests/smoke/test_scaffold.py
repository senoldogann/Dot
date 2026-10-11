"""The scaffold in the real loop: a model with a small window gets a context budget and tool output cut to fit it and
compacts where a model with a large window does not; a model of the weak tier is reminded of its goal every few steps;
a window too small for the fixed part of the prompt refuses the run before the model is asked anything.

No network and no model service is used: the scripted model plays the run, reports the window each case needs, writes
the summaries of a compaction, and reports as the size of each prompt the size the loop estimates for it.
"""

import asyncio
from collections.abc import Sequence
from itertools import pairwise

import pytest
import pytest_asyncio

from dotagent.agent.compact import estimate_tokens
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, Tier, load_default_config
from dotagent.core.errors import ProviderError
from dotagent.core.types import FrozenModel, JsonObject, JsonValue
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home, seed_workspace
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import FileSeed, ScriptedCall, ScriptedTurn
from dotagent.llm.calls import CALL_EVENT
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply, Usage
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Find the line of server.log that says why the backup failed."
LOG: FileSeed = FileSeed(
    path="server.log",
    content="".join(
        f"03:{index // 60 % 60:02d}:{index % 60:02d} backup: chunk {index:05d} of 03000 copied to the archive, checksum ok\n"
        for index in range(1, 3001)
    ),
)
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_MAX_EVENTS: int = 10_000
_SUMMARY_KEY: str = ":summary"
_BROKEN_FAST: str = "broken"
# Every section the continuity check asks for, and no identifier the run's history does not hold.
_SUMMARY: str = (
    "GOAL: find why the backup failed.\nDONE: read the log page by page.\nSTATE: no failure found so far.\n"
    "DECISIONS: read on to the end.\nPROBLEMS: none.\nNEXT: read the next page."
)


class WindowedProvider:
    """The scripted model with the window a case needs. It writes the summaries of a compaction itself, without using
    up the script, and reports the size the loop estimates for each prompt (the scripted model reports one size for
    every prompt, which would throw the loop's estimate off)."""

    def __init__(self, script: ScriptedProvider, window: int | None) -> None:
        self._script: ScriptedProvider = script
        self._window: int | None = window
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        size: int = estimate_tokens(request.messages, request.tools)
        if request.session_key.endswith(_SUMMARY_KEY):
            return ModelReply(
                model=request.model,
                text=_SUMMARY,
                thinking="",
                tool_calls=(),
                usage=Usage(input_tokens=size, output_tokens=60, cached_tokens=0),
                finish="stop",
                raw_finish="stop",
                raw=(),
            )
        reply: ModelReply = await self._script.complete(request, sink)
        return reply.model_copy(update={"usage": reply.usage.model_copy(update={"input_tokens": size})})

    async def model_info(self, model: str) -> ModelInfo:
        info: ModelInfo = await self._script.model_info(model)
        return info.model_copy(update={"context_length": self._window})

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


class Played(FrozenModel):
    """A run that is over: its record, its scaffold, compaction and reminder events, the steps its compactions came
    right before, the requests of its steps and of its summaries, and its model call, assistant and tool result
    events."""

    record: RunRecord
    scaffolds: tuple[JsonObject, ...]
    compactions: tuple[JsonObject, ...]
    compacted_at: tuple[JsonValue, ...]
    reminders: tuple[JsonObject, ...]
    steps: tuple[ChatRequest, ...]
    summaries: tuple[ChatRequest, ...]
    calls: tuple[JsonObject, ...]
    assistants: tuple[JsonObject, ...]
    results: tuple[JsonObject, ...]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s)


def _config(tiers: dict[str, Tier], reminder_every: int) -> AppConfig:
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={"agent": config.agent.model_copy(update={"tiers": tiers, "reminder_every": reminder_every})}
    )


def _kind(events: Sequence[EventRecord], kind: str) -> tuple[JsonObject, ...]:
    return tuple(event.data for event in events if event.kind == kind)


def _compacted_at(events: Sequence[EventRecord]) -> tuple[JsonValue, ...]:
    """The steps a compaction came right before: it folds the history before the step asks the model."""
    marks: list[EventRecord] = [event for event in events if event.kind in ("compacted", "assistant")]
    return tuple(
        later.data["step"]
        for earlier, later in pairwise(marks)
        if (earlier.kind, later.kind) == ("compacted", "assistant")
    )


class BrokenFastProvider(WindowedProvider):
    """The scripted model next to a fast model, ``broken``, whose provider answers every call with an error."""

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if request.model == _BROKEN_FAST:
            raise ProviderError("the fast model is down", {"model": request.model})
        return await super().complete(request, sink)


async def _play(
    config: AppConfig, login_env: dict[str, str], window: int | None, turns: Sequence[ScriptedTurn]
) -> Played:
    """``GOAL`` as an unattended run of a model with ``window`` that plays ``turns``, in a throw-away home."""
    return await _play_with(config, login_env, WindowedProvider(ScriptedProvider(_PROVIDER, turns), window))


async def _play_with(config: AppConfig, login_env: dict[str, str], model: WindowedProvider) -> Played:
    """``GOAL`` as an unattended run of ``model``, registered as the provider of the scripted model, in a throw-away
    home."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, model)
            await asyncio.to_thread(seed_workspace, runtime.workspace, [LOG])
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "ask", False, ()
                )
                async with asyncio.timeout(_TIME_LIMIT_S):
                    record: RunRecord = await manager.wait(run.id)
            finally:
                await manager.shutdown()
            events: list[EventRecord] = await runtime.store.list_events(run.id, 0, _MAX_EVENTS)
        finally:
            await close_runtime(runtime)
    return Played(
        record=record,
        scaffolds=_kind(events, "scaffold"),
        compactions=_kind(events, "compacted"),
        compacted_at=_compacted_at(events),
        reminders=_kind(events, "reminder"),
        steps=tuple(request for request in model.requests if request.session_key == record.thread_id),
        summaries=tuple(request for request in model.requests if request.session_key.endswith(_SUMMARY_KEY)),
        calls=_kind(events, CALL_EVENT),
        assistants=_kind(events, "assistant"),
        results=_kind(events, "tool_result"),
    )


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _read(offset: int) -> ScriptedCall:
    return ScriptedCall(tool="read_file", arguments={"path": LOG.path, "offset": offset})


DONE: ScriptedTurn = _turn(
    ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "No line of server.log says a backup failed.", "success": True})
)


def _reading(pages: int) -> list[ScriptedTurn]:
    """One read of the log per step, 60 lines further on each time; how many lines a page holds is up to the tool's
    output budget."""
    return [*(_turn(_read(1 + 60 * page)) for page in range(pages)), DONE]


def _pages(played: Played) -> list[str]:
    """Each page of the log as the model got it: the newest message of the step after the read."""
    return [step.messages[-1].content for step in played.steps if step.messages[-1].role == "tool"]


def _said(request: ChatRequest) -> str:
    return "\n".join(message.content for message in request.messages)


def _scaffold(window: int | None, context_tokens: int, output_chars: int, cut: bool) -> JsonObject:
    return {
        "model": SCRIPTED_MODEL,
        "window": window,
        "context_tokens": context_tokens,
        "tool_output_chars": output_chars,
        "cut_to_window": cut,
        "tier": "standard",
        "reason": '[agent] scaffold is "fixed"',
        "reminder_every": None,
    }


async def test_a_small_window_cuts_the_budget_and_the_tool_output_and_the_run_compacts_to_stay_inside(
    login_env: dict[str, str],
) -> None:
    # Forty pages fill the small budget even when the system prompt is all of the fixed part of the prompt. The window
    # leaves the fixed part of the prompt (the core tools of items 41 and 34 included) room for two pages after every
    # compaction; what a compaction keeps grows a little each time, so a smaller window would end up compacting at
    # every step.
    turns: list[ScriptedTurn] = _reading(40)
    small: Played = await _play(_config({}, 6), login_env, 26_000, turns)
    roomy: Played = await _play(_config({}, 6), login_env, None, turns)

    assert small.scaffolds == (_scaffold(26_000, 19_500, 2438, True),)
    assert roomy.scaffolds == (_scaffold(None, 96_000, 12_000, False),)
    assert (small.record.status, roomy.record.status) == ("done", "done")

    small_pages: list[str] = _pages(small)
    roomy_pages: list[str] = _pages(roomy)
    assert len(small_pages) == len(roomy_pages) == 40
    # The tool sizes each page to the budget and says where the next one starts; nothing is cut out of a page later.
    for page in [*small_pages, *roomy_pages]:
        assert "chars omitted" not in page
        assert page.endswith("to continue]")
    assert max(len(page) for page in small_pages) <= 2438 < min(len(page) for page in roomy_pages)

    # The small budget is kept by compacting with summaries of the fast model; the large one is never reached.
    assert len(small.compactions) > 1
    assert all(compaction["method"] == "model" for compaction in small.compactions)
    fast: str = parse_model_ref(_config({}, 6).models.fast).label
    assert all((compaction["model"], compaction["fast_problem"]) == (fast, None) for compaction in small.compactions)
    assert len(small.summaries) == len(small.compactions) == len(small.compacted_at)
    assert max(estimate_tokens(step.messages, step.tools) for step in small.steps) <= 19_500
    # Each compaction keeps only what fits the budget and leaves room for the steps after it, instead of folding the
    # history again at the very next step.
    gaps: list[int] = [
        later - earlier
        for earlier, later in pairwise(small.compacted_at)
        if isinstance(earlier, int) and isinstance(later, int)
    ]
    assert len(gaps) == len(small.compactions) - 1
    assert min(gaps) > 1
    assert (roomy.compactions, roomy.summaries) == ((), ())
    assert max(estimate_tokens(step.messages, step.tools) for step in roomy.steps) > 18_000


async def test_a_fast_model_that_fails_leaves_the_summary_to_the_runs_own_model_and_is_named(
    login_env: dict[str, str],
) -> None:
    config: AppConfig = _config({}, 6)
    broken: AppConfig = config.model_copy(
        update={"models": config.models.model_copy(update={"fast": f"{_PROVIDER}:{_BROKEN_FAST}"})}
    )
    played: Played = await _play_with(
        broken, login_env, BrokenFastProvider(ScriptedProvider(_PROVIDER, _reading(40)), 24_000)
    )
    assert played.record.status == "done"
    assert len(played.compactions) > 1
    own: str = parse_model_ref(SCRIPTED_MODEL).label
    for compaction in played.compactions:
        assert (compaction["method"], compaction["model"]) == ("model", own)
        assert compaction["fast_problem"] == "the fast model is down"


async def test_every_model_call_of_a_run_leaves_its_seconds_and_tokens_and_the_totals_hold_them_all(
    login_env: dict[str, str],
) -> None:
    played: Played = await _play(_config({}, 6), login_env, 24_000, _reading(20))
    assert played.record.status == "done"
    assert len(played.compactions) >= 1
    purposes: list[JsonValue] = [call["purpose"] for call in played.calls]
    # One model_call event per step and one per compaction, in the order they were made.
    assert purposes.count("step") == played.record.steps == len(played.steps) == len(played.assistants)
    assert purposes.count("compaction") == len(played.compactions) == len(played.summaries)
    assert set(purposes) == {"step", "compaction"}
    fast: str = parse_model_ref(_config({}, 6).models.fast).label
    for call in played.calls:
        # A step runs on the run's model; a compaction's summary is the fast model's side work.
        assert call["model"] == (fast if call["purpose"] == "compaction" else SCRIPTED_MODEL)
        assert call["error"] is None
        assert isinstance(call["seconds"], float) and call["seconds"] >= 0.0
    # The run's totals hold the tokens of the summaries too, not only those of its steps.
    assert played.record.input_tokens == sum(int(str(call["input_tokens"])) for call in played.calls)
    assert played.record.output_tokens == sum(int(str(call["output_tokens"])) for call in played.calls)
    step_tokens: int = sum(int(str(assistant["input_tokens"])) for assistant in played.assistants)
    assert played.record.input_tokens > step_tokens
    for assistant in played.assistants:
        assert isinstance(assistant["seconds"], float) and assistant["cached_tokens"] == 0
    # Reading a file in the workspace asks nobody: no call waited at the gate longer than its own checks take.
    assert played.results and all(float(str(result["waited_s"])) < 0.5 for result in played.results)


async def test_a_model_of_the_weak_tier_is_reminded_of_its_goal_every_few_steps(login_env: dict[str, str]) -> None:
    weak: Played = await _play(_config({SCRIPTED_MODEL: "weak"}, 3), login_env, None, _reading(7))
    assert weak.record.status == "done"
    assert (weak.scaffolds[0]["tier"], weak.scaffolds[0]["reason"], weak.scaffolds[0]["reminder_every"]) == (
        "weak",
        "set by hand in [agent] tiers",
        3,
    )
    assert [reminder["step"] for reminder in weak.reminders] == [3, 6]
    # The reminder follows the results of the step it is due at, so the next step reads it last.
    for step in (weak.steps[3], weak.steps[6]):
        note: str = step.messages[-1].content
        assert note.startswith("[system note] Reminder, nothing new is asked")
        assert GOAL in note
        assert "read_file" in note
    assert all("[system note] Reminder" not in _said(step) for step in weak.steps[:3])

    standard: Played = await _play(_config({}, 3), login_env, None, _reading(7))
    assert standard.record.status == "done"
    assert standard.reminders == ()
    assert all("[system note] Reminder" not in _said(step) for step in standard.steps)


async def test_a_window_too_small_for_the_fixed_part_of_the_prompt_refuses_the_run_before_the_model_is_asked(
    login_env: dict[str, str],
) -> None:
    # Three quarters of 4096 tokens cannot hold even the system prompt.
    played: Played = await _play(_config({}, 6), login_env, 4096, [DONE])
    assert played.scaffolds == (_scaffold(4096, 3072, 2000, True),)
    assert played.record.status == "failed"
    assert "the model's window is too small for the system prompt and the tool definitions" in played.record.result
    assert played.steps == ()
