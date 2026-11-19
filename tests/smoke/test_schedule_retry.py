"""A schedule's task tried once more on its own (item 1206), in the real store, manager and scheduler with a scripted
model: a task a crash cut is tried once, and the try, cut in its turn, is not tried again but told; a task a passing
error of the model service ended is tried once; a task that sent something out is never tried, and the owner is told;
a task the Claude plan's limit stopped starts again after the reset, not before; and the try is a run of the schedule's
own source, which nobody watches, so that its messages go to the review model as a firing's do."""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import ClaudePlanLimitError, ProviderTransportError
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.events import Subscription
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.notify.owner import OWNER_TOLD_EVENT
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.scheduler.plans import UNSET_OPTIONS, ScheduleRequest, create_schedule
from dotagent.scheduler.recovery import (
    AFTER_RESET_S,
    RETRY_AFTER_S,
    SCHEDULE_RETRY_EVENT,
    ScheduleRetries,
    retries_stored,
)
from dotagent.scheduler.runner import Scheduler
from dotagent.store.endings import RESULT_RESTARTED
from dotagent.store.models import EventRecord, RunRecord, ScheduleRecord, ThreadRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

WAIT_S: float = 60.0
GOAL: str = "Collect the figures of the night."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_WORKING: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 3", "timeout_s": 13}),)
)
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),)
)


class _Failing(ScriptedProvider):
    """A scripted model whose first reply fails with ``error``: a model service that broke off, or a plan's limit."""

    def __init__(self, name: str, turns: Sequence[ScriptedTurn], error: Exception) -> None:
        super().__init__(name, turns)
        self._error: Exception | None = error

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        error: Exception | None = self._error
        if error is not None:
            self._error = None
            raise error
        return await super().complete(request, sink)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config() -> AppConfig:
    """The eval configuration, with a model out of reach not waited for: its error ends the run at once. The nightly
    reflection is off: a tick past its time of day fired it, and it took the scripted reply a retry was to get, so the
    tests failed when they ran in the small hours (2026-11-19, 03:29 and on the gate)."""
    config: AppConfig = eval_config(load_default_config())
    agent = config.agent.model_copy(update={"model_wait_s": 0.0, "reflection_time": "off"})
    return config.model_copy(update={"agent": agent})


@asynccontextmanager
async def _scheduling(
    login_env: dict[str, str], provider: ScriptedProvider
) -> AsyncIterator[tuple[Runtime, RunManager, Scheduler]]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(), login_env)
        try:
            await runtime.router.register(_PROVIDER, provider)
            manager: RunManager = RunManager(runtime)
            scheduler: Scheduler = Scheduler(runtime, manager)
            try:
                yield runtime, manager, scheduler
            finally:
                await scheduler.stop()
                await manager.shutdown()
                await scheduler.drain()
        finally:
            await close_runtime(runtime)


async def _schedule(runtime: Runtime) -> ScheduleRecord:
    """A schedule that fires once a year (on 1 January at 09:00), in a chat of its own each time: none of its firings
    comes within the minutes a test ticks through, but on that morning."""
    return await create_schedule(
        runtime.store,
        runtime.config.scheduler,
        runtime.schedule_zone,
        ScheduleRequest(name="Digest", goal=GOAL, cron="0 9 1 1 *", at=None, mode="task", options=UNSET_OPTIONS),
        time.time(),
        None,
    )


async def _crashed_firing(runtime: Runtime, steps: Sequence[EventRecord]) -> tuple[ScheduleRecord, RunRecord]:
    """A schedule whose firing, in a chat of its own, a crash of Dot cut while it ran, as the next start finds it:
    the run closed as one a crash cut (``RunManager.recover``) and the schedule still saying ``running``. ``steps``
    are the results of its steps, written to its record."""
    store = runtime.store
    schedule: ScheduleRecord = await _schedule(runtime)
    chat: ThreadRecord = await store.create_thread("Digest · firing", SCHEDULE_SOURCE)
    await store.link_schedule_thread(chat.id, schedule.id)
    cut: RunRecord = await store.create_run(
        chat.id, GOAL, "task", SCRIPTED_MODEL, None, SCHEDULE_SOURCE, "medium", "standard", None, "yolo", False, None,
        None, None
    )  # fmt: skip
    await store.record_schedule_hand_run(schedule.id, time.time(), chat.id, cut.id)
    for step in steps:
        await store.add_event(cut.id, step.kind, step.data)
    await store.end_run(cut.id, "failed", RESULT_RESTARTED, {"status": "failed", "result": RESULT_RESTARTED})
    await store.set_schedule_status(schedule.id, "running")
    return await store.get_schedule(schedule.id), cut


def _told(feed: Subscription) -> list[EventRecord]:
    return [feed.queue.get_nowait() for _ in range(feed.queue.qsize())]


async def _schedule_runs(runtime: Runtime, schedule: ScheduleRecord) -> list[RunRecord]:
    """The runs of the schedule's chats, oldest first: each test's store holds that one schedule, so its own runs."""
    assert len(await runtime.store.list_schedules()) == 1, schedule.id
    runs: list[RunRecord] = await runtime.store.list_runs(50, None)
    return sorted((run for run in runs if run.source == SCHEDULE_SOURCE), key=lambda run: run.created_at)


async def _wait_ended(runtime: Runtime, manager: RunManager, scheduler: Scheduler, run_id: str) -> RunRecord:
    async with asyncio.timeout(WAIT_S):
        await manager.wait(run_id)
        await scheduler.drain()
    return await runtime.store.get_run(run_id)


async def test_a_task_a_crash_cut_is_tried_once_and_its_cut_try_is_told(login_env: dict[str, str]) -> None:
    async with _scheduling(login_env, ScriptedProvider(_PROVIDER, [_WORKING, _FINISH])) as (runtime, manager, sched):
        store = runtime.store
        schedule, cut = await _crashed_firing(runtime, ())
        await sched.start()
        waiting: ScheduleRetries = await retries_stored(store)
        assert waiting.pending[schedule.id].from_run_id == cut.id
        assert waiting.pending[schedule.id].why == "cut_off"
        assert (await store.get_schedule(schedule.id)).last_status.startswith("retrying at ")
        # Not before its time.
        await sched.tick(time.time())
        assert [run.id for run in await _schedule_runs(runtime, schedule)] == [cut.id]
        await sched.tick(time.time() + RETRY_AFTER_S + 1)
        runs: list[RunRecord] = await _schedule_runs(runtime, schedule)
        assert len(runs) == 2, runs
        try_run: RunRecord = runs[1]
        # The try is the schedule's own run, which nobody watches: its messages go to the review model.
        assert try_run.source == SCHEDULE_SOURCE
        assert manager._handles[try_run.id].gate.owner_there is False
        marked: EventRecord | None = await store.last_event(try_run.id, SCHEDULE_RETRY_EVENT)
        assert marked is not None and marked.data["from_run_id"] == cut.id and marked.data["why"] == "cut_off"
        chat: ThreadRecord = await store.get_thread(try_run.thread_id)
        assert chat.title.endswith(" · retry"), chat.title
        assert (await retries_stored(store)).pending == {}
        # The try's chat is the schedule's last, where the next start looks for a run a crash cut.
        assert (await store.get_schedule(schedule.id)).thread_id == try_run.thread_id
        # Dot stops while the try works: the try is not tried again, and the owner is told.
        async with runtime.bus.subscribe_kinds(frozenset({OWNER_TOLD_EVENT})) as feed:
            await sched.stop()
            await manager.shutdown()
            await sched.drain()
            told: list[EventRecord] = _told(feed)
        assert (await store.get_run(try_run.id)).status == "cancelled"
        assert (await retries_stored(store)).pending == {}
        assert [event.data["url"] for event in told] == [f"#/runs/{try_run.id}"]
        assert "itself the one try again" in str(told[0].data["text"])
        assert (await store.get_schedule(schedule.id)).last_status.startswith("not tried again: ")


async def test_a_task_a_passing_model_error_ended_is_tried_once(login_env: dict[str, str]) -> None:
    broke: ProviderTransportError = ProviderTransportError("the connection was reset", {"model": SCRIPTED_MODEL})
    async with _scheduling(login_env, _Failing(_PROVIDER, [_FINISH], broke)) as (runtime, manager, sched):
        store = runtime.store
        schedule: ScheduleRecord = await _schedule(runtime)
        first: RunRecord = await sched.fire_by_hand(schedule.id, time.time())
        assert (await _wait_ended(runtime, manager, sched, first.id)).status == "failed"
        ended: EventRecord | None = await store.last_event(first.id, "run_finished")
        assert ended is not None and isinstance(ended.data["error"], dict) and ended.data["error"]["kind"] == "passing"
        assert (await retries_stored(store)).pending[schedule.id].why == "passing_error"
        await sched.tick(time.time() + RETRY_AFTER_S + 1)
        runs: list[RunRecord] = await _schedule_runs(runtime, schedule)
        assert len(runs) == 2 and runs[0].id == first.id, runs
        assert (await _wait_ended(runtime, manager, sched, runs[1].id)).status == "done"
        # Done: nothing more is tried.
        assert (await retries_stored(store)).pending == {}
        await sched.tick(time.time() + 10 * RETRY_AFTER_S)
        assert len(await _schedule_runs(runtime, schedule)) == 2


async def test_a_task_that_sent_a_message_is_never_tried_and_the_owner_is_told(login_env: dict[str, str]) -> None:
    async with _scheduling(login_env, ScriptedProvider(_PROVIDER, [])) as (runtime, _, sched):
        sent: EventRecord = EventRecord(
            id=0,
            run_id="",
            ts=time.time(),
            kind="tool_result",
            data={
                "call_id": "call_1_0",
                "tool": "shell",
                "ok": True,
                "text": "",
                "kind": "shell",
                "flags": ["external_send", "message_send"],
            },
        )
        schedule, cut = await _crashed_firing(runtime, (sent,))
        async with runtime.bus.subscribe_kinds(frozenset({OWNER_TOLD_EVENT})) as feed:
            await sched.start()
            told: list[EventRecord] = _told(feed)
        assert (await retries_stored(runtime.store)).pending == {}
        assert [event.data["url"] for event in told] == [f"#/runs/{cut.id}"]
        assert "sent something out" in str(told[0].data["text"]) and "shell" in str(told[0].data["text"])
        status: str = (await runtime.store.get_schedule(schedule.id)).last_status
        assert status.startswith("not tried again: its task was cut off"), status
        await sched.tick(time.time() + 10 * RETRY_AFTER_S)
        assert [run.id for run in await _schedule_runs(runtime, schedule)] == [cut.id]


def _event(kind: str, data: JsonObject) -> EventRecord:
    """An event to write to a cut task's record (its run id is the task's)."""
    return EventRecord(id=0, run_id="", ts=time.time(), kind=kind, data=data)


async def test_a_try_a_crash_cut_is_not_tried_again_and_the_owner_is_told(login_env: dict[str, str]) -> None:
    async with _scheduling(login_env, ScriptedProvider(_PROVIDER, [])) as (runtime, _, sched):
        mark: EventRecord = _event(SCHEDULE_RETRY_EVENT, {"from_run_id": "run_earlier", "why": "cut_off"})
        schedule, cut = await _crashed_firing(runtime, (mark,))
        async with runtime.bus.subscribe_kinds(frozenset({OWNER_TOLD_EVENT})) as feed:
            await sched.start()
            told: list[EventRecord] = _told(feed)
        assert (await retries_stored(runtime.store)).pending == {}
        assert [event.data["url"] for event in told] == [f"#/runs/{cut.id}"]
        assert "itself the one try again" in str(told[0].data["text"])
        assert (await runtime.store.get_schedule(schedule.id)).last_status.startswith("not tried again: ")


async def test_a_call_a_crash_cut_that_only_its_record_holds_keeps_the_task_from_a_try(
    login_env: dict[str, str],
) -> None:
    # A run on the Claude plan keeps its calls in its events alone: one that started and has no result may have sent.
    started: EventRecord = _event("tool_call", {"call_id": "toolu_1", "tool": "send_message", "args": {}})
    async with _scheduling(login_env, ScriptedProvider(_PROVIDER, [])) as (runtime, _, sched):
        schedule, cut = await _crashed_firing(runtime, (started,))
        async with runtime.bus.subscribe_kinds(frozenset({OWNER_TOLD_EVENT})) as feed:
            await sched.start()
            told: list[EventRecord] = _told(feed)
        assert (await retries_stored(runtime.store)).pending == {}
        assert [event.data["url"] for event in told] == [f"#/runs/{cut.id}"]
        assert "send_message" in str(told[0].data["text"])
        assert (await runtime.store.get_schedule(schedule.id)).last_status.startswith("not tried again: ")


async def test_a_task_the_plans_limit_stopped_starts_after_the_reset(login_env: dict[str, str]) -> None:
    resets_at: float = time.time() + 600
    limit: ClaudePlanLimitError = ClaudePlanLimitError("Your Claude plan's usage limit is reached", resets_at, {})
    async with _scheduling(login_env, _Failing(_PROVIDER, [_FINISH], limit)) as (runtime, manager, sched):
        store = runtime.store
        schedule: ScheduleRecord = await _schedule(runtime)
        first: RunRecord = await sched.fire_by_hand(schedule.id, time.time())
        assert (await _wait_ended(runtime, manager, sched, first.id)).status == "failed"
        waiting: ScheduleRetries = await retries_stored(store)
        assert waiting.pending[schedule.id].why == "plan_limit"
        assert waiting.pending[schedule.id].due_at == pytest.approx(resets_at + AFTER_RESET_S)
        # Before the reset, nothing starts.
        await sched.tick(resets_at - 5)
        assert len(await _schedule_runs(runtime, schedule)) == 1
        await sched.tick(resets_at + AFTER_RESET_S + 1)
        runs: list[RunRecord] = await _schedule_runs(runtime, schedule)
        assert len(runs) == 2, runs
        assert (await _wait_ended(runtime, manager, sched, runs[1].id)).status == "done"
        marked: EventRecord | None = await store.last_event(runs[1].id, SCHEDULE_RETRY_EVENT)
        assert marked is not None and marked.data["why"] == "plan_limit"
