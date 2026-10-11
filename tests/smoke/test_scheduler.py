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

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.verify import new_facts
from dotagent.config import ModelsConfig, load_config
from dotagent.core.errors import ConfigError, StoreError, ToolExecutionError, ToolInputError
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.scheduler.plans import UNSET_OPTIONS, ScheduleRequest, create_schedule, edit_schedule
from dotagent.scheduler.runner import Scheduler
from dotagent.scheduler.timing import format_local, named_zone, next_firing
from dotagent.store.models import EventRecord, RunRecord, ScheduleOptions, ScheduleRecord, ThreadRecord
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
    return await create_schedule(runtime.store, runtime.config.scheduler, runtime.schedule_zone, request, time.time())


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


async def _prepare(runtime: Runtime, name: str, arguments: JsonObject) -> tuple[PreparedCall, ToolContext]:
    ctx: ToolContext = ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id="schedule-test",
        agent_id=None,
        thread_id="schedule-test",
        mode="task",
        approvals="ask",
        questions="wait",
        workspace=workspace_dir(),
        gate=_Recorder(),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )
    return await runtime.registry.tools[name].prepare(arguments, ctx), ctx


async def _call(runtime: Runtime, name: str, arguments: JsonObject) -> tuple[ToolResult, ActionInfo]:
    prepared, ctx = await _prepare(runtime, name, arguments)
    return await prepared.run(ctx), prepared.action


async def test_the_model_can_create_list_and_delete_schedules(runtime: Runtime) -> None:
    created, action = await _call(
        runtime,
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

    listed, action = await _call(runtime, "schedule_list", {})
    assert (action.kind, action.risk) == ("meta", "safe")
    assert identified.group(1) in listed.text
    assert "Weekday digest" in listed.text
    assert "goal: Summarise today's Python news" in listed.text

    with pytest.raises(ToolInputError, match="either cron or at"):
        await _prepare(
            runtime,
            "schedule_create",
            {"name": "Both", "goal": "Do something at some point", "cron": "0 9 * * *", "at": "2099-01-01T09:00"},
        )
    with pytest.raises(ToolInputError, match="too often"):
        await _prepare(
            runtime, "schedule_create", {"name": "Often", "goal": "Do something every minute", "cron": "* * * * *"}
        )

    deleted, action = await _call(runtime, "schedule_delete", {"schedule_id": identified.group(1)})
    assert action.summary == f'Delete schedule "Weekday digest" ({identified.group(1)})'
    assert deleted.text.startswith("Deleted: ")
    with pytest.raises(StoreError, match="not found"):
        await _prepare(runtime, "schedule_delete", {"schedule_id": identified.group(1)})
    gone, _ = await _call(runtime, "schedule_list", {})
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


async def test_an_overdue_firing_is_skipped_and_the_next_one_planned(runtime: Runtime, scheduler: Scheduler) -> None:
    grace_s: int = runtime.config.scheduler.misfire_grace_s
    threads_before: int = len(await runtime.store.list_threads(1000))

    recurring: ScheduleRecord = await _schedule(runtime, _cron("Daily", "0 9 * * *"))
    late: float = _planned(recurring) + grace_s + 120
    await scheduler.tick(late)
    skipped: ScheduleRecord = await runtime.store.get_schedule(recurring.id)
    assert skipped.last_status.startswith("skipped: ") and "overdue" in skipped.last_status
    assert skipped.enabled
    assert skipped.next_run_at == next_firing("0 9 * * *", late, runtime.schedule_zone)
    assert (skipped.last_run_at, skipped.thread_id) == (None, None)

    single: ScheduleRecord = await _schedule(runtime, _once(runtime, "Once", 3600))
    await scheduler.tick(_planned(single) + grace_s + 120)
    ended: ScheduleRecord = await runtime.store.get_schedule(single.id)
    assert ended.last_status.startswith("skipped: ")
    assert (ended.enabled, ended.next_run_at, ended.thread_id) == (False, None, None)

    assert len(await runtime.store.list_threads(1000)) == threads_before


async def test_a_firing_that_cannot_start_leaves_no_thread_behind(runtime: Runtime) -> None:
    broken_models: ModelsConfig = runtime.config.models.model_copy(update={"main": "nonsense"})
    broken: Runtime = dataclasses.replace(runtime, config=runtime.config.model_copy(update={"models": broken_models}))
    manager: RunManager = RunManager(broken)
    threads_before: int = len(await runtime.store.list_threads(1000))

    recurring: ScheduleRecord = await _schedule(runtime, _cron("Broken", "0 9 * * *"))
    now: float = _planned(recurring) + 5
    await Scheduler(broken, manager).tick(now)

    failed: ScheduleRecord = await runtime.store.get_schedule(recurring.id)
    assert failed.last_status.startswith("failed to start: a model reference must look like")
    assert failed.next_run_at == next_firing("0 9 * * *", now, runtime.schedule_zone)
    assert len(await runtime.store.list_threads(1000)) == threads_before


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
        model=CHOSEN_REF, effort="low", approvals="yolo", goal_mode=True, timezone=OTHER_ZONE
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
        model=CHOSEN_REF, effort=None, approvals="yolo", goal_mode=True, timezone=OTHER_ZONE
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
