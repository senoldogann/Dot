"""The scheduler: starts the runs of the schedules that are due, and the sessions of the campaigns that are, for
as long as the Dot daemon lives."""

import asyncio
import time
from zoneinfo import ZoneInfo

from dotagent.agent.learning_health import (
    LEARNED,
    SKIPPED,
    LearningOutcome,
    check_learning,
    failed_with,
    quiet_limit,
    record_learning,
)
from dotagent.agent.manager import RunManager, thread_title
from dotagent.agent.reflection import last_reflection, record_reflection_failure, reflection_due
from dotagent.agent.reflection_record import REFLECTION_OFF, Reflection, summary_line
from dotagent.config import SchedulerConfig
from dotagent.core.errors import DotError, NotFoundError, RunConflict, StoreError
from dotagent.core.logs import get_logger
from dotagent.core.paths import failsafe_path
from dotagent.events import SYSTEM_RUN_ID
from dotagent.harness.probe import probe_apart
from dotagent.harness.step import run_step
from dotagent.runtime import Runtime
from dotagent.scheduler import (
    SCHEDULE_MISSED_EVENT,
    SCHEDULE_SOURCE,
    SCHEDULE_STOPPED_EVENT,
    ScheduleMissed,
    ScheduleStopped,
    stop_limit,
    switches_off,
)
from dotagent.scheduler.failures import (
    FiringFailure,
    firing_failure,
    record_firing_failure,
    record_firing_started,
    retry_after,
)
from dotagent.scheduler.history import NO_DIGEST, Digest, firing_opening, history_digest
from dotagent.scheduler.plans import schedule_zone
from dotagent.scheduler.timing import format_local, next_firing
from dotagent.store.db import Store
from dotagent.store.models import HarnessStep, RunRecord, ScheduleRecord, ThreadRecord

log = get_logger(__name__)

_UNFINISHED: frozenset[str] = frozenset({"starting", "running"})
# How many ticks in a row may fail before the scheduler's loop ends, and the daemon with it (``server/daemon.py``):
# five minutes at the default ``tick_s``. A failure that lasts so long is not passing, and schedules must not silently
# stop firing; a shorter one is waited out, so that it does not end the daemon with every run it holds.
FAILED_TICKS_TO_END: int = 20


# The status of a one-time schedule that is due while the owner's emergency stop is engaged: it waits for the release.
HELD_STATUS: str = "waiting: the emergency stop is engaged"


def late_goal(goal: str, due: float, now: float, zone: ZoneInfo) -> str:
    """The goal of a one-time schedule that fires late, after a note of when it was due and when it starts: what its
    run reads first, while the run keeps the goal alone (:meth:`RunManager.start_firing`)."""
    return f"(Scheduled for {format_local(due, zone)}; it starts late, at {format_local(now, zone)}.)\n\n{goal}"


def by_hand_goal(goal: str, now: float, zone: ZoneInfo) -> str:
    """The goal of a run the owner started by hand (item 455), after a note that it did not start at its time: what its
    run reads first, while the run keeps the goal alone, as a late firing's does (:func:`late_goal`)."""
    return f"(Started by hand at {format_local(now, zone)}, not at its time.)\n\n{goal}"


def _following(record: ScheduleRecord, now: float, zone: ZoneInfo) -> float | None:
    """When the schedule fires after a firing at ``now``: the next cron time, or never again for a one-time schedule."""
    return None if record.cron is None else next_firing(record.cron, now, zone)


async def _emergency_stop_engaged() -> bool:
    """Whether the owner's emergency stop is engaged: its STOP file exists."""
    return await asyncio.to_thread(failsafe_path().exists)


class Scheduler:
    """Looks for due schedules every ``tick_s`` seconds and starts a run for each, in the chat a firing goes in
    (:meth:`_firing_chat`).

    Every firing advances the schedule's next run before the run starts, so a crash can lose a firing but never repeat
    one. Firings that were missed collapse into one, and into none when they are overdue by more than the grace period.
    A repeating schedule whose previous run is still going skips the firing; a one-time one waits for that run, still
    due. The store is the only persistent state; the instance remembers which schedules have a run in flight.

    The nightly reflection (item 70) fires here too, at ``[agent] reflection_time``: once its daily time has passed
    since the last reflection (or since the last attempt, or since this scheduler was made), it runs in a task of its
    own, so the schedules due at the same time wait for nothing. A night the daemon slept through is caught up once.
    """

    def __init__(self, runtime: Runtime, manager: RunManager) -> None:
        self._runtime: Runtime = runtime
        self._manager: RunManager = manager
        self._zone: ZoneInfo = runtime.schedule_zone
        self._in_flight: dict[str, str] = {}
        # The schedules whose run is being started, by a tick or by hand (item 455), until it is in flight.
        self._starting: set[str] = set()
        self._watchers: set[asyncio.Task[None]] = set()
        self._loop: asyncio.Task[None] | None = None
        self._reflected_from: float = time.time()
        self._reflecting: asyncio.Task[None] | None = None
        # The step of the harness (item 391), timed like the reflection.
        self._stepped_from: float = time.time()
        self._stepping: asyncio.Task[None] | None = None

    async def start(self) -> asyncio.Task[None]:
        """Begin ticking in the background; what an earlier process left unfinished (a schedule starting or running a
        run, whose watch went with that process) is marked as interrupted and logged.

        Returns the task of the loop, so the caller can notice when it ends.
        """
        if self._loop is not None:
            raise RunConflict("the scheduler is already running", {})
        store: Store = self._runtime.store
        for record in await store.list_schedules():
            if record.last_status in _UNFINISHED:
                await store.set_schedule_status(record.id, "interrupted")
                log.warning("schedule_interrupted", schedule_id=record.id, status=record.last_status)
                if record.last_status == "starting" and record.cron is None and record.last_run_at is not None:
                    await self._due_again_unless_started(record, record.last_run_at)
        self._loop = asyncio.create_task(self._tick_forever(), name="scheduler")
        return self._loop

    async def _due_again_unless_started(self, record: ScheduleRecord, fired_at: float) -> None:
        """A one-time firing an earlier process recorded at ``fired_at`` and died before its run was under way (item
        386): the firing had switched the schedule off for good, so it is due again, as one whose start failed is,
        unless a run of the firing was made after all, which would then run twice."""
        store: Store = self._runtime.store
        if record.thread_id is not None:
            latest: list[RunRecord] = await store.list_runs(1, record.thread_id)
            if latest and latest[0].source == SCHEDULE_SOURCE and latest[0].created_at >= fired_at:
                return
        await store.keep_schedule_due(record.id, fired_at, fired_at)
        log.warning("schedule_due_again", schedule_id=record.id, fired_at=fired_at)

    def running(self) -> bool:
        return self._loop is not None and not self._loop.done()

    async def stop(self) -> None:
        """Stop ticking and raise whatever ended the loop. A reflection under way is cancelled: its model calls could
        keep the daemon past the time launchd gives it to stop, and it records nothing until its end, so the next
        reflection reads the same runs again (item 159). Runs that were started keep going."""
        reflecting: asyncio.Task[None] | None = self._reflecting
        if reflecting is not None:
            reflecting.cancel()
        stepping: asyncio.Task[None] | None = self._stepping
        if stepping is not None:
            stepping.cancel()
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
        if self._stepping is not None:
            await asyncio.wait({self._stepping})

    def reflecting(self) -> bool:
        """Whether a reflection is under way."""
        return self._reflecting is not None and not self._reflecting.done()

    async def tick(self, now: float) -> None:
        """Fire every switched-on schedule that is due at ``now``, start the next session of every active campaign
        that is, and start the reflection when its time has come. A campaign's session is never skipped as overdue:
        the campaign goes on where it stopped. While the owner's emergency stop is engaged, no campaign starts a
        session (each waits for the release; the stop is looked at before each one) and no schedule fires
        (:meth:`_fire`). Learning that has stopped by the clock is told to the owner (:meth:`_check_learning`). The
        parts are done each on its own: one that fails is logged where it failed and keeps the others from nothing, and
        the failures are raised together at the end, as an ``ExceptionGroup``."""
        failures: list[Exception] = []
        for part in (
            self._fire_due,
            self._start_due_sessions,
            self._reflect_if_due,
            self._step_if_due,
            self._check_learning,
        ):
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

    def stepping(self) -> bool:
        """Whether a step of the harness is under way."""
        return self._stepping is not None and not self._stepping.done()

    async def _step_if_due(self, now: float) -> None:
        """Start the step of the harness (item 391) in a task when its daily time has passed since the last one (or the
        last attempt); never while one is under way, and never with ``[harness] step_time`` off."""
        if self.stepping():
            return
        step_time: str = self._runtime.config.harness.step_time
        if step_time == REFLECTION_OFF:
            return
        last: list[HarnessStep] = await self._runtime.store.list_harness_steps(1)
        anchor: float = max(self._stepped_from, last[0].started_at if last else 0.0)
        if not reflection_due(step_time, anchor, now, self._zone):
            return
        self._stepped_from = now
        self._stepping = asyncio.create_task(self._step(now), name="harness-step")

    async def _step(self, now: float) -> None:
        """Task body of a step of the harness, and the one place its errors end: its outcome is logged and kept in the
        record of how learning fares, as the reflection's is; the probes run in processes of their own."""
        try:
            step: HarnessStep = await run_step(self._runtime, now, None, probe_apart, False)
        except DotError as exc:
            log.error("harness_step_failed", error=exc.message, context=exc.context)
            outcome: LearningOutcome = failed_with(exc.message)
        except Exception as exc:
            log.exception("harness_step_crashed")
            outcome = failed_with(f"{type(exc).__name__}: {exc}")
        else:
            log.info("harness_step_fired", step=step.id, state=step.state)
            if step.state == "skipped":
                outcome = SKIPPED
            elif step.state == "failed":
                outcome = failed_with(step.error or "the step failed")
            else:
                outcome = LEARNED
        quiet_s: float | None = quiet_limit(self._runtime.config.agent.reflection_time)
        try:
            await record_learning(self._runtime.store, self._runtime.bus, "harness", now, outcome, quiet_s)
        except StoreError as error:
            log.error("learning_not_recorded", work="harness", state=outcome.state, error=error.message)

    async def _check_learning(self, now: float) -> None:
        """Tell the owner when no reflection went well for too long, though none ended since
        (:func:`dotagent.agent.learning_health.check_learning`). A record that cannot be read is logged: it keeps the
        scheduler from nothing."""
        try:
            await check_learning(
                self._runtime.store, self._runtime.bus, now, quiet_limit(self._runtime.config.agent.reflection_time)
            )
        except StoreError as error:
            log.error("learning_not_checked", error=error.message)

    async def _reflect(self, now: float) -> None:
        """Task body of a reflection, and the one place its errors end (review O25): its outcome is logged and kept in
        the record of how learning fares (:func:`dotagent.agent.learning_health.record_learning`, which tells the owner
        when learning has stopped); a failure is also kept with what failed
        (:func:`dotagent.agent.reflection.record_reflection_failure`: the Activity list and ``dotx stats`` show it until
        a reflection is done), and the next attempt is the next daily time. A store that cannot keep them is logged."""
        try:
            reflection: Reflection = await self._manager.reflect(now)
        except DotError as exc:
            log.error("reflection_failed", error=exc.message, context=exc.context)
            outcome: LearningOutcome = failed_with(exc.message)
        except Exception as exc:
            log.exception("reflection_crashed")
            outcome = failed_with(f"{type(exc).__name__}: {exc}")
        else:
            log.info("reflection_fired", summary=summary_line(reflection))
            outcome = LEARNED
        quiet_s: float | None = quiet_limit(self._runtime.config.agent.reflection_time)
        try:
            if outcome.error is not None:
                await record_reflection_failure(self._runtime.store, now, outcome.error)
            await record_learning(self._runtime.store, self._runtime.bus, "reflection", now, outcome, quiet_s)
        except StoreError as error:
            log.error("learning_not_recorded", work="reflection", state=outcome.state, error=error.message)

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
        reason as its status, counts it (:func:`dotagent.scheduler.failures.record_firing_failure`) and is tried again
        at the next tick, and the other schedules and the daemon go on (item 116)."""
        try:
            current: ScheduleRecord | None = await self._still_due(record.id, now)
            if current is not None:
                await self._fire(current, now)
        except DotError as exc:
            log.error("schedule_fire_failed", schedule_id=record.id, error=exc.message, context=exc.context)
            failed: str = f"failed to fire: {exc.message}"
            await self._runtime.store.set_schedule_status(record.id, failed)
            await record_firing_failure(self._runtime.store, record.id, now, failed)
        except Exception as exc:
            log.exception("schedule_fire_crashed", schedule_id=record.id)
            crashed: str = f"failed to fire: {type(exc).__name__}"
            await self._runtime.store.set_schedule_status(record.id, crashed)
            await record_firing_failure(self._runtime.store, record.id, now, crashed)

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
        schedule has no next time and is never dropped: while the emergency stop is engaged, or a run of it started by
        hand works, it waits, still due, and one overdue fires late, its opening saying when it was due
        (:func:`late_goal`). An emergency stop engaged while the run was being started ends it as one. A firing missed
        where nobody saw it, one overdue or one whose run could not start, is told (:meth:`_tell_missed`)."""
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
        if one_time:
            # The wait is that of a try of this time again: a time given since the last failure fires when it comes.
            failure: FiringFailure | None = await firing_failure(store, record.id)
            if failure is not None and failure.last_failure_at >= record.next_run_at and now < retry_after(failure):
                return
        if record.id in self._in_flight or record.id in self._starting:
            if one_time:
                # Only a run by hand is at work while a one-time schedule is still due (item 455): the schedule waits,
                # due, and fires once that run has ended, as it waits for the release of the emergency stop.
                return
            log.warning("schedule_skipped_running", schedule_id=record.id, run_id=self._in_flight.get(record.id))
            await store.record_schedule_skip(record.id, following, "skipped: the previous run was still going")
            return
        if late and not one_time:
            log.warning("schedule_skipped_overdue", schedule_id=record.id, overdue_s=round(overdue_s))
            overdue: str = f"skipped: {round(overdue_s / 60)} minutes overdue (the daemon was not running)"
            await store.record_schedule_skip(record.id, following, overdue)
            await self._tell_missed(record, overdue)
            return
        self._starting.add(record.id)
        try:
            await self._start_due(record, record.next_run_at, now, zone, following, late, overdue_s)
        finally:
            self._starting.discard(record.id)

    async def _start_due(
        self,
        record: ScheduleRecord,
        due: float,
        now: float,
        zone: ZoneInfo,
        following: float | None,
        late: bool,
        overdue_s: float,
    ) -> None:
        """Start the run of the firing of ``record`` that was due at ``due``, once :meth:`_fire` let it: the firing is
        written (its next time, its chat) before the run starts, so a crash can lose a firing but never repeat one."""
        store: Store = self._runtime.store
        one_time: bool = record.cron is None
        same_chat, digest = await self._history(record, zone)
        title: str = thread_title(f"{record.name} · {format_local(now, zone)}")
        thread_id, made = await self._firing_chat(record, same_chat, title)
        await store.record_schedule_fire(record.id, now, following, thread_id, "starting")
        if late:
            log.warning("schedule_fired_late", schedule_id=record.id, overdue_s=round(overdue_s))
        told: str = late_goal(record.goal, due, now, zone) if late else record.goal
        carried: tuple[str, ...] = await self._carried(digest, made, thread_id)
        started: RunRecord | None = None
        try:
            started = await self._start(record, firing_opening(told, digest), thread_id, carried)
        except DotError as exc:
            log.error("schedule_start_failed", schedule_id=record.id, error=exc.message, context=exc.context)
            failed: str = f"failed to start: {exc.message}"
            await store.set_schedule_status(record.id, failed)
            await record_firing_failure(store, record.id, now, failed)
            await self._tell_missed(record, failed)
            return
        finally:
            # A chat made for the firing goes with its run that did not start, however the start failed; the chat it
            # went on in stays.
            if started is None and made:
                await self._drop_chat(record.id, thread_id)
            # A one-time schedule is never dropped: one whose run did not start, with Dot's error or with a crash, is
            # due again, and is tried again after a wait (``retry_after``; item 351: the firing above had switched it
            # off for good).
            if started is None and one_time:
                await store.keep_schedule_due(record.id, due, now)
        run: RunRecord = started
        await self._track(record.id, run)
        try:
            await store.set_schedule_status(record.id, "running")
            await record_firing_started(store, record.id)
        except DotError as exc:
            # The run started all the same; its watch writes how it ended.
            log.error(
                "schedule_status_not_written",
                schedule_id=record.id,
                run_id=run.id,
                error=exc.message,
                context=exc.context,
            )
        log.info(
            "schedule_fired",
            schedule_id=record.id,
            run_id=run.id,
            thread_id=thread_id,
            same_chat=not made,
            history=len(digest.run_ids),
            mode=run.mode,
            model=run.model,
            approvals=run.approvals,
            goal_mode=run.goal_mode,
        )

    async def fire_by_hand(self, schedule_id: str, now: float) -> RunRecord:
        """Start a run of the schedule ``schedule_id`` now, because the owner asked (item 455): switched on or off, due
        or not. Nothing of when it fires next, nor of whether it is switched on, changes, so a one-time schedule is
        still due at its time. The run goes as a firing goes: unattended, with the schedule's own choices (a yolo
        schedule's run is a yolo run), the digest of its earlier runs, in a chat of its own whose title says it was
        started by hand (or in the schedule's chat, with ``same_chat``), its first message saying so. It does not start
        while the owner's emergency stop is engaged, nor while a run of the schedule is at work or being started
        (:class:`RunConflict`). A run that cannot start is the caller's error, the chat made for it gone, and it is no
        missed firing: the owner is looking."""
        store: Store = self._runtime.store
        record: ScheduleRecord = await store.get_schedule(schedule_id)
        if await _emergency_stop_engaged():
            raise RunConflict("no schedule starts while the emergency stop is engaged", {"schedule_id": schedule_id})
        if schedule_id in self._in_flight or schedule_id in self._starting:
            raise RunConflict(
                "a run of this schedule is already working",
                {"schedule_id": schedule_id, "run_id": self._in_flight.get(schedule_id)},
            )
        self._starting.add(schedule_id)
        try:
            zone: ZoneInfo = await schedule_zone(record.options, self._zone)
            same_chat, digest = await self._history(record, zone)
            title: str = thread_title(f"{record.name} · {format_local(now, zone)} · by hand")
            thread_id, made = await self._firing_chat(record, same_chat, title)
            carried: tuple[str, ...] = await self._carried(digest, made, thread_id)
            started: RunRecord | None = None
            try:
                started = await self._start(
                    record, firing_opening(by_hand_goal(record.goal, now, zone), digest), thread_id, carried
                )
            finally:
                if started is None and made:
                    await self._drop_chat(record.id, thread_id)
            run: RunRecord = started
            await self._track(record.id, run)
            try:
                await store.record_schedule_hand_run(record.id, now, thread_id)
            except DotError as exc:
                # The run started all the same; its watch writes how it ended.
                log.error(
                    "schedule_status_not_written",
                    schedule_id=record.id,
                    run_id=run.id,
                    error=exc.message,
                    context=exc.context,
                )
        finally:
            self._starting.discard(schedule_id)
        log.info(
            "schedule_fired_by_hand",
            schedule_id=record.id,
            run_id=run.id,
            thread_id=thread_id,
            same_chat=not made,
            history=len(digest.run_ids),
            approvals=run.approvals,
        )
        return run

    async def _history(self, record: ScheduleRecord, zone: ZoneInfo) -> tuple[bool, Digest]:
        """Whether a firing of ``record`` goes on in the chat of its last one, and the digest of its earlier runs'
        answers its run reads in a chat of its own; in the same chat it reads their conversation instead."""
        store: Store = self._runtime.store
        config: SchedulerConfig = self._runtime.config.scheduler
        same_chat: bool = config.same_chat if record.options.same_chat is None else record.options.same_chat
        kept: int = config.history_runs if record.options.history_runs is None else record.options.history_runs
        digest: Digest = (
            NO_DIGEST
            if same_chat or kept == 0
            else history_digest(await store.schedule_runs(record.id, SCHEDULE_SOURCE, kept), zone, config.history_chars)
        )
        return same_chat, digest

    async def _carried(self, digest: Digest, made: bool, thread_id: str) -> tuple[str, ...]:
        """The earlier runs whose words a firing's run reads: the digest's in a chat made for it, else that chat's last
        run."""
        if made:
            return digest.run_ids
        return tuple(run.id for run in await self._runtime.store.list_runs(1, thread_id))

    async def _start(self, record: ScheduleRecord, opening: str, thread_id: str, carried: tuple[str, ...]) -> RunRecord:
        """Start the run of a firing of ``record`` in the chat ``thread_id`` with the schedule's own choices."""
        return await self._manager.start_firing(
            record.goal,
            opening,
            record.mode,
            record.options.model,
            thread_id,
            record.options.effort,
            "standard" if record.options.speed is None else record.options.speed,
            record.options.approvals,
            record.options.goal_mode,
            carried,
        )

    async def _track(self, schedule_id: str, run: RunRecord) -> None:
        """Keep ``run`` as the schedule's run at work until it ends; its watch, which starts first, frees the schedule
        and writes the end even when the store fails a write after this. An emergency stop engaged while it was being
        started ends it as one."""
        if await _emergency_stop_engaged():
            log.warning("schedule_run_stopped_emergency_stop", schedule_id=schedule_id, run_id=run.id)
            self._manager.end_by_emergency_stop(run.id)
        self._in_flight[schedule_id] = run.id
        watcher: asyncio.Task[None] = asyncio.create_task(self._watch(schedule_id, run.id), name=f"watch-{run.id}")
        self._watchers.add(watcher)
        watcher.add_done_callback(self._watcher_done)

    async def _firing_chat(self, record: ScheduleRecord, same_chat: bool, title: str) -> tuple[str, bool]:
        """The chat a firing's run goes in, and whether it was made for it: with ``same_chat``, the chat of the
        schedule's last firing while it is there; otherwise, and when the owner deleted that chat, a new one titled
        ``title``. Either is marked as the schedule's, whose next firings read its runs (item 257)."""
        store: Store = self._runtime.store
        if same_chat and record.thread_id is not None:
            try:
                await store.get_thread(record.thread_id)
            except NotFoundError:
                log.info("schedule_chat_deleted", schedule_id=record.id, thread_id=record.thread_id)
            else:
                await store.link_schedule_thread(record.thread_id, record.id)
                return record.thread_id, False
        thread: ThreadRecord = await store.create_thread(title, SCHEDULE_SOURCE)
        await store.link_schedule_thread(thread.id, record.id)
        return thread.id, True

    async def _drop_chat(self, schedule_id: str, thread_id: str) -> None:
        """Delete the chat made for a firing whose run did not start. One the store does not take stays, logged: the
        missed firing is told all the same."""
        try:
            await self._runtime.store.delete_threads([thread_id])
        except DotError as left:
            log.error("schedule_thread_not_deleted", schedule_id=schedule_id, error=left.message, context=left.context)

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
        """Write the outcome of a run to its schedule once the run is over, with its row of failed runs (item 458); a
        repeating schedule whose runs failed so many times in a row is switched off, and the owner told once."""
        try:
            finished: RunRecord = await self._manager.wait(run_id)
            failed_in_row: int | None = await self._runtime.store.record_schedule_outcome(schedule_id, finished.status)
            if finished.status == "failed" and failed_in_row is not None:
                await self._stop_if_failing(schedule_id, failed_in_row)
        finally:
            self._in_flight.pop(schedule_id, None)

    async def _stop_if_failing(self, schedule_id: str, failed_in_row: int) -> None:
        """Switch the schedule off when its runs failed its limit of times in a row, and tell the owner; a schedule
        switched off or deleted meanwhile is left as it is."""
        store: Store = self._runtime.store
        try:
            record: ScheduleRecord = await store.get_schedule(schedule_id)
        except NotFoundError:
            return
        limit: int = stop_limit(record.options.stop_after_failures, self._runtime.config.scheduler.stop_after_failures)
        if not switches_off(record.cron is not None, record.enabled, failed_in_row, limit):
            return
        status: str = f"switched off: its last {failed_in_row} runs failed"
        if not await store.switch_off_schedule(schedule_id, status):
            return
        log.warning("schedule_switched_off", schedule_id=schedule_id, failures=failed_in_row, limit=limit)
        stopped: ScheduleStopped = ScheduleStopped(schedule_id=schedule_id, name=record.name, failures=failed_in_row)
        await self._runtime.bus.emit(SYSTEM_RUN_ID, SCHEDULE_STOPPED_EVENT, stopped.model_dump(), False)

    def _watcher_done(self, watcher: asyncio.Task[None]) -> None:
        self._watchers.discard(watcher)
        if watcher.cancelled():
            return
        error: BaseException | None = watcher.exception()
        if error is not None:
            log.error("schedule_watch_failed", task=watcher.get_name(), exc_info=error)
