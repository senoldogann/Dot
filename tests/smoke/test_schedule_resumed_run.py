"""A run of a schedule that the owner resumed by hand after the daemon's end cut it (item 553), in the real store and
manager with a scripted model: the schedule counts it as its run at work, so it fires nothing beside it, and writes
its end when it comes; the resumed follow-up of the owner's own is not the schedule's run. An overdue firing says that
the daemon was off or the Mac slept, which is all the clock can tell."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.scheduler.plans import UNSET_OPTIONS, ScheduleRequest, create_schedule
from dotagent.scheduler.runner import Scheduler
from dotagent.server import UI_SOURCE
from dotagent.store.endings import RESULT_SHUTDOWN
from dotagent.store.models import RunRecord, ScheduleRecord, ThreadRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

WAIT_S: float = 60.0
GOAL: str = "Collect the figures of the night."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# A run works for a few seconds, then finishes; the replies after the first serve whichever run asks.
_WORKING: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 3", "timeout_s": 13}),)
)
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),)
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _scheduling(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, RunManager, Scheduler]]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_WORKING, _FINISH, _FINISH, _FINISH]))
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


async def _cut_firing(runtime: Runtime, source: str) -> tuple[ScheduleRecord, RunRecord]:
    """A repeating schedule whose last firing, in a chat of its own, the daemon's end cut off, and that run; the run is
    of ``source``."""
    store = runtime.store
    schedule: ScheduleRecord = await create_schedule(
        store,
        runtime.config.scheduler,
        runtime.schedule_zone,
        ScheduleRequest(name="Digest", goal=GOAL, cron="0 * * * *", at=None, mode="task", options=UNSET_OPTIONS),
        time.time(),
        None,
    )
    chat: ThreadRecord = await store.create_thread("Digest · firing", SCHEDULE_SOURCE)
    await store.link_schedule_thread(chat.id, schedule.id)
    cut: RunRecord = await store.create_run(
        chat.id, GOAL, "task", SCRIPTED_MODEL, None, source, "medium", "standard", None, "yolo", False, None, None, None
    )
    await store.end_run(cut.id, "cancelled", RESULT_SHUTDOWN, {"status": "cancelled", "result": RESULT_SHUTDOWN})
    await store.record_schedule_hand_run(schedule.id, time.time(), chat.id)
    return await store.get_schedule(schedule.id), cut


async def test_a_schedule_fires_nothing_beside_its_resumed_run_and_gets_its_end(login_env: dict[str, str]) -> None:
    async with _scheduling(login_env) as (runtime, manager, scheduler):
        store = runtime.store
        schedule, cut = await _cut_firing(runtime, SCHEDULE_SOURCE)
        assert schedule.next_run_at is not None and schedule.thread_id is not None
        resumed: RunRecord = await manager.resume(cut.id)
        # The schedule is due while the resumed run works: it is skipped, and says why.
        await scheduler.tick(schedule.next_run_at + 5)
        assert {run.id for run in await store.list_runs(10, schedule.thread_id)} == {cut.id, resumed.id}
        skipped: ScheduleRecord = await store.get_schedule(schedule.id)
        assert skipped.last_status == "skipped: the previous run was still going", skipped
        # Its end is written to the schedule, as the end of a run the scheduler started is.
        async with asyncio.timeout(WAIT_S):
            ended: RunRecord = await manager.wait(resumed.id)
            await scheduler.drain()
        assert ended.status == "done"
        assert (await store.get_schedule(schedule.id)).last_status == "done"


async def test_the_resumed_follow_up_of_the_owner_is_not_the_schedules_run(login_env: dict[str, str]) -> None:
    async with _scheduling(login_env) as (runtime, manager, scheduler):
        store = runtime.store
        # The chat's run that the daemon's end cut was the owner's own message there, not a firing.
        schedule, cut = await _cut_firing(runtime, UI_SOURCE)
        assert schedule.next_run_at is not None and schedule.thread_id is not None
        resumed: RunRecord = await manager.resume(cut.id)
        await scheduler.tick(schedule.next_run_at + 5)
        # The firing was not held back by it: a run of the schedule started in a chat of its own.
        assert (await store.get_schedule(schedule.id)).last_status != "skipped: the previous run was still going"
        async with asyncio.timeout(WAIT_S):
            await manager.wait(resumed.id)
            await scheduler.drain()


async def test_an_overdue_firing_says_the_daemon_was_off_or_the_mac_slept(login_env: dict[str, str]) -> None:
    async with _scheduling(login_env) as (runtime, _, scheduler):
        schedule: ScheduleRecord = await create_schedule(
            runtime.store,
            runtime.config.scheduler,
            runtime.schedule_zone,
            ScheduleRequest(name="Digest", goal=GOAL, cron="0 * * * *", at=None, mode="task", options=UNSET_OPTIONS),
            time.time(),
            None,
        )
        assert schedule.next_run_at is not None
        grace: float = runtime.config.scheduler.misfire_grace_s
        await scheduler.tick(schedule.next_run_at + grace + 600)
        skipped: ScheduleRecord = await runtime.store.get_schedule(schedule.id)
        assert skipped.last_status is not None and skipped.last_status.endswith("(the daemon was off or the Mac slept)")
        assert skipped.last_status.startswith("skipped: ") and "minutes overdue" in skipped.last_status
