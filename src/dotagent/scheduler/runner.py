"""The scheduler: starts the runs of the schedules that are due, for as long as the Dot daemon lives."""

import asyncio
import time
from zoneinfo import ZoneInfo

from dotagent.agent.manager import RunManager, thread_title
from dotagent.core.errors import DotError, RunConflict, StoreError
from dotagent.core.logs import get_logger
from dotagent.runtime import Runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.scheduler.plans import schedule_zone
from dotagent.scheduler.timing import format_local, next_firing
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, ScheduleRecord, ThreadRecord

log = get_logger(__name__)

_UNFINISHED: frozenset[str] = frozenset({"starting", "running"})


def _following(record: ScheduleRecord, now: float, zone: ZoneInfo) -> float | None:
    """When the schedule fires after a firing at ``now``: the next cron time, or never again for a one-time schedule."""
    return None if record.cron is None else next_firing(record.cron, now, zone)


class Scheduler:
    """Looks for due schedules every ``tick_s`` seconds and starts a run in a new thread for each.

    Every firing advances the schedule's next run before the run starts, so a crash can lose a firing but never repeat
    one. Firings that were missed collapse into one, and into none when they are overdue by more than the grace period.
    A schedule whose previous run is still going skips the firing. The store is the only persistent state; the
    instance remembers which schedules have a run in flight.
    """

    def __init__(self, runtime: Runtime, manager: RunManager) -> None:
        self._runtime: Runtime = runtime
        self._manager: RunManager = manager
        self._zone: ZoneInfo = runtime.schedule_zone
        self._in_flight: dict[str, str] = {}
        self._watchers: set[asyncio.Task[None]] = set()
        self._loop: asyncio.Task[None] | None = None

    async def start(self) -> asyncio.Task[None]:
        """Begin ticking in the background; what an earlier process left unfinished is marked as interrupted.

        Returns the task of the loop, so the caller can notice when it ends.
        """
        if self._loop is not None:
            raise RunConflict("the scheduler is already running", {})
        store: Store = self._runtime.store
        for record in await store.list_schedules():
            if record.last_status in _UNFINISHED:
                await store.set_schedule_status(record.id, "interrupted")
        self._loop = asyncio.create_task(self._tick_forever(), name="scheduler")
        return self._loop

    def running(self) -> bool:
        return self._loop is not None and not self._loop.done()

    async def stop(self) -> None:
        """Stop ticking and raise whatever ended the loop. Runs that were started keep going."""
        loop: asyncio.Task[None] | None = self._loop
        if loop is None:
            return
        self._loop = None
        loop.cancel()
        await asyncio.wait({loop})
        if not loop.cancelled():
            loop.result()

    async def drain(self) -> None:
        """Wait until every run that finished has been written to its schedule."""
        if self._watchers:
            await asyncio.wait(set(self._watchers))

    async def tick(self, now: float) -> None:
        """Fire every switched-on schedule that is due at ``now``."""
        for record in await self._runtime.store.due_schedules(now):
            await self._fire(record, now)

    async def _tick_forever(self) -> None:
        tick_s: float = self._runtime.config.scheduler.tick_s
        while True:
            await self.tick(time.time())
            await asyncio.sleep(tick_s)

    async def _fire(self, record: ScheduleRecord, now: float) -> None:
        store: Store = self._runtime.store
        if record.next_run_at is None:
            raise StoreError("a due schedule has no next run", {"schedule_id": record.id})
        zone: ZoneInfo = await schedule_zone(record.options, self._zone)
        following: float | None = _following(record, now, zone)
        overdue_s: float = now - record.next_run_at
        if record.id in self._in_flight:
            log.warning("schedule_skipped_running", schedule_id=record.id, run_id=self._in_flight[record.id])
            await store.record_schedule_skip(record.id, following, "skipped: the previous run was still going")
            return
        if overdue_s > self._runtime.config.scheduler.misfire_grace_s:
            log.warning("schedule_skipped_overdue", schedule_id=record.id, overdue_s=round(overdue_s))
            await store.record_schedule_skip(
                record.id, following, f"skipped: {round(overdue_s / 60)} minutes overdue (the daemon was not running)"
            )
            return
        title: str = thread_title(f"{record.name} · {format_local(now, zone)}")
        thread: ThreadRecord = await store.create_thread(title, SCHEDULE_SOURCE)
        await store.record_schedule_fire(record.id, now, following, thread.id, "starting")
        try:
            run: RunRecord = await self._manager.start(
                record.goal,
                record.mode,
                record.options.model,
                thread.id,
                SCHEDULE_SOURCE,
                None,
                record.options.effort,
                record.options.approvals,
                record.options.goal_mode,
                (),
            )
        except DotError as exc:
            log.error("schedule_start_failed", schedule_id=record.id, error=exc.message, context=exc.context)
            await store.set_schedule_status(record.id, f"failed to start: {exc.message}")
            await store.delete_thread(thread.id)
            return
        self._in_flight[record.id] = run.id
        await store.set_schedule_status(record.id, "running")
        log.info(
            "schedule_fired",
            schedule_id=record.id,
            run_id=run.id,
            thread_id=thread.id,
            mode=run.mode,
            model=run.model,
            approvals=run.approvals,
            goal_mode=run.goal_mode,
        )
        watcher: asyncio.Task[None] = asyncio.create_task(self._watch(record.id, run.id), name=f"watch-{run.id}")
        self._watchers.add(watcher)
        watcher.add_done_callback(self._watcher_done)

    async def _watch(self, schedule_id: str, run_id: str) -> None:
        """Write the outcome of a run to its schedule once the run is over."""
        try:
            finished: RunRecord = await self._manager.wait(run_id)
            await self._runtime.store.set_schedule_status(schedule_id, finished.status)
        finally:
            self._in_flight.pop(schedule_id, None)

    def _watcher_done(self, watcher: asyncio.Task[None]) -> None:
        self._watchers.discard(watcher)
        if watcher.cancelled():
            return
        error: BaseException | None = watcher.exception()
        if error is not None:
            log.error("schedule_watch_failed", task=watcher.get_name(), exc_info=error)
