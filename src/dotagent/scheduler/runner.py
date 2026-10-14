"""The scheduler: starts the runs of the schedules that are due, and the sessions of the campaigns that are, for
as long as the Dot daemon lives."""

import asyncio
import time
from zoneinfo import ZoneInfo

from dotagent.agent.manager import RunManager, thread_title
from dotagent.agent.reflection import Reflection, last_reflection, reflection_due, summary_line
from dotagent.core.errors import DotError, NotFoundError, RunConflict, StoreError
from dotagent.core.logs import get_logger
from dotagent.core.paths import failsafe_path
from dotagent.events import SYSTEM_RUN_ID
from dotagent.runtime import Runtime
from dotagent.scheduler import SCHEDULE_MISSED_EVENT, SCHEDULE_SOURCE, ScheduleMissed
from dotagent.scheduler.plans import schedule_zone
from dotagent.scheduler.timing import format_local, next_firing
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, ScheduleRecord, ThreadRecord

log = get_logger(__name__)

_UNFINISHED: frozenset[str] = frozenset({"starting", "running"})
# How many ticks in a row may fail before the scheduler's loop ends, and the daemon with it (``server/daemon.py``):
# five minutes at the default ``tick_s``. A failure that lasts so long is not passing, and schedules must not silently
# stop firing; a shorter one is waited out, so that it does not end the daemon with every run it holds.
FAILED_TICKS_TO_END: int = 20


# The status of a one-time schedule that is due while the owner's emergency stop is engaged: it waits for the release.
HELD_STATUS: str = "waiting: the emergency stop is engaged"


def late_goal(goal: str, due: float, now: float, zone: ZoneInfo) -> str:
    """The goal of a one-time schedule that fires late, after a note of when it was due and when it starts."""
    return f"(Scheduled for {format_local(due, zone)}; it starts late, at {format_local(now, zone)}.)\n\n{goal}"


def _following(record: ScheduleRecord, now: float, zone: ZoneInfo) -> float | None:
    """When the schedule fires after a firing at ``now``: the next cron time, or never again for a one-time schedule."""
    return None if record.cron is None else next_firing(record.cron, now, zone)


async def _emergency_stop_engaged() -> bool:
    """Whether the owner's emergency stop is engaged: its STOP file exists."""
    return await asyncio.to_thread(failsafe_path().exists)


class Scheduler:
    """Looks for due schedules every ``tick_s`` seconds and starts a run in a new thread for each.

    Every firing advances the schedule's next run before the run starts, so a crash can lose a firing but never repeat
    one. Firings that were missed collapse into one, and into none when they are overdue by more than the grace period.
    A schedule whose previous run is still going skips the firing. The store is the only persistent state; the
    instance remembers which schedules have a run in flight.

    The nightly reflection (item 70) fires here too, at ``[agent] reflection_time``: once its daily time has passed
    since the last reflection (or since the last attempt, or since this scheduler was made), it runs in a task of its
    own, so the schedules due at the same time wait for nothing. A night the daemon slept through is caught up once.
    """

    def __init__(self, runtime: Runtime, manager: RunManager) -> None:
        self._runtime: Runtime = runtime
        self._manager: RunManager = manager
        self._zone: ZoneInfo = runtime.schedule_zone
        self._in_flight: dict[str, str] = {}
        self._watchers: set[asyncio.Task[None]] = set()
        self._loop: asyncio.Task[None] | None = None
        self._reflected_from: float = time.time()
        self._reflecting: asyncio.Task[None] | None = None

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
        """Stop ticking and raise whatever ended the loop. A reflection under way is cancelled: its model calls could
        keep the daemon past the time launchd gives it to stop, and it records nothing until its end, so the next
        reflection reads the same runs again (item 159). Runs that were started keep going."""
        reflecting: asyncio.Task[None] | None = self._reflecting
        if reflecting is not None:
            reflecting.cancel()
        loop: asyncio.Task[None] | None = self._loop
        if loop is None:
            return
        self._loop = None
        loop.cancel()
        await asyncio.wait({loop})
        if not loop.cancelled():
            loop.result()

    async def drain(self) -> None:
        """Wait until every run that finished has been written to its schedule, and a reflection under way is over."""
        if self._watchers:
            await asyncio.wait(set(self._watchers))
        if self._reflecting is not None:
            await asyncio.wait({self._reflecting})

    def reflecting(self) -> bool:
        """Whether a reflection is under way."""
        return self._reflecting is not None and not self._reflecting.done()

    async def tick(self, now: float) -> None:
        """Fire every switched-on schedule that is due at ``now``, start the next session of every active campaign
        that is, and start the reflection when its time has come. A campaign's session is never skipped as overdue:
        the campaign goes on where it stopped. While the owner's emergency stop is engaged, no campaign starts a
        session (each waits for the release; the stop is looked at before each one) and no schedule fires
        (:meth:`_fire`). The three are done each on its own: one that fails is logged where it failed and keeps the
        others from nothing, and the failures are raised together at the end, as an ``ExceptionGroup``."""
        failures: list[Exception] = []
        for part in (self._fire_due, self._start_due_sessions, self._reflect_if_due):
            try:
                await part(now)
            except Exception as exc:
                log.exception("scheduler_part_failed", part=part.__name__)
                failures.append(exc)
        if failures:
            raise ExceptionGroup("a part of the scheduler's tick failed", failures)

    async def _fire_due(self, now: float) -> None:
        """Fire every switched-on schedule that is due at ``now``, each on its own (:meth:`_fire_alone`)."""
        for record in await self._runtime.store.due_schedules(now):
            await self._fire_alone(record, now)

    async def _start_due_sessions(self, now: float) -> None:
        """Start the next session of every active campaign that is due at ``now``, while the emergency stop is not
        engaged."""
        for campaign in await self._runtime.store.due_campaigns(now):
            if await _emergency_stop_engaged():
                break
            await self._start_session(campaign.id)

    async def _reflect_if_due(self, now: float) -> None:
        """Start the reflection in a task when its daily time has passed since the last one (or the last attempt)."""
        if self.reflecting():
            return
        last: Reflection | None = await last_reflection(self._runtime.store)
        anchor: float = max(self._reflected_from, last.at if last is not None else 0.0)
        if not reflection_due(self._runtime.config.agent.reflection_time, anchor, now, self._zone):
            return
        self._reflected_from = now
        self._reflecting = asyncio.create_task(self._reflect(now), name="reflection")

    async def _reflect(self, now: float) -> None:
        """Task body of a reflection: its outcome is logged; a failure is logged with what failed and the next attempt
        is the next daily time."""
        try:
            reflection: Reflection = await self._manager.reflect(now)
        except DotError as exc:
            log.error("reflection_failed", error=exc.message, context=exc.context)
        except Exception:
            log.exception("reflection crashed")
        else:
            log.info("reflection_fired", summary=summary_line(reflection))

    async def _tick_forever(self) -> None:
        """Tick every ``tick_s`` seconds. A tick that failed is tried again at the next one; :data:`FAILED_TICKS_TO_END`
        failed ticks in a row end the loop with the last one's failures."""
        tick_s: float = self._runtime.config.scheduler.tick_s
        failed: int = 0
        while True:
            try:
                await self.tick(time.time())
            except ExceptionGroup:
                failed += 1
                if failed >= FAILED_TICKS_TO_END:
                    raise
                log.warning("scheduler_tick_failed", failed_in_a_row=failed, ends_at=FAILED_TICKS_TO_END)
            else:
                failed = 0
            await asyncio.sleep(tick_s)

    async def _fire_alone(self, record: ScheduleRecord, now: float) -> None:
        """Fire one schedule as the store has it now: one deleted, switched off or moved since the tick listed it does
        not fire. One that cannot fire (its time zone or its times cannot be read, the store failed for it) keeps the
        reason as its status and is tried again at the next tick, and the other schedules and the daemon go on (item
        116)."""
        try:
            current: ScheduleRecord | None = await self._still_due(record.id, now)
            if current is not None:
                await self._fire(current, now)
        except DotError as exc:
            log.error("schedule_fire_failed", schedule_id=record.id, error=exc.message, context=exc.context)
            await self._runtime.store.set_schedule_status(record.id, f"failed to fire: {exc.message}")
        except Exception as exc:
            log.exception("schedule_fire_crashed", schedule_id=record.id)
            await self._runtime.store.set_schedule_status(record.id, f"failed to fire: {type(exc).__name__}")

    async def _still_due(self, schedule_id: str, now: float) -> ScheduleRecord | None:
        """The schedule as the store has it now, when it is still switched on and due at ``now``; ``None`` when it was
        deleted, switched off or moved since the tick listed it."""
        try:
            record: ScheduleRecord = await self._runtime.store.get_schedule(schedule_id)
        except NotFoundError:
            return None
        if not record.enabled or record.next_run_at is None or record.next_run_at > now:
            return None
        return record

    async def _fire(self, record: ScheduleRecord, now: float) -> None:
        """Start the run of a due schedule; a firing the emergency stop, a run still going or a long absence of the
        daemon keeps from starting is skipped to the schedule's next time, with the reason as its status. A one-time
        schedule has no next time and is never dropped: while the emergency stop is engaged it waits, still due, for
        the release, and one overdue fires late, its goal saying when it was due (:func:`late_goal`). An emergency
        stop engaged while the run was being started ends it as one. A firing missed where nobody saw it, one overdue
        or one whose run could not start, is told (:meth:`_tell_missed`)."""
        store: Store = self._runtime.store
        if record.next_run_at is None:
            raise StoreError("a due schedule has no next run", {"schedule_id": record.id})
        zone: ZoneInfo = await schedule_zone(record.options, self._zone)
        following: float | None = _following(record, now, zone)
        overdue_s: float = now - record.next_run_at
        late: bool = overdue_s > self._runtime.config.scheduler.misfire_grace_s
        one_time: bool = record.cron is None
        if await _emergency_stop_engaged():
            if one_time:
                if record.last_status != HELD_STATUS:
                    log.warning("schedule_held_emergency_stop", schedule_id=record.id)
                    await store.set_schedule_status(record.id, HELD_STATUS)
                return
            log.warning("schedule_skipped_emergency_stop", schedule_id=record.id)
            await store.record_schedule_skip(record.id, following, "skipped: the emergency stop is engaged")
            return
        if record.id in self._in_flight:
            log.warning("schedule_skipped_running", schedule_id=record.id, run_id=self._in_flight[record.id])
            await store.record_schedule_skip(record.id, following, "skipped: the previous run was still going")
            return
        if late and not one_time:
            log.warning("schedule_skipped_overdue", schedule_id=record.id, overdue_s=round(overdue_s))
            overdue: str = f"skipped: {round(overdue_s / 60)} minutes overdue (the daemon was not running)"
            await store.record_schedule_skip(record.id, following, overdue)
            await self._tell_missed(record, overdue)
            return
        title: str = thread_title(f"{record.name} · {format_local(now, zone)}")
        thread: ThreadRecord = await store.create_thread(title, SCHEDULE_SOURCE)
        await store.record_schedule_fire(record.id, now, following, thread.id, "starting")
        if late:
            log.warning("schedule_fired_late", schedule_id=record.id, overdue_s=round(overdue_s))
        try:
            run: RunRecord = await self._manager.start(
                late_goal(record.goal, record.next_run_at, now, zone) if late else record.goal,
                record.mode,
                record.options.model,
                thread.id,
                SCHEDULE_SOURCE,
                None,
                record.options.effort,
                "standard",
                record.options.approvals,
                record.options.goal_mode,
                (),
            )
        except DotError as exc:
            log.error("schedule_start_failed", schedule_id=record.id, error=exc.message, context=exc.context)
            failed: str = f"failed to start: {exc.message}"
            await store.set_schedule_status(record.id, failed)
            try:
                await store.delete_threads([thread.id])
            except DotError as kept:
                # The thread of a run whose failed start the store did not take yet stays; the missed firing is told.
                log.error(
                    "schedule_thread_not_deleted", schedule_id=record.id, error=kept.message, context=kept.context
                )
            await self._tell_missed(record, failed)
            return
        if await _emergency_stop_engaged():
            log.warning("schedule_run_stopped_emergency_stop", schedule_id=record.id, run_id=run.id)
            self._manager.end_by_emergency_stop(run.id)
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

    async def _tell_missed(self, record: ScheduleRecord, status: str) -> None:
        """Tell the owner that the schedule ``record`` (as it was before this firing) missed it, with ``status`` saying
        why; not again when its status said so already, so a schedule that keeps failing the same way is told once."""
        if record.last_status == status:
            return
        missed: ScheduleMissed = ScheduleMissed(schedule_id=record.id, name=record.name, status=status)
        await self._runtime.bus.emit(SYSTEM_RUN_ID, SCHEDULE_MISSED_EVENT, missed.model_dump(), False)

    async def _start_session(self, campaign_id: str) -> None:
        try:
            await self._manager.start_session(campaign_id)
        except DotError as exc:
            log.error("campaign_session_start_failed", campaign_id=campaign_id, error=exc.message, context=exc.context)

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
