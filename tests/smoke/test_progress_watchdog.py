"""The progress watchdog in the real loop: a scripted model that gets nowhere is nudged, limited to planning, given the
advice of the review model and stopped with what it tried; one that recovers or keeps working is left alone.

No network and no model service is used: the scripted model plays the run and answers for the review model too.
"""

import asyncio
from collections.abc import Sequence

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.review import CHECKPOINT_TOOL, PROGRESS_TOOL
from dotagent.config import AppConfig, load_default_config
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home, seed_workspace
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import FileSeed, ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.endings import PREFIX_STALLED, classify_ending
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL, MARK_PLAN_STEP_TOOL, UPDATE_PLAN_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Summarize notes.txt into summary.md."
NOTES: FileSeed = FileSeed(path="notes.txt", content="Ship the report on Friday.\n", encoding="text")
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
    """A run that is over: its record, its stall events, and the requests of the run and of the review model."""

    record: RunRecord
    stalls: tuple[JsonObject, ...]
    steps: tuple[ChatRequest, ...]
    consults: tuple[ChatRequest, ...]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config(escalation: bool, checkpoint_steps: int = 0) -> AppConfig:
    """The configuration of an eval run, with the scripted model as the review model too, and checkpoints every
    ``checkpoint_steps`` acting steps (item 909; none by default here, so the other tests' scripts stay as they are)."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(
                update={"stall_escalation": escalation, "checkpoint_steps": checkpoint_steps}
            ),
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
        }
    )


async def _play(config: AppConfig, login_env: dict[str, str], turns: Sequence[ScriptedTurn]) -> Played:
    """``GOAL`` as an unattended run of a model that plays ``turns``, in a throw-away home."""
    model: RecordingProvider = RecordingProvider(ScriptedProvider(_PROVIDER, turns))
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, model)
            await asyncio.to_thread(seed_workspace, runtime.workspace, [NOTES])
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
        stalls=tuple(event.data for event in events if event.kind == "stall"),
        steps=tuple(request for request in model.requests if request.session_key == record.thread_id),
        consults=tuple(request for request in model.requests if request.session_key != record.thread_id),
    )


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _write(path: str, content: str) -> ScriptedCall:
    return ScriptedCall(tool="write_file", arguments={"path": path, "content": content})


READ: ScriptedCall = ScriptedCall(tool="read_file", arguments={"path": NOTES.path})
DONE: ScriptedTurn = _turn(
    ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "summary.md is written.", "success": True})
)


def _offered(request: ChatRequest) -> frozenset[str]:
    return frozenset(spec.name for spec in request.tools)


def _said(request: ChatRequest) -> str:
    return "\n".join(message.content for message in request.messages)


async def test_a_run_that_repeats_itself_is_nudged_once_and_left_alone_when_it_changes_course(
    login_env: dict[str, str],
) -> None:
    played: Played = await _play(
        _config(True), login_env, [*[_turn(READ)] * 4, _turn(_write("summary.md", "- Friday: ship the report\n")), DONE]
    )
    assert played.record.status == "done"
    assert [(stall["action"], stall["level"], stall["reason"]) for stall in played.stalls] == [("nudge", 1, "repeat")]
    assert "[system note] Progress check: The same step ran 4 times in a row" in _said(played.steps[4])
    assert "read_file" in _offered(played.steps[4])
    assert played.consults == ()


async def test_a_run_that_gets_nowhere_climbs_the_ladder_and_is_stopped_with_what_it_tried(
    login_env: dict[str, str],
) -> None:
    plan: ScriptedCall = ScriptedCall(
        tool=UPDATE_PLAN_TOOL,
        arguments={
            "steps": [
                {"text": "Read notes.txt", "status": "done", "evidence": "read_file notes.txt: 2 lines"},
                {"text": "Write summary.md", "status": "pending"},
            ]
        },
    )
    advice: ScriptedTurn = _turn(
        ScriptedCall(
            tool=PROGRESS_TOOL,
            arguments={
                "diagnosis": "It reads notes.txt again and again although it already has the text.",
                "next_steps": ["Write summary.md from the text of notes.txt now."],
            },
        )
    )
    # The step after the re-plan also tries to read again, which it is no longer allowed to do.
    turns: list[ScriptedTurn] = [*[_turn(READ)] * 8, _turn(plan, READ), *[_turn(READ)] * 4, advice, *[_turn(READ)] * 4]
    played: Played = await _play(_config(True), login_env, turns)

    assert [(stall["action"], stall["level"]) for stall in played.stalls] == [
        ("nudge", 1),
        ("replan", 2),
        ("consult", 3),
        ("stop", 4),
    ]
    assert len(played.steps) == 17
    assert _offered(played.steps[8]) == {UPDATE_PLAN_TOOL, MARK_PLAN_STEP_TOOL, FINISH_TOOL}
    assert all("read_file" in _offered(step) for index, step in enumerate(played.steps) if index != 8)
    refused: list[str] = [message.content for message in played.steps[9].messages if message.tool_call_id == "call_9_1"]
    assert len(refused) == 1
    assert refused[0].startswith("Unknown tool 'read_file'")

    (consult,) = played.consults
    assert _offered(consult) == {PROGRESS_TOOL}
    assert "Write summary.md" in _said(consult)
    assert "The same step ran 4 times in a row" in _said(consult)
    assert played.stalls[2]["diagnosis"] == "It reads notes.txt again and again although it already has the text."
    assert played.stalls[2]["next_steps"] == ["Write summary.md from the text of notes.txt now."]
    assert "1. Write summary.md from the text of notes.txt now." in _said(played.steps[13])

    result: str = played.record.result
    assert played.record.status == "failed"
    assert result.startswith(f"{PREFIX_STALLED}. The same step ran 4 times in a row")
    assert classify_ending(played.record.status, result) == "stalled"
    assert "What it did last:\n- read_file" in result
    assert "Its plan:" in result
    assert "Write summary.md" in result


async def test_a_long_run_that_keeps_working_is_never_stopped(login_env: dict[str, str]) -> None:
    # Fifteen drafts of one file, each followed by a look at the notes, which answer the same every time.
    work: list[ScriptedTurn] = [
        turn for draft in range(15) for turn in (_turn(_write("summary.md", f"draft {draft}\n")), _turn(READ))
    ]
    played: Played = await _play(_config(True), login_env, [*work, DONE])
    assert played.record.status == "done"
    assert played.stalls == ()
    assert len(played.steps) == 31


async def test_without_escalation_a_run_that_repeats_itself_is_nudged_twice_and_stopped(
    login_env: dict[str, str],
) -> None:
    played: Played = await _play(_config(False), login_env, [_turn(READ)] * 12)
    assert [(stall["action"], stall["level"]) for stall in played.stalls] == [("nudge", 1), ("nudge", 2), ("stop", 3)]
    assert played.record.status == "failed"
    assert classify_ending(played.record.status, played.record.result) == "stalled"
    assert all("read_file" in _offered(step) for step in played.steps)
    assert played.consults == ()


def _verdict(on_track: bool, steps: Sequence[str] = ()) -> ScriptedTurn:
    why: str = (
        "It drafts summary.md and reads the notes, as the goal asks."
        if on_track
        else ("It keeps rewriting summary.md with drafts that drop what notes.txt says.")
    )
    return _turn(
        ScriptedCall(
            tool=CHECKPOINT_TOOL, arguments={"on_track": on_track, "diagnosis": why, "next_steps": list(steps)}
        )
    )


async def test_a_checkpoint_of_a_long_run_gives_the_steps_back_when_it_drifts_and_nothing_when_it_is_on_track(
    login_env: dict[str, str],
) -> None:
    """Item 909: a run that changes its steps shows no pattern; every few acting steps the review model is asked
    whether it is on track. Off track, the run is given its steps back and the ladder climbs; on track, nothing."""
    drafts: list[ScriptedTurn] = [_turn(_write("summary.md", f"draft {number}\n")) for number in range(6)]
    back: str = "Write summary.md from what notes.txt says: the report ships on Friday."
    turns: list[ScriptedTurn] = [*drafts[:3], _verdict(False, [back]), *drafts[3:], _verdict(True), DONE]
    played: Played = await _play(_config(False, checkpoint_steps=3), login_env, turns)
    assert played.record.status == "done", played.record.result
    assert len(played.consults) == 2 and all(_offered(ask) == {CHECKPOINT_TOOL} for ask in played.consults)
    assert "draft 2" in _said(played.consults[0])
    # The owner's words stand apart from the digest, where a line that says it is the user's may come of a page.
    assert "<<OWNER'S WORDS" in _said(played.consults[0]) and GOAL in _said(played.consults[0])
    assert [(stall["action"], stall["reason"]) for stall in played.stalls] == [("nudge", "drift")]
    assert played.stalls[0]["next_steps"] == [back]
    # The step after the checkpoint carries the judge's steps back; the one after the second, on track, nothing new.
    assert f"1. {back}" in _said(played.steps[3])
    assert _said(played.steps[6]).count(f"1. {back}") == 1


async def test_a_run_judged_off_track_at_every_checkpoint_is_stopped_with_what_it_tried(
    login_env: dict[str, str],
) -> None:
    drafts: list[ScriptedTurn] = [_turn(_write("summary.md", f"draft {number}\n")) for number in range(9)]
    off: ScriptedTurn = _verdict(False, ["Read notes.txt and write its one line into summary.md."])
    turns: list[ScriptedTurn] = [*drafts[:2], off, *drafts[2:4], off, *drafts[4:6], off, *drafts[6:], DONE]
    played: Played = await _play(_config(False, checkpoint_steps=2), login_env, turns)
    assert [stall["action"] for stall in played.stalls] == ["nudge", "nudge", "stop"]
    assert played.record.status == "failed"
    assert classify_ending(played.record.status, played.record.result) == "stalled"
    assert "review model judged that the steps are not getting closer" in played.record.result
    # The stop says the judge's last diagnosis, in the result and on the timeline.
    assert "drop what notes.txt says" in played.record.result
    assert played.stalls[-1]["diagnosis"].startswith("It keeps rewriting summary.md")
