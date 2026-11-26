"""How a run on OpenCode ends (item 552), on a real store and bus with no OpenCode server: the run's limits
(``run_max_minutes`` and ``run_max_tokens``) bind its agent as they bind Dot's own loop, and a reply that stopped at
the model's output limit ends the run failed, not done with half an answer."""

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import pytest_asyncio

from dotagent.agent.limits import LIMIT_EVENT, RunLimits
from dotagent.agent.loop import AgentRun, RunOutcome
from dotagent.core.errors import OpenCodeError
from dotagent.events import EventBus
from dotagent.opencode.events import PartSeen, Piece, StepEnded, StepStarted, ToolSeen
from dotagent.opencode.run import _Follow, _outcome, _Step
from dotagent.opencode.server import OpenCodeServer, PromptFile
from dotagent.store.db import Store
from dotagent.store.endings import PREFIX_CUT_SHORT, PREFIX_LIMIT, classify_ending
from dotagent.store.models import EventRecord, RunRecord, ThreadRecord
from dotagent.tools.meta import FinishArgs

GOAL: str = "Collect the figures of the quarter."


@dataclass
class _Server:
    """What the run asks of OpenCode's server: the prompts it sends the agent."""

    prompts: list[str] = field(default_factory=list)

    async def prompt(
        self,
        session_id: str,
        directory: Path,
        model: str,
        variant: str | None,
        text: str,
        files: tuple[PromptFile, ...],
        system: str,
    ) -> None:
        self.prompts.append(text)


@dataclass(frozen=True)
class Room:
    store: Store
    bus: EventBus
    run: RunRecord


@pytest_asyncio.fixture
async def room(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Room]:
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("opencode", "test")
        run: RunRecord = await store.create_run(
            thread.id, GOAL, "task", "opencode:big-pickle", None, "test", "medium", "standard", None, "yolo", False,
            None, None, None,
        )  # fmt: skip
        yield Room(store=store, bus=EventBus(store), run=run)
    finally:
        await store.close()


def _follow(room: Room, server: _Server, limits: RunLimits | None, worked_s: float, tmp_path: Path) -> _Follow:
    """The following of a run that began ``worked_s`` seconds ago, with ``limits``; ``m1`` is the agent's message."""
    agent: AgentRun = cast(
        AgentRun, SimpleNamespace(run=room.run, runtime=SimpleNamespace(store=room.store, bus=room.bus))
    )
    follow: _Follow = _Follow(
        agent=agent,
        server=cast(OpenCodeServer, server),
        session_id="ses_1",
        directory=tmp_path,
        model="big-pickle",
        variant=None,
        vision=False,
        queue=asyncio.Queue(),
        door="dot_1a2b3c4d_",
        silence_s=600.0,
        started=time.monotonic() - worked_s,
        limits=limits,
    )
    follow.assistants["m1"] = _Step(started=time.monotonic())
    return follow


def _step(reason: str, tokens: int = 100) -> StepEnded:
    return StepEnded(message_id="m1", input_tokens=tokens, output_tokens=0, cached_tokens=0, reason=reason)


async def test_an_agent_that_goes_on_past_the_time_limit_is_told_once_and_then_the_run_is_over(
    room: Room, tmp_path: Path
) -> None:
    server: _Server = _Server()
    follow: _Follow = _follow(room, server, RunLimits(max_minutes=30, max_tokens=0), 3600.0, tmp_path)
    # The first step that ends past the limit tells the agent to stop and answer.
    assert not await follow.seen(_step("tool-calls"))
    assert follow.told is not None and follow.over is None
    (note,) = server.prompts
    assert "limit of this run is reached" in note and "60.0 of 30 minutes" in note
    stored: list[EventRecord] = await room.store.events_of_kind(room.run.id, LIMIT_EVENT, 10)
    assert [event.data["max_minutes"] for event in stored] == [30.0]
    # The step under way when the note arrived has not read it; the one after has, and the agent still goes on.
    assert not await follow.seen(_step("tool-calls"))
    assert follow.over is None
    assert await follow.seen(_step("tool-calls"))
    assert follow.over is not None and server.prompts == [note]
    ended: RunOutcome = _outcome(follow, "Most of the figures are in.", GOAL)
    assert ended.status == "failed" and ended.result.startswith(PREFIX_LIMIT)
    assert "Most of the figures are in." in ended.result
    assert classify_ending(ended.status, ended.result) == "limit"


async def test_an_agent_that_answers_when_it_is_told_ends_the_run_done(room: Room, tmp_path: Path) -> None:
    server: _Server = _Server()
    follow: _Follow = _follow(room, server, RunLimits(max_minutes=30, max_tokens=0), 3600.0, tmp_path)
    assert not await follow.seen(_step("tool-calls"))
    assert len(server.prompts) == 1
    # Its next step ends with an answer, not with a call: the agent is done, and the run says so.
    assert not await follow.seen(_step("stop"))
    assert follow.over is None
    follow.answer = "The figures: 1, 2, 3."
    assert _outcome(follow, follow.answer, GOAL) == RunOutcome(status="done", result=follow.answer, needs="")


async def test_a_limit_of_tokens_counts_the_steps_of_the_run(room: Room, tmp_path: Path) -> None:
    server: _Server = _Server()
    follow: _Follow = _follow(room, server, RunLimits(max_minutes=0, max_tokens=150), 1.0, tmp_path)
    assert not await follow.seen(_step("tool-calls", 100))
    assert follow.told is None and server.prompts == []
    assert not await follow.seen(_step("tool-calls", 100))
    assert follow.told is not None and follow.told.tokens == 200 and len(server.prompts) == 1


async def test_a_run_inside_its_limits_or_without_any_is_not_told_anything(room: Room, tmp_path: Path) -> None:
    server: _Server = _Server()
    inside: _Follow = _follow(room, server, RunLimits(max_minutes=30, max_tokens=1_000_000), 60.0, tmp_path)
    for _ in range(4):
        assert not await inside.seen(_step("tool-calls"))
    unbounded: _Follow = _follow(room, server, None, 100_000.0, tmp_path)
    for _ in range(4):
        assert not await unbounded.seen(_step("tool-calls"))
    assert (inside.told, unbounded.told, server.prompts) == (None, None, [])


@pytest.mark.parametrize("reason", ["length", "other"])
async def test_a_reply_cut_short_ends_the_run_failed_with_what_cut_it(room: Room, tmp_path: Path, reason: str) -> None:
    follow: _Follow = _follow(room, _Server(), None, 1.0, tmp_path)
    assert not await follow.seen(_step(reason))
    ended: RunOutcome = _outcome(follow, "The first half of the answer and", GOAL)
    assert ended.status == "failed" and ended.result.startswith(PREFIX_CUT_SHORT)
    assert f"ended with '{reason}'" in ended.result
    assert classify_ending(ended.status, ended.result) == "cut_short"


async def test_a_run_that_stopped_of_its_own_accord_is_done_with_its_answer_or_a_line_that_says_it_gave_none(
    room: Room, tmp_path: Path
) -> None:
    follow: _Follow = _follow(room, _Server(), None, 1.0, tmp_path)
    assert not await follow.seen(_step("stop"))
    assert _outcome(follow, "All done.", GOAL) == RunOutcome(status="done", result="All done.", needs="")
    empty: RunOutcome = _outcome(follow, "", GOAL)
    assert (empty.status, empty.result) == ("done", "OpenCode's agent finished without a closing message.")


async def test_a_step_s_streamed_words_and_their_held_tail_come_under_the_step_that_wrote_them(
    room: Room, tmp_path: Path
) -> None:
    """Item 660: the stream holds back the word being written until the step ends (item 551), and the end of a step
    let it go after counting the step, so the tail came under the next step's number: the page opened a second live
    message with it, the first never took the step's ``assistant`` and kept its caret, and the answer came under both.
    A one-line answer is the case the owner met on a Zen free model."""
    follow: _Follow = _follow(room, _Server(), None, 0.0, tmp_path)
    shown: list[tuple[str, dict[str, object]]] = []

    async def emit(kind: str, data: dict[str, object], persisted: bool) -> None:
        shown.append((kind, data))

    follow.emit = emit  # type: ignore[method-assign]
    words: str = "Merhaba! Size nasıl yardımcı olabilirim?"
    await follow.seen(StepStarted(message_id="m1"))
    await follow.seen(PartSeen(part_id="p1", message_id="m1", kind="text", text=""))
    await follow.seen(Piece(part_id="p1", message_id="m1", text=words))
    await follow.seen(_step("stop"))
    deltas: list[dict[str, object]] = [data for kind, data in shown if kind == "delta"]
    (said,) = [data for kind, data in shown if kind == "assistant"]
    assert deltas and {data["step"] for data in deltas} == {said["step"]} == {1}, shown
    assert "".join(str(data["text"]) for data in deltas) == words == said["text"], shown
    # A task's words on OpenCode may be its answer, which is its last reply: they stream as either (item 1166).
    assert all(data["says"] == "either" for data in deltas), shown


def _seen(call_id: str, message_id: str, tool: str) -> ToolSeen:
    return ToolSeen(
        call_id=call_id,
        message_id=message_id,
        tool=tool,
        status="running",
        input={"command": "echo more"},
        output="",
        error="",
        title="",
        started_ms=None,
        ended_ms=None,
    )


async def test_a_call_of_opencodes_own_tool_after_a_finish_drops_it(room: Room, tmp_path: Path) -> None:
    """Item 1695's pre-push review: a finish ends the run as it says only while the agent makes no call after it; its
    own tools (bash, write) do not go through Dot's door, and a call of one drops the finish too."""
    follow: _Follow = _follow(room, _Server(), None, 0.0, tmp_path)
    follow.finished_calls.append(FinishArgs(answer="All done.", success=True))
    # A call of the finish's own step ran beside it, not after it: the finish stands (the third review of item 1695).
    follow.finish_step = "m0"
    follow.assistants["m0"] = _Step(started=time.monotonic())
    await follow.tool(_seen("c0", "m0", "bash"))
    assert follow.finished_calls == [FinishArgs(answer="All done.", success=True)]
    await follow.tool(
        ToolSeen(
            call_id="c1",
            message_id="m1",
            tool="bash",
            status="running",
            input={"command": "echo more"},
            output="",
            error="",
            title="",
            started_ms=None,
            ended_ms=None,
        )
    )
    assert follow.finished_calls == []


def _running(call_id: str, tool: str) -> ToolSeen:
    return ToolSeen(
        call_id=call_id, message_id="m1", tool=tool, status="running", input={}, output="", error="", title="",
        started_ms=None, ended_ms=None,
    )  # fmt: skip


async def test_a_session_that_says_nothing_fails_the_run_unless_a_tool_works_for_it(room: Room, tmp_path: Path) -> None:
    """Item 1940: a session that hangs mid-stream no longer leaves its run working forever."""
    follow: _Follow = _follow(room, _Server(), None, 0.0, tmp_path)
    follow.silence_s = 0.2
    with pytest.raises(OpenCodeError) as raised:
        async with asyncio.timeout(5.0):
            await follow.follow()
    assert "said nothing for" in raised.value.message and raised.value.context["session"] == "ses_1"
    # A command at work (OpenCode's own or one of Dot's tools) is no silence, however long it takes.
    working: _Follow = _follow(room, _Server(), None, 0.0, tmp_path)
    working.silence_s = 0.2
    await working.tool(_running("c1", "dot_1a2b3c4d_read_file"))
    assert working.running == {"c1"} and working.wake_in(time.monotonic()) is None
    working.heard -= 10.0
    assert not await working.quiet()
    # Its end starts the silence again.
    await working.tool(_running("c1", "dot_1a2b3c4d_read_file").model_copy(update={"status": "completed"}))
    assert working.running == set()
    with pytest.raises(OpenCodeError):
        await working.quiet()


async def test_the_time_limit_is_kept_by_the_clock_when_no_step_ends(
    room: Room, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1940: past ``run_max_minutes`` with no step ending (a session that hangs), the agent is told once, and the
    run is over a grace later, as it is when the agent goes on regardless."""
    monkeypatch.setattr("dotagent.opencode.run._LIMIT_GRACE_S", 0.2)
    server: _Server = _Server()
    follow: _Follow = _follow(room, server, RunLimits(max_minutes=30, max_tokens=0), 3600.0, tmp_path)
    follow.answer = "Half of the figures."
    async with asyncio.timeout(5.0):
        assert await follow.follow() == "Half of the figures."
    (note,) = server.prompts
    assert "limit of this run is reached" in note
    assert follow.over is not None
    ended: RunOutcome = _outcome(follow, follow.answer, GOAL)
    assert ended.status == "failed" and classify_ending(ended.status, ended.result) == "limit"
