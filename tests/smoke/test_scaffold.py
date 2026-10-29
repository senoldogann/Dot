"""The scaffold in the real loop: a model with a small window gets a context budget and tool output cut to fit it and
compacts where a model with a large window does not; a context size chosen for the model sets the budget, the tool
output and the point of summary of its next run (item 250); a model of the weak tier is reminded of its goal every few
steps; a window too small for the fixed part of the prompt refuses the run before the model is asked anything.

No network and no model service is used: the scripted model plays the run, reports the window each case needs, writes
the summaries of a compaction, and reports as the size of each prompt the size the loop estimates for it.
"""

import asyncio
import re
import secrets
from collections.abc import Sequence
from itertools import pairwise

import pytest
import pytest_asyncio
from structlog.testing import capture_logs

from dotagent.agent.compact import estimate_tokens
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, Tier, load_default_config
from dotagent.core.errors import ProviderError, ProviderRateLimitError, ProviderTransportError
from dotagent.core.types import FrozenModel, JsonObject, JsonValue
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home, seed_workspace
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import FileSeed, ScriptedCall, ScriptedTurn
from dotagent.llm.calls import CALL_EVENT
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply, Usage
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.secrets import resolve_secret
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Find the line of server.log that says why the backup failed."
LOG: FileSeed = FileSeed(
    path="server.log",
    content="".join(
        f"03:{index // 60 % 60:02d}:{index % 60:02d} backup: chunk {index:05d} of 03000 copied to the archive, "
        "checksum ok\n"
        for index in range(1, 3001)
    ),
    encoding="text",
)
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_MAX_EVENTS: int = 10_000
_SUMMARY_KEY: str = ":summary"
_BROKEN_FAST: str = "broken"
# The window of the runs whose summaries are measured: three quarters of it hold the fixed part of the prompt with a
# little room, so that forty pages make the run summarize again and again. 24 000 held it with a few tokens to spare
# until every run was told that there is always a way (item 446), 26 000 until the browser read a page's data in one
# call and Dot kept a list of its own (items 714 and 715), 30 000 for recon (item 776), whose schema of 985 characters
# adds about 330 tokens to the fixed part, 31 000 for `todo_drop` (item 821), which took it 62 tokens over; the window
# is rounded up so that the summary still has room.
_SMALL_WINDOW: int = 31_000
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


class TellingFastProvider(WindowedProvider):
    """The scripted model next to a fast model whose summaries repeat a password and a key Dot holds (``held``)."""

    def __init__(self, script: ScriptedProvider, window: int | None, held: str) -> None:
        super().__init__(script, window)
        self._held: str = held

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        reply: ModelReply = await super().complete(request, sink)
        if not request.session_key.endswith(_SUMMARY_KEY):
            return reply
        told: str = f"PROBLEMS: the vault took password: hunter2 and the key {self._held}."
        return reply.model_copy(update={"text": _SUMMARY.replace("PROBLEMS: none.", told)})


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
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


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


class UnreadableFastProvider(WindowedProvider):
    """The scripted model next to a fast model, ``broken``, that its server can tell nothing of: an Ollama model that is
    not pulled, or a server that is not running. The run's own model is read as it was."""

    async def model_info(self, model: str) -> ModelInfo:
        if model == _BROKEN_FAST:
            raise ProviderTransportError("connection refused", {"model": model})
        return await super().model_info(model)


class RateLimitedFastProvider(WindowedProvider):
    """The scripted model next to a fast model, ``broken``, whose provider answers every call with a rate limit; it
    counts the calls the fast model gets."""

    def __init__(self, script: ScriptedProvider, window: int | None) -> None:
        super().__init__(script, window)
        self.fast_calls: int = 0

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if request.model == _BROKEN_FAST:
            self.fast_calls += 1
            raise ProviderRateLimitError("the fast model is rate limited", 429, None, {"model": request.model})
        return await super().complete(request, sink)


class ForgetfulFastProvider(WindowedProvider):
    """The scripted model next to a fast model that leaves PROBLEMS out of a summary until asked for it by name."""

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if request.session_key.endswith(_SUMMARY_KEY) and "lacked the sections PROBLEMS" not in _said(request):
            reply: ModelReply = await super().complete(request, sink)
            return reply.model_copy(update={"text": _SUMMARY.replace("PROBLEMS: none.\n", "")})
        return await super().complete(request, sink)


class StubbornFastProvider(WindowedProvider):
    """The scripted model next to a fast model that leaves PROBLEMS out of every summary, asked for it or not."""

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        reply: ModelReply = await super().complete(request, sink)
        if not request.session_key.endswith(_SUMMARY_KEY):
            return reply
        return reply.model_copy(update={"text": _SUMMARY.replace("PROBLEMS: none.\n", "")})


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
                    GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "standard", None, "ask", False, ()
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


def _sized(context: dict[str, int]) -> AppConfig:
    """The configuration of these runs with ``context`` as the context sizes chosen for models (item 250)."""
    config: AppConfig = _config({}, 6)
    return config.model_copy(update={"models": config.models.model_copy(update={"context": context})})


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


def _scaffold(
    window: int | None, chosen: int | None, context_tokens: int, summarise_at: int, output_chars: int, cut: bool
) -> JsonObject:
    return {
        "model": SCRIPTED_MODEL,
        "window": window,
        "chosen": chosen,
        "context_tokens": context_tokens,
        "summarise_at": summarise_at,
        "tool_output_chars": output_chars,
        "cut_to_window": cut,
        "tier": "standard",
        "reason": '[agent] scaffold is "fixed"',
        "reminder_every": None,
    }


async def test_a_size_chosen_for_the_model_sets_the_budget_the_tool_output_and_the_point_of_summary(
    login_env: dict[str, str],
) -> None:
    # Above 96,000 the budget and the point of summary rise and the tool output stays at its most; below it all three
    # fall in proportion; past three quarters of the window the window cuts the size (item 250).
    turns: list[ScriptedTurn] = _reading(2)
    large: Played = await _play(_sized({SCRIPTED_MODEL: 192_000}), login_env, 1_000_000, turns)
    small: Played = await _play(_sized({SCRIPTED_MODEL: 64_000}), login_env, 1_000_000, turns)
    cut: Played = await _play(_sized({SCRIPTED_MODEL: 192_000}), login_env, 204_800, turns)
    other: Played = await _play(_sized({"anthropic:claude-opus-5-5": 192_000}), login_env, 1_000_000, turns)
    assert large.scaffolds == (_scaffold(1_000_000, 192_000, 192_000, 163_200, 12_000, False),)
    assert small.scaffolds == (_scaffold(1_000_000, 64_000, 64_000, 54_400, 8_000, False),)
    assert cut.scaffolds == (_scaffold(204_800, 192_000, 153_600, 153_600, 12_000, True),)
    # A size chosen for another model leaves this one's at Dot's default, the most of its window up to 200,000
    # (items 396 and 732).
    assert other.scaffolds == (_scaffold(1_000_000, None, 200_000, 170_000, 12_000, False),)
    assert {large.record.status, small.record.status, cut.record.status, other.record.status} == {"done"}


async def test_a_small_window_cuts_the_budget_and_the_tool_output_and_the_run_compacts_to_stay_inside(
    login_env: dict[str, str],
) -> None:
    # Forty pages fill the small budget even when the system prompt is all of the fixed part of the prompt. The window
    # leaves the fixed part of the prompt (the core tools of items 41 and 34, the page tool of item 193, the skill tool
    # with the skills that come with Dot of item 196 and the draft tool of item 314 included) room for three pages after
    # every compaction; what a compaction keeps grows a little each time, and a window that left room for one page ended
    # up compacting at every step once the instructions grew by a few words (item 404). It was 32 000 until three tools
    # joined the fixed part (items 714 and 715), 33 600 until three more did (items 740 and 746), and 35 200 until the
    # security scan and the Deep-OS skill did (item 781).
    turns: list[ScriptedTurn] = _reading(40)
    small: Played = await _play(_config({}, 6), login_env, 37_000, turns)
    roomy: Played = await _play(_config({}, 6), login_env, None, turns)

    assert small.scaffolds == (_scaffold(37_000, None, 27_750, 27_750, 3469, True),)
    assert roomy.scaffolds == (_scaffold(None, None, 96_000, 81_600, 12_000, False),)
    assert (small.record.status, roomy.record.status) == ("done", "done")

    small_pages: list[str] = _pages(small)
    roomy_pages: list[str] = _pages(roomy)
    assert len(small_pages) == len(roomy_pages) == 40
    # The tool sizes each page to the budget and says where the next one starts; nothing is cut out of a page later.
    for page in [*small_pages, *roomy_pages]:
        assert "chars omitted" not in page
        assert page.endswith("to continue]")
    assert max(len(page) for page in small_pages) <= 3300 < min(len(page) for page in roomy_pages)

    # The small budget is kept by compacting with summaries of the fast model; the large one is never reached.
    assert len(small.compactions) > 1
    assert all(compaction["method"] == "model" for compaction in small.compactions)
    fast: str = parse_model_ref(_config({}, 6).models.fast).label
    assert all((compaction["model"], compaction["fast_problem"]) == (fast, None) for compaction in small.compactions)
    assert len(small.summaries) == len(small.compactions) == len(small.compacted_at)
    # Each compaction tells the size of the prompt before it and after it (item 312): smaller after, inside the budget.
    for compaction in small.compactions:
        before, after = compaction["tokens_before"], compaction["tokens_after"]
        assert isinstance(before, int) and isinstance(after, int), compaction
        assert after < before and after <= 27_750, compaction
    assert max(estimate_tokens(step.messages, step.tools) for step in small.steps) <= 27_750
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
        broken, login_env, BrokenFastProvider(ScriptedProvider(_PROVIDER, _reading(40)), _SMALL_WINDOW)
    )
    assert played.record.status == "done"
    assert len(played.compactions) > 1
    own: str = parse_model_ref(SCRIPTED_MODEL).label
    for compaction in played.compactions:
        assert (compaction["method"], compaction["model"]) == ("model", own)
        assert compaction["fast_problem"] == "the fast model is down"


async def test_a_fast_model_whose_window_cannot_be_read_leaves_the_summary_to_the_runs_own_model(
    login_env: dict[str, str],
) -> None:
    """Item 552: the window of an Ollama model is read from its server, which may not be running; that is the fast
    model's problem, named as any other, and no reason to fail the run."""
    config: AppConfig = _config({}, 6)
    down: AppConfig = config.model_copy(
        update={"models": config.models.model_copy(update={"fast": f"{_PROVIDER}:{_BROKEN_FAST}"})}
    )
    played: Played = await _play_with(
        down, login_env, UnreadableFastProvider(ScriptedProvider(_PROVIDER, _reading(40)), _SMALL_WINDOW)
    )
    assert played.record.status == "done", played.record
    assert len(played.compactions) > 1
    own: str = parse_model_ref(SCRIPTED_MODEL).label
    for compaction in played.compactions:
        assert (compaction["method"], compaction["model"], compaction["fast_problem"]) == (
            "model",
            own,
            "connection refused",
        )


async def test_a_fast_model_paused_after_a_rate_limit_is_not_asked_again_and_the_runs_own_model_writes_at_once(
    login_env: dict[str, str],
) -> None:
    config: AppConfig = _config({}, 6)
    limited: AppConfig = config.model_copy(
        update={"models": config.models.model_copy(update={"fast": f"{_PROVIDER}:{_BROKEN_FAST}"})}
    )
    model: RateLimitedFastProvider = RateLimitedFastProvider(ScriptedProvider(_PROVIDER, _reading(40)), _SMALL_WINDOW)
    played: Played = await _play_with(limited, login_env, model)
    assert played.record.status == "done"
    assert len(played.compactions) > 1
    # The first compaction met the rate limit, which paused the fast model; the later ones did not ask it (item 240).
    assert model.fast_calls == 1
    own: str = parse_model_ref(SCRIPTED_MODEL).label
    first, *later = played.compactions
    assert (first["method"], first["model"], first["fast_problem"]) == ("model", own, "the fast model is rate limited")
    for compaction in later:
        assert (compaction["method"], compaction["model"]) == ("model", own)
        problem: JsonValue = compaction["fast_problem"]
        assert isinstance(problem, str)
        assert re.fullmatch(
            rf"{_PROVIDER}:{_BROKEN_FAST} \(rate limited until (\d{{4}}-\d\d-\d\d )?\d\d:\d\d\) cannot answer now",
            problem,
        ), problem


async def test_a_summary_that_lacks_a_section_is_asked_for_again_with_the_section_named(
    login_env: dict[str, str],
) -> None:
    played: Played = await _play_with(
        _config({}, 6), login_env, ForgetfulFastProvider(ScriptedProvider(_PROVIDER, _reading(40)), _SMALL_WINDOW)
    )
    assert played.record.status == "done"
    assert len(played.compactions) > 1
    for compaction in played.compactions:
        # The second ask, with the section named and the draft carried along, gave a summary that passes its check.
        assert (compaction["method"], compaction["attempts"]) == ("model", 2)
        assert compaction["problem"] == "the summary lacked the sections PROBLEMS"
        check = compaction["check"]
        assert isinstance(check, dict) and check["ok"] and check["missing_sections"] == []
    # Two summary requests per compaction: the second names the missing section and carries the first reply.
    assert len(played.summaries) == 2 * len(played.compactions)
    second: str = _said(played.summaries[1])
    assert "lacked the sections PROBLEMS" in second and "DECISIONS: read on to the end." in second


async def test_a_summary_that_still_lacks_a_section_keeps_its_heading_empty_and_says_so(
    login_env: dict[str, str],
) -> None:
    """Review O9: asked for once more and still without PROBLEMS, the summary goes into the note with an empty PROBLEMS
    heading; the check and the note name the section, and the log says it was left empty."""
    with capture_logs() as logs:
        played: Played = await _play_with(
            _config({}, 6), login_env, StubbornFastProvider(ScriptedProvider(_PROVIDER, _reading(40)), _SMALL_WINDOW)
        )
    assert played.record.status == "done"
    assert played.compactions
    for compaction in played.compactions:
        assert (compaction["method"], compaction["attempts"]) == ("model", 2)
        check = compaction["check"]
        assert isinstance(check, dict) and not check["ok"] and check["missing_sections"] == ["PROBLEMS"]
    after: str = _said(played.steps[-1])
    assert "NEXT: read the next page.\nPROBLEMS:\n" in after
    assert "The summary lacks the sections PROBLEMS" in after
    left_empty: list[object] = [entry["sections"] for entry in logs if entry["event"] == "summary_sections_left_empty"]
    assert left_empty == [["PROBLEMS"]] * len(played.compactions)


async def test_every_model_call_of_a_run_leaves_its_seconds_and_tokens_and_the_totals_hold_them_all(
    login_env: dict[str, str],
) -> None:
    played: Played = await _play(_config({}, 6), login_env, _SMALL_WINDOW, _reading(20))
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
    assert played.scaffolds == (_scaffold(4096, None, 3072, 3072, 2000, True),)
    assert played.record.status == "failed"
    assert "the model's window is too small for the system prompt and the tool definitions" in played.record.result
    assert played.steps == ()


async def test_a_summary_keeps_no_secret_dot_holds_nor_one_of_a_known_form(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Y3: the continuity note carries the summary into every later step and the run's record, so a secret Dot
    holds (one looked up in this process) and one of a known form are masked in it."""
    held: str = f"MARKER-{secrets.token_hex(8)}"
    monkeypatch.setenv("DOT_TEST_SUMMARY_KEY", held)
    assert resolve_secret("DOT_TEST_SUMMARY_KEY") == held
    played: Played = await _play_with(
        _config({}, 6), login_env, TellingFastProvider(ScriptedProvider(_PROVIDER, _reading(40)), _SMALL_WINDOW, held)
    )
    assert played.record.status == "done"
    assert played.compactions and all(compaction["method"] == "model" for compaction in played.compactions)
    told: list[str] = [_said(step) for step in played.steps]
    assert any("the vault took password: *** and the key ***." in said for said in told)
    assert not any(held in said or "hunter2" in said for said in told)
