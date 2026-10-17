"""Schedules end to end: the tools the model calls and the scheduler that starts due runs, against the real store.

The runs that firings start use the real local Ollama server and a cloud model; the tests that need it are skipped
when it is not there. The scheduler is driven with explicit clock values where the test has to control time, and with
its own loop where the test is about the loop.
"""

import asyncio
import dataclasses
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
from structlog.testing import capture_logs

from dotagent.agent.manager import RunManager
from dotagent.agent.verify import new_facts
from dotagent.cli_commands import StatsCommand
from dotagent.cli_stats import cmd_stats
from dotagent.config import ModelsConfig, load_config
from dotagent.core.errors import ConfigError, StoreError, ToolExecutionError, ToolInputError
from dotagent.core.paths import db_path, failsafe_path, workspace_dir
from dotagent.core.types import JsonObject
from dotagent.events import SYSTEM_RUN_ID, EventBus, Subscription
from dotagent.llm.types import Effort, Speed
from dotagent.policy.types import ActionInfo, ApprovalMode
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler import SCHEDULE_MISSED_EVENT, SCHEDULE_SOURCE
from dotagent.scheduler.failures import SCHEDULE_FAILURES_KEY, FiringFailures, parse_failures, record_firing_failure
from dotagent.scheduler.plans import UNSET_OPTIONS, ScheduleRequest, create_schedule, edit_schedule
from dotagent.scheduler.runner import FAILED_TICKS_TO_END, Scheduler
from dotagent.scheduler.timing import format_local, named_zone, next_firing
from dotagent.store.models import (
    NO_PICKS,
    CampaignRecord,
    ChatChoices,
    EventRecord,
    RunMode,
    RunRecord,
    ScheduleOptions,
    ScheduleRecord,
    ThreadRecord,
)
from dotagent.store.stats import StuckWork, collect_stuck
from dotagent.tools.base import PreparedCall, ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.registry import tools_for_mode

pytestmark = pytest.mark.asyncio(loop_scope="module")

OLLAMA_ROOT: str = "http://localhost:11434"
MODEL: str = "gpt-oss:20b-cloud"
# The model a schedule picks for itself; the configuration's main model is MODEL, so a run on the wrong one shows.
CHOSEN_REF: str = "ollama:deepseek-v4.1-flash:cloud"
OTHER_ZONE: str = "America/New_York"
GOAL: str = "Reply with the single word pong and nothing else. Do not use any tools."
FINAL_STATUSES: frozenset[str] = frozenset({"done", "blocked", "failed", "cancelled"})
RUN_TIMEOUT_S: float = 180.0


def model_is_listed(name: str) -> bool:
    try:
        answer: httpx.Response = httpx.get(f"{OLLAMA_ROOT}/api/tags", timeout=2.0)
    except httpx.HTTPError:
        return False
    return answer.status_code == 200 and any(model["name"] == name for model in answer.json()["models"])


requires_model = pytest.mark.skipif(not model_is_listed(MODEL), reason=f"needs the local Ollama server with {MODEL}")
requires_chosen = pytest.mark.skipif(
    not model_is_listed(CHOSEN_REF.split(":", 1)[1]), reason=f"needs the local Ollama server with {CHOSEN_REF}"
)


class _Recorder:
    """Stands in for the run gate: lets every action through."""

    async def authorize(self, info: ActionInfo) -> str:
        return ""

    def add_instruction(self, text: str) -> None:
        return None


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = tmp_path_factory.mktemp("home")
    (path / "config.toml").write_text(
        f'[models]\nmain = "ollama:{MODEL}"\n\n[scheduler]\ntick_s = 0.2\n', encoding="utf-8"
    )
    return path


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(home: Path) -> AsyncIterator[Runtime]:
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def no_schedules(runtime: Runtime) -> None:
    for record in await runtime.store.list_schedules():
        await runtime.store.delete_schedule(record.id)


@pytest_asyncio.fixture(loop_scope="module")
async def scheduler(runtime: Runtime) -> AsyncIterator[Scheduler]:
    manager: RunManager = RunManager(runtime)
    built: Scheduler = Scheduler(runtime, manager)
    try:
        yield built
    finally:
        await built.stop()
        await manager.shutdown()
        await built.drain()


async def _schedule(runtime: Runtime, request: ScheduleRequest) -> ScheduleRecord:
    return await create_schedule(
        runtime.store, runtime.config.scheduler, runtime.schedule_zone, request, time.time(), None
    )


def _cron(name: str, cron: str) -> ScheduleRequest:
    return ScheduleRequest(name=name, goal=GOAL, cron=cron, at=None, mode="research", options=UNSET_OPTIONS)


def _once(runtime: Runtime, name: str, seconds_ahead: float) -> ScheduleRequest:
    moment: datetime = datetime.fromtimestamp(time.time() + seconds_ahead, tz=runtime.schedule_zone)
    return ScheduleRequest(
        name=name, goal=GOAL, cron=None, at=moment.strftime("%Y-%m-%dT%H:%M:%S"), mode="research", options=UNSET_OPTIONS
    )


def _planned(record: ScheduleRecord) -> float:
    assert record.next_run_at is not None, record
    return record.next_run_at


async def _runs_in(runtime: Runtime, record: ScheduleRecord) -> list[RunRecord]:
    assert record.thread_id is not None, record
    return await runtime.store.list_runs(20, record.thread_id)


async def _until_over(runtime: Runtime, schedule_id: str) -> ScheduleRecord:
    deadline: float = time.monotonic() + RUN_TIMEOUT_S
    while True:
        record: ScheduleRecord = await runtime.store.get_schedule(schedule_id)
        if record.last_status in FINAL_STATUSES:
            return record
        assert time.monotonic() < deadline, record
        await asyncio.sleep(0.2)


# ------------------------------------------------------------------------------------------ the tools


async def _prepare(
    runtime: Runtime, run: RunRecord, name: str, arguments: JsonObject
) -> tuple[PreparedCall, ToolContext]:
    ctx: ToolContext = ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id=run.id,
        agent_id=None,
        thread_id=run.thread_id,
        mode=run.mode,
        approvals=run.approvals,
        questions="wait",
        workspace=workspace_dir(),
        gate=_Recorder(),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )
    return await runtime.registry.tools[name].prepare(arguments, ctx), ctx


async def _call(runtime: Runtime, run: RunRecord, name: str, arguments: JsonObject) -> tuple[ToolResult, ActionInfo]:
    prepared, ctx = await _prepare(runtime, run, name, arguments)
    return await prepared.run(ctx), prepared.action


async def _chat_run(runtime: Runtime) -> RunRecord:
    """The run of a chat the owner started with choices of their own: YOLO, goal mode, a chosen model and level, the
    research mode; the chat keeps them as its composer shows them."""
    store = runtime.store
    thread: ThreadRecord = await store.create_thread("Plan the week", "ui")
    run: RunRecord = await store.create_run(
        thread.id,
        "Plan the week",
        "research",
        CHOSEN_REF,
        None,
        "ui",
        "high",
        "standard",
        None,
        "yolo",
        True,
        None,
        None,
        None,
    )
    choices: ChatChoices = ChatChoices(
        mode="research", model=CHOSEN_REF, effort="high", speed="standard", approvals="yolo", goal_mode=True
    )
    await store.set_chat_choices(thread.id, choices)
    return run


async def test_the_model_can_create_list_and_delete_schedules(runtime: Runtime) -> None:
    run: RunRecord = await _chat_run(runtime)
    created, action = await _call(
        runtime,
        run,
        "schedule_create",
        {"name": "Weekday digest", "goal": "Summarise today's Python news into digest.md", "cron": "0 9 * * 1-5"},
    )
    assert not created.is_error
    assert (action.tool, action.kind, action.risk, action.flags) == (
        "schedule_create",
        "schedule",
        "medium",
        ("persistence",),
    )
    assert "cron 0 9 * * 1-5" in action.summary
    identified: re.Match[str] | None = re.match(r"Scheduled: (sch_\w+) ", created.text)
    assert identified is not None, created.text
    assert "dotx daemon" in created.text
    # The schedule starts its runs as the chat it was made in does, in that chat (item 291).
    kept: ScheduleRecord = await runtime.store.get_schedule(identified.group(1))
    assert (kept.mode, kept.thread_id) == ("research", run.thread_id)
    assert kept.options == ScheduleOptions(
        model=CHOSEN_REF,
        effort="high",
        speed="standard",
        approvals="yolo",
        goal_mode=True,
        timezone=None,
        history_runs=None,
        same_chat=True,
    )

    listed, action = await _call(runtime, run, "schedule_list", {})
    assert (action.kind, action.risk) == ("meta", "safe")
    assert identified.group(1) in listed.text
    assert "Weekday digest" in listed.text
    assert "goal: Summarise today's Python news" in listed.text

    with pytest.raises(ToolInputError, match="either cron or at"):
        await _prepare(
            runtime,
            run,
            "schedule_create",
            {"name": "Both", "goal": "Do something at some point", "cron": "0 9 * * *", "at": "2099-01-01T09:00"},
        )
    with pytest.raises(ToolInputError, match="too often"):
        await _prepare(
            runtime,
            run,
            "schedule_create",
            {"name": "Often", "goal": "Do something every minute", "cron": "* * * * *"},
        )

    deleted, action = await _call(runtime, run, "schedule_delete", {"schedule_id": identified.group(1)})
    assert action.summary == f'Delete schedule "Weekday digest" ({identified.group(1)})'
    assert deleted.text.startswith("Deleted: ")
    with pytest.raises(StoreError, match="not found"):
        await _prepare(runtime, run, "schedule_delete", {"schedule_id": identified.group(1)})
    gone, _ = await _call(runtime, run, "schedule_list", {})
    assert gone.text.startswith("No schedules.")


async def test_only_listing_schedules_is_open_to_research_runs(runtime: Runtime) -> None:
    research: set[str] = {tool.spec.name for tool in tools_for_mode(runtime.registry, "research")}
    assert "schedule_list" in research
    assert {"schedule_create", "schedule_delete"}.isdisjoint(research)
    task: set[str] = {tool.spec.name for tool in tools_for_mode(runtime.registry, "task")}
    assert {"schedule_create", "schedule_list", "schedule_delete"} <= task


async def test_the_number_of_switched_on_schedules_is_limited(runtime: Runtime) -> None:
    limit: int = runtime.config.scheduler.max_schedules
    for number in range(limit):
        await _schedule(runtime, _cron(f"Schedule {number}", "0 9 * * *"))
    with pytest.raises(ToolExecutionError, match="too many schedules"):
        await _schedule(runtime, _cron("One too many", "0 9 * * *"))


# --------------------------------------------------------------------------------- firing and skipping


def _missed(feed: Subscription) -> list[tuple[str, str, str]]:
    """The firings ``feed`` (of ``schedule_missed``) was told were missed so far: the run id, name and status."""
    events: list[EventRecord] = [feed.queue.get_nowait() for _ in range(feed.queue.qsize())]
    return [(event.run_id, str(event.data["name"]), str(event.data["status"])) for event in events]


async def test_an_overdue_firing_is_skipped_and_the_next_one_planned(runtime: Runtime, scheduler: Scheduler) -> None:
    grace_s: int = runtime.config.scheduler.misfire_grace_s
    threads_before: int = len(await runtime.store.list_threads(1000))

    recurring: ScheduleRecord = await _schedule(runtime, _cron("Daily", "0 9 * * *"))
    late: float = _planned(recurring) + grace_s + 120
    async with runtime.bus.subscribe_kinds(frozenset({SCHEDULE_MISSED_EVENT})) as feed:
        await scheduler.tick(late)
        told: list[tuple[str, str, str]] = _missed(feed)
    skipped: ScheduleRecord = await runtime.store.get_schedule(recurring.id)
    # Item 151: nobody saw the firing go, so the owner is told.
    assert told == [(SYSTEM_RUN_ID, "Daily", skipped.last_status)]
    assert skipped.last_status.startswith("skipped: ") and "overdue" in skipped.last_status
    assert skipped.enabled
    assert skipped.next_run_at == next_firing("0 9 * * *", late, runtime.schedule_zone)
    assert (skipped.last_run_at, skipped.thread_id) == (None, None)
    assert len(await runtime.store.list_threads(1000)) == threads_before


async def test_a_firing_that_cannot_start_leaves_no_thread_behind(runtime: Runtime) -> None:
    broken_models: ModelsConfig = runtime.config.models.model_copy(update={"main": "nonsense"})
    broken: Runtime = dataclasses.replace(runtime, config=runtime.config.model_copy(update={"models": broken_models}))
    manager: RunManager = RunManager(broken)
    threads_before: int = len(await runtime.store.list_threads(1000))

    recurring: ScheduleRecord = await _schedule(runtime, _cron("Broken", "0 9 * * *"))
    now: float = _planned(recurring) + 5
    async with runtime.bus.subscribe_kinds(frozenset({SCHEDULE_MISSED_EVENT})) as feed:
        await Scheduler(broken, manager).tick(now)
        told: list[tuple[str, str, str]] = _missed(feed)
        failed: ScheduleRecord = await runtime.store.get_schedule(recurring.id)
        assert failed.next_run_at is not None
        # The next firing fails the same way: the owner was told already.
        await Scheduler(broken, manager).tick(failed.next_run_at + 5)
        told_again: list[tuple[str, str, str]] = _missed(feed)

    assert failed.last_status.startswith("failed to start: a model reference must look like")
    assert failed.next_run_at == next_firing("0 9 * * *", now, runtime.schedule_zone)
    assert told == [(SYSTEM_RUN_ID, "Broken", failed.last_status)] and told_again == []
    assert (await runtime.store.get_schedule(recurring.id)).last_status == failed.last_status
    assert len(await runtime.store.list_threads(1000)) == threads_before


class _QueueFails(EventBus):
    """A bus whose store fails as a run is queued: the run's record is made, and its start fails after."""

    async def emit(self, run_id: str, kind: str, data: JsonObject, persist: bool) -> EventRecord:
        if kind == "run_queued":
            raise StoreError("the database is locked", {"run_id": run_id})
        return await super().emit(run_id, kind, data, persist)


async def test_a_firing_whose_run_fails_after_it_was_made_leaves_no_run_behind(runtime: Runtime) -> None:
    failing: Runtime = dataclasses.replace(runtime, bus=_QueueFails(runtime.store))
    runs_before: int = len(await runtime.store.list_runs(1000, None))

    recurring: ScheduleRecord = await _schedule(runtime, _cron("Queue fails", "0 9 * * *"))
    await Scheduler(failing, RunManager(failing)).tick(_planned(recurring) + 5)

    failed: ScheduleRecord = await runtime.store.get_schedule(recurring.id)
    assert failed.last_status == "failed to start: the database is locked"
    assert len(await runtime.store.list_runs(1000, None)) == runs_before


class _OwnerSwitchesTheOtherOff(RunManager):
    """While the tick fires the first of two schedules, the owner switches the other off; no run starts here."""

    def __init__(self, runtime: Runtime, schedule_of: dict[str, str]) -> None:
        super().__init__(runtime)
        self._schedule_of: dict[str, str] = schedule_of
        self.started: list[str] = []

    async def start_firing(
        self,
        goal: str,
        opening: str,
        mode: RunMode,
        model: str | None,
        thread_id: str,
        effort: Effort | None,
        speed: Speed,
        approvals: ApprovalMode | None,
        goal_mode: bool | None,
        carried: tuple[str, ...],
    ) -> RunRecord:
        self.started.append(goal)
        for other, schedule_id in self._schedule_of.items():
            if other != goal:
                await self._runtime.store.set_schedule_enabled(schedule_id, False, None)
        raise ConfigError("no run starts in this test", {"goal": goal})


async def test_a_schedule_switched_off_while_the_tick_fires_another_does_not_fire(runtime: Runtime) -> None:
    """Item 136: each schedule is read again just before it fires, so the tick does not act on what it listed."""
    goals: tuple[str, str] = ("Say ping.", "Say pong.")
    schedules: list[ScheduleRecord] = [
        await _schedule(
            runtime,
            ScheduleRequest(name=goal, goal=goal, cron="0 9 * * *", at=None, mode="research", options=UNSET_OPTIONS),
        )
        for goal in goals
    ]
    manager: _OwnerSwitchesTheOtherOff = _OwnerSwitchesTheOtherOff(
        runtime, {record.goal: record.id for record in schedules}
    )
    await Scheduler(runtime, manager).tick(max(_planned(record) for record in schedules) + 5)
    assert len(manager.started) == 1


async def test_while_the_emergency_stop_is_engaged_a_due_schedule_is_skipped_to_its_next_time(
    runtime: Runtime,
) -> None:
    """Item 136: no schedule fires while the owner's emergency stop is engaged; the firing is skipped, saying why. The
    runtime's model cannot start a run, so a firing would show as a failed start."""
    broken_models: ModelsConfig = runtime.config.models.model_copy(update={"main": "nonsense"})
    broken: Runtime = dataclasses.replace(runtime, config=runtime.config.model_copy(update={"models": broken_models}))
    threads_before: int = len(await runtime.store.list_threads(1000))
    recurring: ScheduleRecord = await _schedule(runtime, _cron("Held", "0 9 * * *"))
    now: float = _planned(recurring) + 5
    stop: Path = failsafe_path()
    await asyncio.to_thread(stop.touch)
    try:
        await Scheduler(broken, RunManager(broken)).tick(now)
    finally:
        await asyncio.to_thread(stop.unlink)
    held: ScheduleRecord = await runtime.store.get_schedule(recurring.id)
    assert held.last_status == "skipped: the emergency stop is engaged"
    assert held.next_run_at == next_firing("0 9 * * *", now, runtime.schedule_zone)
    assert (held.last_run_at, held.thread_id) == (None, None)
    assert len(await runtime.store.list_threads(1000)) == threads_before


class _StartsAndEnds(RunManager):
    """Starts every firing's run as a record of the store, which ends done once the test lets it (``ends``); no model
    runs."""

    def __init__(self, runtime: Runtime) -> None:
        super().__init__(runtime)
        self.goals: list[str] = []
        self.ends: asyncio.Event = asyncio.Event()

    async def start_firing(
        self,
        goal: str,
        opening: str,
        mode: RunMode,
        model: str | None,
        thread_id: str,
        effort: Effort | None,
        speed: Speed,
        approvals: ApprovalMode | None,
        goal_mode: bool | None,
        carried: tuple[str, ...],
    ) -> RunRecord:
        self.goals.append(goal)
        return await self._runtime.store.create_run(
            thread_id,
            goal,
            mode,
            f"ollama:{MODEL}",
            None,
            SCHEDULE_SOURCE,
            "medium",
            speed,
            None,
            "ask",
            False,
            None,
            None,
            None,
        )

    async def wait(self, run_id: str) -> RunRecord:
        await self.ends.wait()
        await self._runtime.store.finish_run(run_id, "done", "pong")
        return await self._runtime.store.get_run(run_id)


class _Starts(_StartsAndEnds):
    """Keeps the opening of every run a firing starts, and starts it as :class:`_StartsAndEnds` does, ended at once."""

    def __init__(self, runtime: Runtime) -> None:
        super().__init__(runtime)
        self.started: list[str] = []
        self.ends.set()

    async def start_firing(
        self,
        goal: str,
        opening: str,
        mode: RunMode,
        model: str | None,
        thread_id: str,
        effort: Effort | None,
        speed: Speed,
        approvals: ApprovalMode | None,
        goal_mode: bool | None,
        carried: tuple[str, ...],
    ) -> RunRecord:
        self.started.append(opening)
        return await super().start_firing(
            goal, opening, mode, model, thread_id, effort, speed, approvals, goal_mode, carried
        )


async def test_a_schedule_whose_running_status_was_not_written_is_free_once_its_run_ends(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The code review's O3: the schedule was marked as running a run before its status was written, and freed only by
    a watch started after that write; a write the store failed kept it running for good, so every later firing was
    skipped as if its run were still going."""
    write_status = runtime.store.set_schedule_status

    async def fails_on_running(schedule_id: str, status: str) -> None:
        if status == "running":
            raise StoreError("the database is locked", {"schedule_id": schedule_id})
        await write_status(schedule_id, status)

    monkeypatch.setattr(runtime.store, "set_schedule_status", fails_on_running)
    recurring: ScheduleRecord = await _schedule(runtime, _cron("Often", "*/5 * * * *"))
    manager: _StartsAndEnds = _StartsAndEnds(runtime)
    scheduler: Scheduler = Scheduler(runtime, manager)
    try:
        await scheduler.tick(_planned(recurring) + 5)
        manager.ends.set()
        fired: ScheduleRecord = await _until_over(runtime, recurring.id)
        await scheduler.tick(_planned(fired) + 5)
    finally:
        await scheduler.drain()
    assert manager.goals == [GOAL, GOAL]
    assert (await runtime.store.get_schedule(recurring.id)).last_status != "skipped: the previous run was still going"


class _FailsThenStarts(_StartsAndEnds):
    """Starts a firing's run as :class:`_StartsAndEnds` does once two starts failed: one with Dot's own error, one with
    a crash."""

    def __init__(self, runtime: Runtime) -> None:
        super().__init__(runtime)
        self.failures: list[Exception] = [ConfigError("the model is gone", {}), RuntimeError("the start crashed")]

    async def start_firing(
        self,
        goal: str,
        opening: str,
        mode: RunMode,
        model: str | None,
        thread_id: str,
        effort: Effort | None,
        speed: Speed,
        approvals: ApprovalMode | None,
        goal_mode: bool | None,
        carried: tuple[str, ...],
    ) -> RunRecord:
        if self.failures:
            raise self.failures.pop(0)
        return await super().start_firing(
            goal, opening, mode, model, thread_id, effort, speed, approvals, goal_mode, carried
        )


async def test_a_one_time_schedule_whose_run_does_not_start_stays_due_and_fires_later(runtime: Runtime) -> None:
    """Item 351: a one-time schedule's firing switched it off before its run started, so one whose start failed (Dot's
    error, or a crash) never fired. It stays due now, and fires once its run can start."""
    once: ScheduleRecord = await _schedule(runtime, _once(runtime, "Flaky once", 60.0))
    manager: _FailsThenStarts = _FailsThenStarts(runtime)
    scheduler: Scheduler = Scheduler(runtime, manager)
    due: float = _planned(once)
    try:
        await scheduler.tick(due + 5)
        refused: ScheduleRecord = await runtime.store.get_schedule(once.id)
        assert (refused.enabled, refused.next_run_at) == (True, due), refused
        assert refused.last_status == "failed to start: the model is gone"
        await scheduler.tick(due + 10)
        crashed: ScheduleRecord = await runtime.store.get_schedule(once.id)
        assert (crashed.enabled, crashed.next_run_at, crashed.last_status) == (
            True,
            due,
            "failed to fire: RuntimeError",
        )
        await scheduler.tick(due + 15)
        manager.ends.set()
        fired: ScheduleRecord = await _until_over(runtime, once.id)
    finally:
        await scheduler.drain()
    assert (fired.enabled, fired.next_run_at, fired.last_status, manager.goals) == (False, None, "done", [GOAL])


async def test_a_firing_that_fails_is_counted_leaves_no_chat_and_the_schedule_fires_again(runtime: Runtime) -> None:
    """The code review's O3-O6: a firing whose run does not start, with Dot's error or with a crash, leaves no chat
    behind either way and counts in a row with its error, which ``dotx stats`` shows; the next firing starts its run
    and clears the count."""
    threads_before: int = len(await runtime.store.list_threads(1000))
    recurring: ScheduleRecord = await _schedule(runtime, _cron("Flaky", "*/5 * * * *"))
    manager: _FailsThenStarts = _FailsThenStarts(runtime)
    scheduler: Scheduler = Scheduler(runtime, manager)
    try:
        await scheduler.tick(_planned(recurring) + 5)
        once: ScheduleRecord = await runtime.store.get_schedule(recurring.id)
        await scheduler.tick(_planned(once) + 5)
        twice: ScheduleRecord = await runtime.store.get_schedule(recurring.id)
        counted: FiringFailures = parse_failures(await runtime.store.kv_get(SCHEDULE_FAILURES_KEY))
        threads_after_failures: int = len(await runtime.store.list_threads(1000))
        await scheduler.tick(_planned(twice) + 5)
        manager.ends.set()
        fired: ScheduleRecord = await _until_over(runtime, recurring.id)
    finally:
        await scheduler.drain()
    assert once.last_status == "failed to start: the model is gone"
    assert twice.last_status == "failed to fire: RuntimeError"
    failing = counted.schedules[recurring.id]
    assert (failing.failures_in_row, failing.last_error) == (2, "failed to fire: RuntimeError")
    assert threads_after_failures == threads_before
    assert (fired.last_status, manager.goals) == ("done", [GOAL])
    assert recurring.id not in parse_failures(await runtime.store.kv_get(SCHEDULE_FAILURES_KEY)).schedules


async def test_a_schedule_left_starting_or_running_is_marked_interrupted_and_logged_as_the_scheduler_starts(
    runtime: Runtime,
) -> None:
    """The code review's O3-O6: a schedule whose run was watched by the process before this one is not left running."""
    running: ScheduleRecord = await _schedule(runtime, _cron("Left running", "0 9 * * *"))
    starting: ScheduleRecord = await _schedule(runtime, _cron("Left starting", "0 10 * * *"))
    ended: ScheduleRecord = await _schedule(runtime, _cron("Ended", "0 11 * * *"))
    await runtime.store.set_schedule_status(running.id, "running")
    await runtime.store.set_schedule_status(starting.id, "starting")
    await runtime.store.set_schedule_status(ended.id, "done")
    scheduler: Scheduler = Scheduler(runtime, RunManager(runtime))
    with capture_logs() as logs:
        await scheduler.start()
        await scheduler.stop()
    statuses: list[str] = [
        (await runtime.store.get_schedule(record.id)).last_status for record in (running, starting, ended)
    ]
    assert statuses == ["interrupted", "interrupted", "done"]
    told: list[tuple[object, object]] = [
        (entry["schedule_id"], entry["status"]) for entry in logs if entry["event"] == "schedule_interrupted"
    ]
    assert sorted(map(str, told)) == sorted(map(str, [(running.id, "running"), (starting.id, "starting")]))


async def test_dotx_stats_counts_what_waits_and_will_not_move_on_by_itself(
    runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    """The code review's O3-O6: a schedule marked running with no run at work, an active campaign with no session
    planned, a chat whose queued message nothing moves on and one held for the owner are counted; a schedule whose
    firings fail is named with how many in a row and its error."""
    before: StuckWork = await asyncio.to_thread(collect_stuck, db_path())
    stale: ScheduleRecord = await _schedule(runtime, _cron("Left running", "0 9 * * *"))
    await runtime.store.set_schedule_status(stale.id, "running")
    failing: ScheduleRecord = await _schedule(runtime, _cron("Flaky", "0 10 * * *"))
    await record_firing_failure(runtime.store, failing.id, time.time(), "failed to start: the model is gone")
    leads: ThreadRecord = await runtime.store.create_thread("Leads", "campaign")
    campaign: CampaignRecord = await runtime.store.create_campaign(
        "Leads", "Find fifty leads.", leads.id, f"ollama:{MODEL}", "medium", "standard", None, "ask", (), "own"
    )
    stalled: ThreadRecord = await runtime.store.create_thread("Stalled", "test")
    held: ThreadRecord = await runtime.store.create_thread("Held", "test")
    for thread in (stalled, held):
        await runtime.store.add_queued(
            thread.id,
            "Then say pong.",
            (),
            "research",
            None,
            None,
            "standard",
            None,
            NO_PICKS,
            None,
            None,
            None,
            "test",
        )
    await runtime.store.hold_queue(held.id)
    try:
        after: StuckWork = await asyncio.to_thread(collect_stuck, db_path())
        assert await cmd_stats(StatsCommand(name="stats", days=1, by="model", run=None)) == 0
        printed: str = capsys.readouterr().out
    finally:
        await runtime.store.set_campaign_status(campaign.id, "stopped", None)
        await runtime.store.delete_threads([stalled.id, held.id])
    added: tuple[int, int, int, int] = (
        after.stale_schedules - before.stale_schedules,
        after.unplanned_campaigns - before.unplanned_campaigns,
        after.stalled_queues - before.stalled_queues,
        after.held_queues - before.held_queues,
    )
    assert added == (1, 1, 1, 1)
    assert "failing schedules: 1" in printed
    assert "    Flaky: 1 firings failed in a row, the last " in printed
    assert "the model is gone" in printed


async def test_a_one_time_schedule_overdue_fires_late_saying_when_it_was_due(runtime: Runtime) -> None:
    """Item 152: a one-time schedule whose moment passed while the daemon was off or the Mac slept has no next time to
    wait for: it fires late, and its run reads when it was due, where before it was switched off unseen; the run keeps
    the goal alone (item 257)."""
    single: ScheduleRecord = await _schedule(runtime, _once(runtime, "Once", 3600))
    due: float = _planned(single)
    late: float = due + runtime.config.scheduler.misfire_grace_s + 120
    manager: _Starts = _Starts(runtime)
    await Scheduler(runtime, manager).tick(late)
    fired: ScheduleRecord = await runtime.store.get_schedule(single.id)
    zone: ZoneInfo = runtime.schedule_zone
    assert manager.started == [
        f"(Scheduled for {format_local(due, zone)}; it starts late, at {format_local(late, zone)}.)\n\n{GOAL}"
    ]
    assert manager.goals == [GOAL]
    assert (fired.enabled, fired.next_run_at, fired.last_run_at) == (False, None, late)


async def test_a_one_time_schedule_due_under_the_emergency_stop_waits_for_its_release(runtime: Runtime) -> None:
    """Item 152: while the owner's emergency stop is engaged a one-time schedule does not fire, and it is not dropped
    either: it stays due, saying why, and fires once the stop is released."""
    single: ScheduleRecord = await _schedule(runtime, _once(runtime, "Held once", 3600))
    due: float = _planned(single)
    manager: _Starts = _Starts(runtime)
    scheduler: Scheduler = Scheduler(runtime, manager)
    stop: Path = failsafe_path()
    await asyncio.to_thread(stop.touch)
    try:
        await scheduler.tick(due + 5)
        await scheduler.tick(due + 20)
    finally:
        await asyncio.to_thread(stop.unlink)
    held: ScheduleRecord = await runtime.store.get_schedule(single.id)
    assert manager.started == []
    assert (held.enabled, held.next_run_at, held.last_status) == (True, due, "waiting: the emergency stop is engaged")
    await scheduler.tick(due + 35)
    fired: ScheduleRecord = await runtime.store.get_schedule(single.id)
    assert manager.started == [GOAL]
    assert (fired.enabled, fired.next_run_at, fired.last_run_at) == (False, None, due + 35)


@requires_model
@pytest.mark.live
async def test_a_single_schedule_runs_once_in_a_thread_of_its_own(runtime: Runtime, scheduler: Scheduler) -> None:
    created: ScheduleRecord = await _schedule(runtime, _once(runtime, "Pong", 3))
    await scheduler.start()
    finished: ScheduleRecord = await _until_over(runtime, created.id)
    await scheduler.stop()

    assert finished.last_status == "done"
    assert (finished.enabled, finished.next_run_at) == (False, None)
    assert finished.last_run_at is not None and finished.last_run_at >= _planned(created)
    runs: list[RunRecord] = await _runs_in(runtime, finished)
    assert len(runs) == 1
    assert (runs[0].source, runs[0].mode, runs[0].status) == (SCHEDULE_SOURCE, "research", "done")
    assert "pong" in runs[0].result.lower()
    assert finished.thread_id is not None
    thread: ThreadRecord = await runtime.store.get_thread(finished.thread_id)
    assert thread.source == SCHEDULE_SOURCE
    assert thread.title.startswith("Pong · ")


@requires_model
@pytest.mark.live
async def test_missed_firings_collapse_and_a_running_schedule_skips(runtime: Runtime, scheduler: Scheduler) -> None:
    created: ScheduleRecord = await _schedule(runtime, _cron("Often", "*/5 * * * *"))
    late: float = _planned(created) + 1200
    await scheduler.tick(late)

    fired: ScheduleRecord = await runtime.store.get_schedule(created.id)
    assert (fired.last_status, fired.last_run_at) == ("running", late)
    assert fired.next_run_at == next_firing("*/5 * * * *", late, runtime.schedule_zone)
    assert _planned(fired) > late

    await scheduler.tick(_planned(fired))
    skipped: ScheduleRecord = await runtime.store.get_schedule(created.id)
    assert skipped.last_status == "skipped: the previous run was still going"
    assert skipped.next_run_at == next_firing("*/5 * * * *", _planned(fired), runtime.schedule_zone)
    assert len(await _runs_in(runtime, skipped)) == 1

    finished: ScheduleRecord = await _until_over(runtime, created.id)
    assert (finished.last_status, finished.enabled) == ("done", True)


# ------------------------------------------------------------------------------------------- editing


async def test_a_schedule_is_edited_whole_and_keeps_its_history(runtime: Runtime) -> None:
    created: ScheduleRecord = await _schedule(runtime, _cron("Morning", "0 9 * * *"))
    assert created.options == UNSET_OPTIONS
    options: ScheduleOptions = ScheduleOptions(
        model=CHOSEN_REF,
        effort="low",
        speed=None,
        approvals="yolo",
        goal_mode=True,
        timezone=OTHER_ZONE,
        history_runs=None,
        same_chat=None,
    )
    edited: ScheduleRecord = await edit_schedule(
        runtime.store,
        runtime.config.scheduler,
        runtime.schedule_zone,
        created.id,
        ScheduleRequest(
            name="Evening", goal="Say good evening.", cron="30 18 * * 1-5", at=None, mode="task", options=options
        ),
        True,
        time.time(),
    )
    assert (edited.id, edited.created_at) == (created.id, created.created_at)
    assert (edited.name, edited.goal, edited.cron, edited.mode, edited.options) == (
        "Evening",
        "Say good evening.",
        "30 18 * * 1-5",
        "task",
        options,
    )
    # The cron is read in the schedule's own zone, not the configured one.
    zone = named_zone(OTHER_ZONE)
    assert edited.next_run_at is not None
    assert format_local(edited.next_run_at, zone).endswith("18:30")

    # Switched off, a schedule waits for nothing; a one-time moment that is not changed may lie in the past.
    off: ScheduleRecord = await edit_schedule(
        runtime.store,
        runtime.config.scheduler,
        runtime.schedule_zone,
        created.id,
        ScheduleRequest(
            name="Once", goal="Say hi.", cron=None, at="2031-03-04T05:06", mode="research", options=options
        ),
        False,
        time.time(),
    )
    assert (off.enabled, off.next_run_at, off.cron) == (False, None, None)
    assert off.run_at is not None
    later_than_it: float = off.run_at + 60
    kept: ScheduleRecord = await edit_schedule(
        runtime.store,
        runtime.config.scheduler,
        runtime.schedule_zone,
        created.id,
        ScheduleRequest(
            name="Renamed", goal="Say hi.", cron=None, at="2031-03-04T05:06", mode="research", options=options
        ),
        False,
        later_than_it,
    )
    assert (kept.name, kept.run_at) == ("Renamed", off.run_at)
    with pytest.raises(ToolExecutionError, match="no run left"):
        await edit_schedule(
            runtime.store,
            runtime.config.scheduler,
            runtime.schedule_zone,
            created.id,
            ScheduleRequest(
                name="Renamed", goal="Say hi.", cron=None, at="2031-03-04T05:06", mode="research", options=options
            ),
            True,
            later_than_it,
        )
    with pytest.raises(ToolInputError, match="unknown time zone"):
        await edit_schedule(
            runtime.store,
            runtime.config.scheduler,
            runtime.schedule_zone,
            created.id,
            _cron("Bad zone", "0 9 * * *").model_copy(
                update={"options": options.model_copy(update={"timezone": "Mars/Base"})}
            ),
            True,
            time.time(),
        )
    with pytest.raises(ConfigError, match="model reference"):
        await _schedule(
            runtime,
            _cron("Bad model", "0 9 * * *").model_copy(
                update={"options": options.model_copy(update={"model": "nonsense"})}
            ),
        )


@requires_chosen
@pytest.mark.live
async def test_a_schedule_starts_its_runs_with_the_mode_model_and_approvals_it_was_given(
    runtime: Runtime, scheduler: Scheduler
) -> None:
    options: ScheduleOptions = ScheduleOptions(
        model=CHOSEN_REF,
        effort=None,
        speed=None,
        approvals="yolo",
        goal_mode=True,
        timezone=OTHER_ZONE,
        history_runs=None,
        same_chat=None,
    )
    request: ScheduleRequest = _once(runtime, "Chosen", 3).model_copy(update={"mode": "task", "options": options})
    # The moment was written in the configured zone; read it in the schedule's own zone instead it would move, so give
    # it with its offset.
    moment: datetime = datetime.fromtimestamp(time.time() + 3, tz=runtime.schedule_zone)
    created: ScheduleRecord = await _schedule(runtime, request.model_copy(update={"at": moment.isoformat()}))
    await scheduler.start()
    finished: ScheduleRecord = await _until_over(runtime, created.id)
    await scheduler.stop()

    assert finished.last_status == "done"
    runs: list[RunRecord] = await _runs_in(runtime, finished)
    assert len(runs) == 1
    run: RunRecord = runs[0]
    assert (run.source, run.mode, run.model, run.approvals, run.goal_mode) == (
        SCHEDULE_SOURCE,
        "task",
        CHOSEN_REF,
        "yolo",
        True,
    )
    queued: list[EventRecord] = [
        event for event in await runtime.store.list_events(run.id, 0, 100) if event.kind == "run_queued"
    ]
    assert queued[0].data["models"] == {
        "steps": CHOSEN_REF,
        "compaction": CHOSEN_REF,
        "review": runtime.config.models.review,
        "vision": runtime.config.models.vision,
    }


async def test_a_tick_that_fails_is_tried_again_and_the_loop_goes_on(
    runtime: Runtime, scheduler: Scheduler, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed: list[float] = []
    third: asyncio.Event = asyncio.Event()
    due_campaigns = runtime.store.due_campaigns

    async def fails_once(now: float) -> list[CampaignRecord]:
        listed.append(now)
        if len(listed) == 3:
            third.set()
        if len(listed) == 1:
            raise StoreError("the database is locked", {})
        return await due_campaigns(now)

    monkeypatch.setattr(runtime.store, "due_campaigns", fails_once)
    loop: asyncio.Task[None] = await scheduler.start()
    async with asyncio.timeout(10):
        await third.wait()
    assert not loop.done()
    assert scheduler.running()


async def test_a_scheduler_whose_ticks_keep_failing_ends_with_their_error(
    runtime: Runtime, scheduler: Scheduler, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed: list[float] = []

    async def always_fails(now: float) -> list[CampaignRecord]:
        listed.append(now)
        raise StoreError("the database is locked", {})

    monkeypatch.setattr(runtime.store, "due_campaigns", always_fails)
    loop: asyncio.Task[None] = await scheduler.start()
    async with asyncio.timeout(30):
        await asyncio.wait({loop})
    assert len(listed) == FAILED_TICKS_TO_END
    with pytest.raises(ExceptionGroup) as ended:
        await scheduler.stop()
    assert [type(error) for error in ended.value.exceptions] == [StoreError]
