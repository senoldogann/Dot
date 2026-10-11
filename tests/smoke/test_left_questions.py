"""Questions in the real loop with a scripted model: a yolo or scheduled run leaves its question and goes on, and the
answer carries the work on in the thread, into the run that works there or into a new one; a run the user watches waits
as before. And the end of a task run: only text replies in a row end it, so a status line between tool steps does not.

No network and no model service is used: the scripted model plays the runs.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.approvals import QUESTION_LEFT_EVENT
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import InvalidValueError, RunConflict
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home, seed_workspace
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import FileSeed, ScriptedCall, ScriptedTurn
from dotagent.events import Subscription
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, Message, ModelReply
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.server import UI_SOURCE
from dotagent.store.models import ApprovalRecord, EventRecord, RunRecord, ThreadRecord
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Note the port of the staging server in port.txt."
QUESTION: str = "Which port does the staging server use?"
ANSWER: str = "8431"
CARRIED: str = f"Answer to your question “{QUESTION}”: {ANSWER}"
NEEDS: str = "The port of the staging server."
NOTES: FileSeed = FileSeed(path="notes.txt", content="The staging server runs the API.\n")
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_MAX_EVENTS: int = 10_000


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s)


class _HeldModel(ScriptedProvider):
    """The scripted model, whose ``held``-th call waits until the test lets it go: the test acts while the run is in
    that step."""

    def __init__(self, turns: Sequence[ScriptedTurn], held: int) -> None:
        super().__init__(_PROVIDER, turns)
        self._held: int = held
        self._calls: int = 0
        self.entered: asyncio.Event = asyncio.Event()
        self.release: asyncio.Event = asyncio.Event()

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self._calls += 1
        if self._calls == self._held:
            self.entered.set()
            await self.release.wait()
        return await super().complete(request, sink)


@asynccontextmanager
async def _session(login_env: dict[str, str], model: ScriptedProvider) -> AsyncIterator[tuple[Runtime, RunManager]]:
    """A runtime in a throw-away home whose model is ``model``, with ``NOTES`` in its workspace, and its runs."""
    config: AppConfig = eval_config(load_default_config())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, model)
            await asyncio.to_thread(seed_workspace, runtime.workspace, [NOTES])
            manager: RunManager = RunManager(runtime)
            try:
                yield runtime, manager
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _finish(answer: str, success: bool, needs: str) -> ScriptedTurn:
    return _turn(
        ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": success, "needs_from_user": needs})
    )


ASK: ScriptedTurn = _turn(ScriptedCall(tool=ASK_USER_TOOL, arguments={"question": QUESTION, "options": [ANSWER]}))
READ: ScriptedCall = ScriptedCall(tool="read_file", arguments={"path": NOTES.path})


async def _start(manager: RunManager, source: str, approvals: ApprovalMode) -> RunRecord:
    return await manager.start(GOAL, "task", SCRIPTED_MODEL, None, source, None, None, approvals, False, ())


async def _ended(manager: RunManager, run: RunRecord) -> RunRecord:
    async with asyncio.timeout(_TIME_LIMIT_S):
        return await manager.wait(run.id)


async def _next(subscription: Subscription, kind: str) -> EventRecord:
    async with asyncio.timeout(_TIME_LIMIT_S):
        event: EventRecord = await subscription.queue.get()
        while event.kind != kind:
            event = await subscription.queue.get()
    return event


async def _events(runtime: Runtime, run: RunRecord) -> list[EventRecord]:
    return await runtime.store.list_events(run.id, 0, _MAX_EVENTS)


def _result_of(events: Sequence[EventRecord], tool: str) -> str:
    texts: list[str] = [
        str(event.data["text"]) for event in events if event.kind == "tool_result" and event.data["tool"] == tool
    ]
    assert len(texts) == 1, texts
    return texts[0]


@pytest.mark.parametrize(("source", "approvals"), [(UI_SOURCE, "yolo"), (SCHEDULE_SOURCE, "ask")])
async def test_a_run_nobody_answers_leaves_its_question_and_the_answer_starts_a_run_on_its_thread(
    login_env: dict[str, str], source: str, approvals: ApprovalMode
) -> None:
    turns: list[ScriptedTurn] = [
        ASK,
        _finish("I could not note the port: nobody told me which it is.", False, NEEDS),
        _finish("port.txt holds 8431.", True, ""),
    ]
    async with _session(login_env, ScriptedProvider(_PROVIDER, turns)) as (runtime, manager):
        asked: RunRecord = await _ended(manager, await _start(manager, source, approvals))
        events: list[EventRecord] = await _events(runtime, asked)
        kinds: list[str] = [event.kind for event in events]
        # Nothing waited for the question, so the run was never blocked.
        assert QUESTION_LEFT_EVENT in kinds
        assert "approval_requested" not in kinds
        assert "left for the user" in _result_of(events, ASK_USER_TOOL)
        finish_args: list[object] = [
            event.data["args"] for event in events if event.kind == "tool_call" and event.data["tool"] == FINISH_TOOL
        ]
        assert finish_args == [{"answer": asked.result, "success": False, "needs_from_user": NEEDS}]
        assert [event.data["needs"] for event in events if event.kind == "run_finished"] == [NEEDS]
        left: list[ApprovalRecord] = await runtime.store.list_left_questions(asked.id)
        assert [(question.summary, question.status) for question in left] == [(QUESTION, "left")]

        # An empty answer is refused and leaves the question open.
        with pytest.raises(InvalidValueError):
            await manager.decide(left[0].id, True, UI_SOURCE, " ")
        assert (await runtime.store.get_approval(left[0].id)).status == "left"
        decided: ApprovalRecord = await manager.decide(left[0].id, True, UI_SOURCE, ANSWER)
        assert (decided.status, decided.note) == ("approved", ANSWER)
        newest: RunRecord = (await runtime.store.list_runs(1, asked.thread_id))[0]
        assert newest.id != asked.id
        # The new run carries the task of the run that asked on, with the answer under it.
        assert (newest.goal, newest.mode, newest.model, newest.approvals, newest.source) == (
            f"{GOAL}\n\n{CARRIED}",
            "task",
            SCRIPTED_MODEL,
            approvals,
            UI_SOURCE,
        )
        assert (await _ended(manager, newest)).status == "done"
        assert await runtime.store.list_left_questions(asked.id) == []
        assert [event.data for event in await _events(runtime, asked) if event.kind == "approval_decided"] == [
            {"approval_id": left[0].id, "status": "approved", "note": ANSWER}
        ]
        # A question is answered once.
        with pytest.raises(RunConflict):
            await manager.decide(left[0].id, True, UI_SOURCE, ANSWER)
        assert len(await runtime.store.list_runs(5, asked.thread_id)) == 2


async def test_an_answer_while_the_run_works_reaches_that_run(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [
        ASK,
        _turn(ScriptedCall(tool="shell", arguments={"command": "sleep 3"})),
        _finish("port.txt holds 8431.", True, ""),
    ]
    async with _session(login_env, ScriptedProvider(_PROVIDER, turns)) as (runtime, manager):
        async with runtime.bus.subscribe(None) as subscription:
            run: RunRecord = await _start(manager, UI_SOURCE, "yolo")
            left: EventRecord = await _next(subscription, QUESTION_LEFT_EVENT)
        await manager.decide(str(left.data["approval_id"]), True, UI_SOURCE, ANSWER)
        assert (await _ended(manager, run)).status == "done"
        assert [record.id for record in await runtime.store.list_runs(5, run.thread_id)] == [run.id]
        assert [event.data["text"] for event in await _events(runtime, run) if event.kind == "steer"] == [CARRIED]


async def test_an_answer_during_the_last_step_is_read_before_the_run_ends(login_env: dict[str, str]) -> None:
    # The step that finishes is held until the answer is in: the run reads it before it ends, and finishes again.
    turns: list[ScriptedTurn] = [
        ASK,
        _finish("I could not note the port.", False, NEEDS),
        _finish("port.txt holds 8431.", True, ""),
    ]
    model: _HeldModel = _HeldModel(turns, 2)
    async with _session(login_env, model) as (runtime, manager):
        async with runtime.bus.subscribe(None) as subscription:
            run: RunRecord = await _start(manager, UI_SOURCE, "yolo")
            left: EventRecord = await _next(subscription, QUESTION_LEFT_EVENT)
        async with asyncio.timeout(_TIME_LIMIT_S):
            await model.entered.wait()
        await manager.decide(str(left.data["approval_id"]), True, UI_SOURCE, ANSWER)
        model.release.set()
        ended: RunRecord = await _ended(manager, run)
        assert (ended.status, ended.result, ended.steps) == ("done", "port.txt holds 8431.", 3)
        assert [record.id for record in await runtime.store.list_runs(5, run.thread_id)] == [run.id]
        assert [event.data["text"] for event in await _events(runtime, run) if event.kind == "steer"] == [CARRIED]


async def test_an_answer_a_stopped_run_did_not_read_stays_in_its_thread(login_env: dict[str, str]) -> None:
    model: _HeldModel = _HeldModel([ASK, _finish("port.txt holds 8431.", True, "")], 2)
    async with _session(login_env, model) as (runtime, manager):
        async with runtime.bus.subscribe(None) as subscription:
            run: RunRecord = await _start(manager, UI_SOURCE, "yolo")
            left: EventRecord = await _next(subscription, QUESTION_LEFT_EVENT)
        async with asyncio.timeout(_TIME_LIMIT_S):
            await model.entered.wait()
        await manager.decide(str(left.data["approval_id"]), True, UI_SOURCE, ANSWER)
        manager.cancel(run.id)
        assert (await _ended(manager, run)).status == "cancelled"
        assert [event.data["text"] for event in await _events(runtime, run) if event.kind == "steer"] == [CARRIED]
        kept: list[Message] = await runtime.store.load_messages(run.thread_id, 0)
        assert (kept[-1].role, kept[-1].content) == ("user", CARRIED)


async def test_a_run_the_user_watches_waits_for_the_answer(login_env: dict[str, str]) -> None:
    model: ScriptedProvider = ScriptedProvider(_PROVIDER, [ASK, _finish("port.txt holds 8431.", True, "")])
    async with _session(login_env, model) as (runtime, manager):
        async with runtime.bus.subscribe(None) as subscription:
            run: RunRecord = await _start(manager, UI_SOURCE, "ask")
            asked: EventRecord = await _next(subscription, "approval_requested")
        assert (await runtime.store.get_run(run.id)).status == "blocked"
        await manager.decide(str(asked.data["approval_id"]), True, UI_SOURCE, ANSWER)
        assert (await _ended(manager, run)).status == "done"
        events: list[EventRecord] = await _events(runtime, run)
        assert _result_of(events, ASK_USER_TOOL) == f"The user answered: {ANSWER}"
        assert QUESTION_LEFT_EVENT not in [event.kind for event in events]
        assert [record.id for record in await runtime.store.list_runs(5, run.thread_id)] == [run.id]


async def test_text_between_tool_steps_does_not_end_a_task_run(login_env: dict[str, str]) -> None:
    # One text reply more than the nudges a run gets, each after a tool step: only replies in a row would end the run.
    nudges: int = load_default_config().agent.finish_nudges
    working: list[ScriptedTurn] = [ScriptedTurn(text="Still reading the notes.", calls=()), _turn(READ)]
    turns: list[ScriptedTurn] = [_turn(READ), *working * (nudges + 1), _finish("The notes name the API.", True, "")]
    async with _session(login_env, ScriptedProvider(_PROVIDER, turns)) as (_, manager):
        ended: RunRecord = await _ended(manager, await _start(manager, UI_SOURCE, "ask"))
    assert (ended.status, ended.result) == ("done", "The notes name the API.")
    assert ended.steps == len(turns)


async def test_a_run_that_only_leaves_questions_between_text_replies_still_ends(login_env: dict[str, str]) -> None:
    # A left question changes nothing until someone answers it, so it does not start the count of text replies again.
    nudges: int = load_default_config().agent.finish_nudges
    waiting: ScriptedTurn = ScriptedTurn(text="I need the port to go on.", calls=())
    turns: list[ScriptedTurn] = [
        step
        for index in range(nudges + 1)
        for step in (
            _turn(ScriptedCall(tool=ASK_USER_TOOL, arguments={"question": f"{QUESTION} ({index})", "options": []})),
            waiting,
        )
    ]
    async with _session(login_env, ScriptedProvider(_PROVIDER, turns)) as (_, manager):
        ended: RunRecord = await _ended(manager, await _start(manager, UI_SOURCE, "yolo"))
    assert (ended.status, ended.result, ended.steps) == ("done", waiting.text, len(turns))


async def test_an_answer_waits_while_a_run_of_another_process_works_on_the_thread(login_env: dict[str, str]) -> None:
    async with _session(login_env, ScriptedProvider(_PROVIDER, [])) as (runtime, manager):
        thread: ThreadRecord = await runtime.store.create_thread("a run of dotx run", "cli")
        elsewhere: RunRecord = await runtime.store.create_run(
            thread.id, GOAL, "task", SCRIPTED_MODEL, None, "cli", "medium", "yolo", False, None, None
        )
        await runtime.store.set_run_status(elsewhere.id, "running")
        left: ApprovalRecord = await runtime.approvals.leave(
            elsewhere.id, ASK_USER_TOOL, QUESTION, "question", {"question": QUESTION, "options": []}
        )
        with pytest.raises(RunConflict):
            await manager.decide(left.id, True, UI_SOURCE, ANSWER)
        assert (await runtime.store.get_approval(left.id)).status == "left"
        assert [record.id for record in await runtime.store.list_runs(5, thread.id)] == [elsewhere.id]
