"""The scheduler: starts the runs of the schedules that are due, and the sessions of the campaigns that are, for
as long as the Dot daemon lives."""

import asyncio
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotagent.agent.helpers import SCHEDULE_EFFORT_EVENT
from dotagent.agent.learning_health import (
    LEARNED,
    SKIPPED,
    LearningOutcome,
    check_learning,
    failed_with,
    quiet_limit,
    record_learning,
)
from dotagent.agent.manager import RunManager, agent_bounds, thread_title
from dotagent.agent.reflection import last_reflection, record_reflection_failure, reflection_due
from dotagent.agent.reflection_record import REFLECTION_OFF, Reflection, summary_line
from dotagent.agent.resume import RESUME_SOURCE, RESUMED_EVENT
from dotagent.config import SchedulerConfig
from dotagent.core.errors import DotError, NotFoundError, PrecheckError, ProviderError, RunConflict, StoreError
from dotagent.core.logs import get_logger
from dotagent.core.paths import failsafe_path, workspace_dir
from dotagent.events import SYSTEM_RUN_ID
from dotagent.harness.schedule import schedule_of
from dotagent.harness.weekly import night as harness_night
from dotagent.llm.types import Effort, Speed
from dotagent.memory.service import backfill
from dotagent.notify.owner import tell_owner
from dotagent.plugins import scout_service
from dotagent.plugins.scout import ScoutStopped
from dotagent.plugins.scout_types import ScoutState
from dotagent.proactive.brief import BriefFiring, is_brief, prepare, tell_brief
from dotagent.proactive.checkin import is_checkin, prepare_checkin, tell_checkin
from dotagent.proactive.initiative import Initiative
from dotagent.repair.github import command
from dotagent.repair.night import NIGHT_KEY, PROCESS_STARTED_AT, checkout_of, repair_night
from dotagent.repair.settle import settle_stored
from dotagent.restart_notice import tell_if_due
from dotagent.runtime import Runtime
from dotagent.scheduler import (
    SCHEDULE_MISSED_EVENT,
    SCHEDULE_RUN_ENDED_EVENT,
    SCHEDULE_SOURCE,
    SCHEDULE_STOPPED_EVENT,
    NightWork,
    ScheduleMissed,
    ScheduleRunEnded,
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
from dotagent.scheduler.notices import BY_HAND_LEVEL, notify_level, said_nothing_new, tells
from dotagent.scheduler.plans import schedule_zone
from dotagent.scheduler.precheck import CHECK_TIMEOUT_S, CheckVerdict, failure_line, opening_with_check, run_check
from dotagent.scheduler.recovery import (
    SCHEDULE_RETRY_EVENT,
    EndFacts,
    NoRetry,
    PendingRetry,
    Retry,
    ScheduleRetries,
    claim_stored,
    decide,
    drop_stored,
    end_facts,
    not_retried_status,
    queue_stored,
    retries_stored,
    retry_goal,
    retrying_status,
    started_stored,
    supersede_stored,
)
from dotagent.scheduler.timing import MissedFirings, format_local, newest_missed, next_firing
from dotagent.store.db import Store
from dotagent.store.models import (
    WORKING_STATUSES,
    AgentRecord,
    EventRecord,
    HarnessStep,
    NotifyLevel,
    RunRecord,
    ScheduleOptions,
    ScheduleRecord,
    ThreadRecord,
)

log = get_logger(__name__)

_UNFINISHED: frozenset[str] = frozenset({"starting", "running"})
# How many ticks in a row may fail before the scheduler's loop ends, and the daemon with it (``server/daemon.py``):
# five minutes at the default ``tick_s``. A failure that lasts so long is not passing, and schedules must not silently
# stop firing; a shorter one is waited out, so that it does not end the daemon with every run it holds. Only the parts
# that fire schedules and start sessions count (item 688): a record of the reflection or of the harness that cannot be
# read is logged each tick and ends nothing.
FAILED_TICKS_TO_END: int = 20


# The status of a one-time schedule that is due while the owner's emergency stop is engaged: it waits for the release.
HELD_STATUS: str = "waiting: the emergency stop is engaged"
# The status of a firing whose check said there is nothing to wake for (item 1855): no run, and no skip.
QUIET_STATUS: str = "quiet: its check found nothing to wake for"

# How far apart the late starts of firings missed while the daemon was off or the Mac slept are (item 1204), so that
# a wake that finds several due does not start them all at once; the others wait, still due, for a later tick.
CATCH_UP_GAP_S: float = 15.0


def late_goal(goal: str, due: float, now: float, zone: ZoneInfo) -> str:
    """The goal of a one-time schedule that fires late, after a note of when it was due and when it starts: what its
    run reads first, while the run keeps the goal alone (:meth:`RunManager.start_firing`)."""
    return f"(Scheduled for {format_local(due, zone)}; it starts late, at {format_local(now, zone)}.)\n\n{goal}"


def caught_up_goal(goal: str, due: float, now: float, zone: ZoneInfo, missed: int) -> str:
    """The goal of a firing that starts late (:func:`late_goal`), after a note, when ``missed`` firings before it were
    missed too (item 1204), that those do not run."""
    if missed == 0:
        return late_goal(goal, due, now, zone)
    return (
        f"(Scheduled for {format_local(due, zone)}; it starts late, at {format_local(now, zone)}. The"
        f" {_firings(missed)} before it were missed (Dot was off, the Mac slept or they could not start), and do not"
        f" run.)\n\n{goal}"
    )


def missed_status(missed: int, newest: float, late: bool, zone: ZoneInfo) -> str:
    """What the owner is told of the ``missed`` firings of a repeating schedule that did not run before its newest one,
    due at ``newest``, which runs (item 1204)."""
    when: str = "late, now" if late else "now"
    return (
        f"missed {_firings(missed)} (Dot was off, the Mac slept or they could not start); the one of"
        f" {format_local(newest, zone)} runs"
        f" {when}"
    )


def firing_tail(late: bool, missed: int) -> str:
    """What the title of a firing's chat says after its name and time: that it started late, and how many firings
    before it were missed (item 1204), which the schedule's History shows; nothing for one on time."""
    parts: list[str] = (["late"] if late else []) + ([f"{missed} missed before"] if missed else [])
    return f" · {', '.join(parts)}" if parts else ""


def _firings(count: int) -> str:
    return "1 firing" if count == 1 else f"{count} firings"


def by_hand_goal(goal: str, now: float, zone: ZoneInfo) -> str:
    """The goal of a run the owner started by hand (item 455), after a note that it did not start at its time: what its
    run reads first, while the run keeps the goal alone, as a late firing's does (:func:`late_goal`)."""
    return f"(Started by hand at {format_local(now, zone)}, not at its time.)\n\n{goal}"


def _with_check(record: ScheduleRecord, opening: str, woken: CheckVerdict | None) -> tuple[ScheduleRecord, str]:
    """``(record, opening)`` as a firing's run starts with them: unchanged without a check's verdict; with one, the
    opening followed by what the check printed, and the run started tainted, as a watcher's is, since a check may have
    read what others wrote (item 1855)."""
    if woken is None or record.options.precheck is None:
        return record, opening
    tainted: ScheduleOptions = record.options.model_copy(update={"from_outside": True})
    return (
        record.model_copy(update={"options": tainted}),
        opening_with_check(opening, record.options.precheck, woken.output),
    )


def _following(record: ScheduleRecord, now: float, zone: ZoneInfo) -> float | None:
    """When the schedule fires after a firing at ``now``: the next cron time, or never again for a one-time schedule."""
    return None if record.cron is None else next_firing(record.cron, now, zone)


async def _emergency_stop_engaged() -> bool:
    """Whether the owner's emergency stop is engaged: its STOP file exists."""
    return await asyncio.to_thread(failsafe_path().exists)


def _chat_busy(error: DotError) -> bool:
    """Whether a firing's start was refused because a run works in the chat it goes on in (item 723)."""
    return isinstance(error, RunConflict) and "thread_id" in error.context


def restart_argv(python: str) -> tuple[str, ...]:
    """The command that asks for the daemon's restart once idle (item 943): with ``-P``, so that the folder it runs in
    (the home folder, which a sandboxed run may write) is no place Python imports a module from, as the launchd jobs
    and the harness's probes run (a ``~/dotagent/__main__.py`` a run left there would run instead of Dot)."""
    return (python, "-P", "-m", "dotagent", "service", "restart", "--when-idle")


class Scheduler:
    """Looks for due schedules every ``tick_s`` seconds and starts a run for each, in the chat a firing goes in
    (:meth:`_firing_chat`).

    Every firing advances the schedule's next run before the run starts, so a crash can lose a firing but never repeat
    one. Firings of a repeating schedule that were missed collapse into the newest (item 1204): it starts as at its
    time within ``misfire_grace_s``, late (saying so, at most one such start every :data:`CATCH_UP_GAP_S`) within
    ``catch_up_s``, and not at all past that; the older ones are told as missed once the oldest is past the grace.
    A repeating schedule whose previous run is still going skips the firing; a one-time one waits for that run, still
    due. The store is the only persistent state; the instance remembers which schedules have a run in flight.

    The nightly reflection (item 70) fires here too, at ``[agent] reflection_time``: once its daily time has passed
    since the last reflection (or since the last attempt, or since this scheduler was made), it runs in a task of its
    own, so the schedules due at the same time wait for nothing. A night the daemon slept through is caught up once.
    """

    def __init__(self, runtime: Runtime, manager: RunManager, scout: bool = False) -> None:
        """``scout`` lets the plugin scout's night start (item 711), and the repair night (item 921): the daemon's
        scheduler alone, since a night reads GitHub, calls the review model or runs Claude Code, which a scheduler made
        elsewhere (a test ticking its own clock) must not."""
        self._runtime: Runtime = runtime
        self._may_scout: bool = scout
        self._manager: RunManager = manager
        self._zone: ZoneInfo = runtime.schedule_zone
        self._in_flight: dict[str, str] = {}
        # The schedules whose run is being started, by a tick or by hand (item 455), until it is in flight.
        self._starting: set[str] = set()
        # The earliest moment the next late firing may start (item 1204, :data:`CATCH_UP_GAP_S`).
        self._next_late_start: float = float("-inf")
        self._watchers: set[asyncio.Task[None]] = set()
        self._loop: asyncio.Task[None] | None = None
        self._reflected_from: float = time.time()
        self._reflecting: asyncio.Task[None] | None = None
        # The step of the harness (item 391), timed like the reflection.
        self._stepped_from: float = time.time()
        self._stepping: asyncio.Task[None] | None = None
        # The plugin scout's night (item 711), timed like the reflection.
        self._scouted_from: float = time.time()
        self._scouting: asyncio.Task[None] | None = None
        # The repair night (item 921), timed like the reflection.
        self._repaired_from: float = time.time()
        self._repairing: asyncio.Task[None] | None = None
        # The reading of where the repairs' pull requests stand on GitHub (item 958), which a night waits for.
        self._settling: asyncio.Task[None] | None = None
        # Notes that have no vector, embedded off the request path. One pass at a time.
        self._backfilling: asyncio.Task[None] | None = None
        # Dot's own initiative (item 715): its runs while the Mac is idle, started from the tick.
        self._initiative: Initiative = Initiative(runtime, manager)

    async def start(self) -> asyncio.Task[None]:
        """Begin ticking in the background; what an earlier process left unfinished (a schedule starting or running a
        run, whose watch went with that process) is marked as interrupted and logged. A run that was under way counts
        as a failure in the schedule's row (item 1105): a schedule whose run brings the daemon down is switched off as
        one whose runs fail is. That run is tried once more when it may be (item 1206, :meth:`_recover`).

        Returns the task of the loop, so the caller can notice when it ends.
        """
        if self._loop is not None:
            raise RunConflict("the scheduler is already running", {})
        store: Store = self._runtime.store
        for record in await store.list_schedules():
            if record.last_status in _UNFINISHED:
                if record.last_status == "running":
                    failed_in_row: int | None = await store.record_schedule_outcome(record.id, "interrupted")
                    if failed_in_row is not None:
                        await self._stop_if_failing(record.id, failed_in_row)
                    await self._recover_cut(record)
                else:
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
        scouting: asyncio.Task[None] | None = self._scouting
        if scouting is not None:
            scouting.cancel()
        repairing: asyncio.Task[None] | None = self._repairing
        if repairing is not None:
            repairing.cancel()
        backfilling: asyncio.Task[None] | None = self._backfilling
        if backfilling is not None:
            backfilling.cancel()
        settling: asyncio.Task[None] | None = self._settling
        if settling is not None:
            settling.cancel()
        await self._initiative.stop()
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
        if self._scouting is not None:
            await asyncio.wait({self._scouting})
        if self._repairing is not None:
            await asyncio.wait({self._repairing})
        if self._backfilling is not None:
            await asyncio.wait({self._backfilling})
        if self._settling is not None:
            await asyncio.wait({self._settling})

    def reflecting(self) -> bool:
        """Whether a reflection is under way."""
        return self._reflecting is not None and not self._reflecting.done()

    async def tick(self, now: float) -> None:
        """Fire every switched-on schedule that is due at ``now``, start the next session of every active campaign
        that is, and start the reflection when its time has come. A campaign's session is never skipped as overdue:
        the campaign goes on where it stopped. While the owner's emergency stop is engaged, no campaign starts a
        session (each waits for the release; the stop is looked at before each one) and no schedule fires
        (:meth:`_fire`). Learning that has stopped by the clock is told to the owner (:meth:`_check_learning`). The
        parts are done each on its own: one that fails is logged where it failed and keeps the others from nothing. The
        failures of the parts that fire schedules and start sessions are raised together at the end, as an
        ``ExceptionGroup``, and a tick that raises counts towards the end of the loop (:meth:`_tick_forever`); a
        failure of the others (the reflection, the harness's step, the record of how learning fares, Dot's own
        initiative of item 715, the follow-up of the work orders of item 907, the embedding of notes that have no
        vector) is logged at each tick with the part's name and never raises (item 688)."""
        failures: list[Exception] = []
        for part in (self._fire_due, self._retry_due, self._start_due_sessions):
            try:
                await part(now)
            except Exception as exc:
                log.exception("scheduler_part_failed", part=part.__name__)
                failures.append(exc)
        for part in (
            self._reflect_if_due,
            self._step_if_due,
            self._scout_if_due,
            self._repair_if_due,
            self._check_learning,
            self._tell_restart_waits,
            self._initiative.tick,
            self._manager.desk.follow_up,
        ):
            try:
                await part(now)
            except Exception as exc:
                log.warning(
                    "scheduler_auxiliary_part_failed",
                    part=part.__name__,
                    error=repr(exc),
                    note="the loop goes on; this part is tried again at the next tick",
                    exc_info=True,
                )
        try:
            await self._backfill_memory(now)
        except Exception as exc:
            log.warning(
                "scheduler_auxiliary_part_failed",
                part=self._backfill_memory.__name__,
                error=repr(exc),
                note="the loop goes on; this part is tried again at the next tick",
                exc_info=True,
            )
        if failures:
            raise ExceptionGroup("a part of the scheduler's tick failed", failures)

    async def _tell_restart_waits(self, now: float) -> None:
        """Tell the owner which run a restart they asked for waits for, once it has waited half an hour (item 1310)."""
        await tell_if_due(self._runtime, now)

    async def _backfill_memory(self, _now: float) -> None:
        """Embed notes that have no vector, in a task, until none are left (item 1263). A pass already running is left
        to finish. Each note is stored before the next batch (:func:`dotagent.memory.service.backfill`), so a process
        that dies mid-way leaves what it wrote and the next tick takes the rest."""
        running: asyncio.Task[None] | None = self._backfilling
        if (running is not None and not running.done()) or self._runtime.memory.embedder is None:
            return
        if not await self._runtime.store.memories_without_vectors(1):
            return
        self._backfilling = asyncio.create_task(self._embed_missing(), name="memory-backfill")

    async def _embed_missing(self) -> None:
        """One background pass of :func:`dotagent.memory.service.backfill`. A service that fails is logged; the next
        tick tries again. A fault that is not the service's is logged here, so the task's error is not lost."""
        try:
            embedded: int = await backfill(self._runtime.memory)
        except ProviderError as error:
            log.warning(
                "memory_backfill_failed",
                error_type=type(error).__name__,
                error=error.message,
                note="the next tick tries again",
            )
        except Exception:
            log.exception("memory_backfill_crashed")
        else:
            if embedded:
                log.info("memory_backfilled", notes=embedded)

    async def _fire_due(self, now: float) -> None:
        """Fire every switched-on schedule that is due at ``now``, each on its own (:meth:`_fire_alone`)."""
        for record in await self._runtime.store.due_schedules(now):
            await self._fire_alone(record, now)

    async def _start_due_sessions(self, now: float) -> None:
        """Plan again the campaigns a failed end left without a next session (item 1104), and start the next session of
        every active campaign that is due at ``now``, while the emergency stop is not engaged."""
        await self._manager.replan_stalled_campaigns(now)
        for campaign in await self._runtime.store.due_campaigns(now):
            if await _emergency_stop_engaged():
                break
            await self._start_session(campaign.id)

    async def _reflect_if_due(self, now: float) -> None:
        """Start the reflection in a task when its daily time has passed since the last one (or the last attempt). While
        the emergency stop is engaged none starts, and one under way is cancelled, as at the daemon's stop: it records
        nothing until its end (item 683)."""
        if await _emergency_stop_engaged():
            if self._reflecting is not None and not self._reflecting.done():
                self._reflecting.cancel()
                log.info("reflection_stopped_by_emergency_stop")
            return
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

    def night_work(self) -> tuple[NightWork, ...]:
        """The night's learning under way now (item 815), which a restart would cut off: the reflection, the harness's
        step, the plugin scout's night (this scheduler's or one the owner started with Look now) and the repair night
        (item 921)."""
        work: list[NightWork] = []
        if self.reflecting():
            work.append("reflection")
        if self.stepping():
            work.append("harness_step")
        if (self._scouting is not None and not self._scouting.done()) or scout_service.running(self._runtime):
            work.append("plugin_scout")
        if self.repairing():
            work.append("repair")
        return tuple(work)

    def repairing(self) -> bool:
        """Whether a repair night is under way."""
        return self._repairing is not None and not self._repairing.done()

    def repair_now(self) -> bool:
        """Start a repair night now, by the owner's hand (Settings › Self-repair, item 921): ``False`` when one is under
        way already. The night itself stops before a session while the emergency stop is engaged."""
        if self.repairing():
            return False
        self._repairing = asyncio.create_task(self._repair(), name="repair-night")
        return True

    async def stop_repair(self) -> bool:
        """Stop the repair night under way, by the owner's hand (Settings › Self-repair, item 939): its task is
        cancelled, which ends the session of Claude Code with everything it started and leaves its problem as it was,
        as the emergency stop does; waits until it has ended. ``False`` when none is under way."""
        task: asyncio.Task[None] | None = self._repairing
        if task is None or task.done():
            return False
        task.cancel()
        await asyncio.wait({task})
        log.info("repair_stopped_by_the_owner")
        return True

    async def _repair_if_due(self, now: float) -> None:
        """Start the repair night (item 921) in a task when its daily time has passed since the last one (or the last
        attempt); the daemon's scheduler alone, never while one is under way, never with ``[repair] time`` off or no
        ``repo``. While the emergency stop is engaged none starts, and one under way is cancelled, which ends its
        session of Claude Code and the commands it started."""
        if await _emergency_stop_engaged():
            if self._repairing is not None and not self._repairing.done():
                self._repairing.cancel()
                log.info("repair_stopped_by_emergency_stop")
            return
        config = self._runtime.config.repair
        if not self._may_scout or not config.repo or config.time == REFLECTION_OFF:
            return
        if self._repairing is not None and not self._repairing.done():
            return
        if not reflection_due(config.time, self._repaired_from, now, self._zone):
            return
        kept: str | None = await self._runtime.store.kv_get(NIGHT_KEY)
        anchor: float = max(self._repaired_from, float(kept) if kept is not None else 0.0)
        if not reflection_due(config.time, anchor, now, self._zone):
            return
        self._repaired_from = now
        await self._runtime.store.kv_set(NIGHT_KEY, str(now))
        self._repairing = asyncio.create_task(self._repair(), name="repair-night")

    def settle_repairs(self) -> asyncio.Task[None] | None:
        """Read again, in a task, where the repairs' pull requests stand on GitHub (item 958): when Settings ›
        Self-repair opens. ``None`` while a night is under way, which reads them itself first; the reading under way
        when one is. A night that starts meanwhile waits for it, so the two never write the book at once."""
        if self.repairing():
            return None
        if self._settling is None or self._settling.done():
            self._settling = asyncio.create_task(self._settle(), name="repair-settle")
        return self._settling

    async def _settle(self) -> None:
        """Task body of a reading of the pull requests' states, and the one place its errors end: logged, and the next
        reading tries again."""
        try:
            repo: Path | None = await asyncio.to_thread(checkout_of, self._runtime.config.repair.repo)
            if repo is None:
                return
            await settle_stored(self._runtime.store, repo, dict(self._runtime.shell_env), asyncio.sleep)
        except DotError as exc:
            log.error("repair_settle_failed", error=exc.message, context=exc.context)
        except Exception:
            log.exception("repair_settle_crashed")

    async def _repair(self) -> None:
        """Task body of the repair night, and the one place its errors end: logged, and the next night tries again.
        Where its pull requests stand is read first (item 958), after a reading under way ends."""
        settling: asyncio.Task[None] | None = self._settling
        if settling is not None and not settling.done():
            await asyncio.wait({settling})
        await self._settle()
        try:
            done = await repair_night(
                self._runtime, self._restart_when_idle, time.time, asyncio.sleep, PROCESS_STARTED_AT
            )
        except DotError as exc:
            log.error("repair_night_failed", error=exc.message, context=exc.context)
        except Exception:
            log.exception("repair_night_crashed")
        else:
            log.info("repair_night_done", sessions=len(done), ends=[session.end for session in done])

    async def _restart_when_idle(self) -> None:
        """Ask for the daemon's restart once no run works and none of the night's work is under way (the repair night
        merged a change into the owner's checkout): ``dotx service restart --when-idle``, as a change of Dot's code
        asks for it."""
        await command(restart_argv(sys.executable), Path.home(), self._runtime.shell_env)

    async def _step_if_due(self, now: float) -> None:
        """Start the night of the harness (the digest of the day's real runs, item 987) in a task when its daily time
        has passed since the last one (or the last attempt); never while one is under way, and never with ``[harness]
        step_time`` off. While the emergency stop is engaged none starts, and one under way is cancelled (item 683)."""
        if await _emergency_stop_engaged():
            if self.stepping() and self._stepping is not None:
                self._stepping.cancel()
                log.info("harness_step_stopped_by_emergency_stop")
            return
        if self.stepping():
            return
        # The page's choice wins over the settings file's (item 721).
        step_time: str = (await schedule_of(self._runtime.store, self._runtime.config.harness)).step_time
        if step_time == REFLECTION_OFF:
            return
        last: list[HarnessStep] = await self._runtime.store.list_harness_steps(1)
        anchor: float = max(self._stepped_from, last[0].started_at if last else 0.0)
        if not reflection_due(step_time, anchor, now, self._zone):
            return
        self._stepped_from = now
        self._stepping = asyncio.create_task(self._step(now), name="harness-step")

    async def _scout_if_due(self, now: float) -> None:
        """Start the plugin scout's night (item 711) in a task when its daily time has passed since its last night
        (or the last attempt); never while one is under way (the scheduler's, or a Look now, which then counts as the
        night's), never with ``[scout] time`` off, and none while the
        emergency stop is engaged, which also cancels one under way: it stores nothing until its end, so the next
        night looks at the same plugins again."""
        if await _emergency_stop_engaged():
            if self._scouting is not None and not self._scouting.done():
                self._scouting.cancel()
                log.info("scout_stopped_by_emergency_stop")
            return
        if not self._may_scout or (self._scouting is not None and not self._scouting.done()):
            return
        scout_time: str = self._runtime.config.scout.time
        if scout_time == REFLECTION_OFF:
            return
        # The cheap check first: a night is not due before its time since the last attempt, whatever the state says.
        if not reflection_due(scout_time, self._scouted_from, now, self._zone):
            return
        last: ScoutState = await scout_service.load(self._runtime.store)
        anchor: float = max(self._scouted_from, last.last_run or 0.0)
        if not reflection_due(scout_time, anchor, now, self._zone):
            return
        self._scouted_from = now
        if scout_service.running(self._runtime):
            # A look the owner started (Look now) is under way: it is tonight's, not one to queue behind it.
            log.info("scout_night_taken_by_look_now")
            return
        self._scouting = asyncio.create_task(self._scout(now), name="plugin-scout")

    async def _scout(self, now: float) -> None:
        """Task body of the scout's night, and the one place its errors end: logged, and the next night tries again."""
        try:
            state: ScoutState = await scout_service.night(self._runtime, now)
        except ScoutStopped as exc:
            log.info("scout_stopped", reason=exc.message)
        except DotError as exc:
            log.error("scout_failed", error=exc.message, context=exc.context)
        except Exception:
            log.exception("scout_crashed")
        else:
            log.info("scout_fired", summary=state.last_summary)
            tell: tuple[str, str] | None = scout_service.blind_tell(state)
            if tell is not None:
                await tell_owner(self._runtime.bus, tell[0], tell[1], scout_service.PLUGINS_ROUTE, "system", "")

    async def _step(self, now: float) -> None:
        """Task body of the night of the harness, and the one place its errors end: its outcome is logged and kept in
        the record of how learning fares, as the reflection's is. The night is the digest of the day's real runs (item
        987), which calls no model, and once a week the proposal and the trial of a version on real runs (item 988); the
        step that plays synthetic tasks (item 391) runs only when asked (``dotx harness step``)."""
        try:
            step: HarnessStep = await harness_night(self._runtime, now)
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
        """Tell the owner when no reflection went well for too long, though none ended since, and none is under way
        (:func:`dotagent.agent.learning_health.check_learning`). A record that cannot be read is logged: it keeps the
        scheduler from nothing."""
        try:
            await check_learning(
                self._runtime.store,
                self._runtime.bus,
                now,
                quiet_limit(self._runtime.config.agent.reflection_time),
                self.reflecting(),
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
            # A reflection that could not draw the lessons of some runs is done, and a failure too (item 687): the
            # record of how learning fares counts it, so that a draw that keeps failing is told to the owner.
            outcome = LEARNED if reflection.lessons_error is None else failed_with(reflection.lessons_error)
        quiet_s: float | None = quiet_limit(self._runtime.config.agent.reflection_time)
        try:
            if outcome.error is not None:
                await record_reflection_failure(self._runtime.store, now, outcome.error)
            await record_learning(self._runtime.store, self._runtime.bus, "reflection", now, outcome, quiet_s)
        except StoreError as error:
            log.error("learning_not_recorded", work="reflection", state=outcome.state, error=error.message)

    async def _tick_forever(self) -> None:
        """Tick every ``tick_s`` seconds. A tick that failed (a part that fires schedules or starts sessions did, see
        :meth:`tick`) is tried again at the next one; :data:`FAILED_TICKS_TO_END` failed ticks in a row end the loop
        with the last one's failures."""
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
            if current is not None and await self._waits_after_failure(current, now):
                return
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

    async def _waits_after_failure(self, record: ScheduleRecord, now: float) -> bool:
        """Whether a schedule whose firings of this time failed waits before it is tried again (:func:`retry_after`): a
        recurring one as a one-time one does, rather than at every tick (the review of 2026-11-01: a zone that could
        not be read failed every 15 seconds for an hour)."""
        failure: FiringFailure | None = await firing_failure(self._runtime.store, record.id)
        due: float | None = record.next_run_at
        return failure is not None and due is not None and failure.last_failure_at >= due and now < retry_after(failure)

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
        """Start the run of a due schedule; a firing the emergency stop, the pause of the agent it runs as (item 465), a
        run still going or an absence of the daemon longer than ``catch_up_s`` keeps from starting is skipped to the
        schedule's next time, with the reason as its status. A repeating schedule's newest missed firing within
        ``catch_up_s`` fires late, as a one-time schedule's overdue firing does, one such start every
        :data:`CATCH_UP_GAP_S` (item 1204). A one-time schedule has no next time and is never dropped: while the
        emergency stop is engaged, its agent is paused, or a run of it started by hand works, it waits, still due, and
        one overdue fires late, its opening saying when it was due (:func:`caught_up_goal`). An emergency stop engaged
        while the run was being started ends it as one. A firing missed where nobody saw it, one overdue, one whose run
        could not start or the older ones a late firing leaves behind, is told (:meth:`_tell_missed`)."""
        store: Store = self._runtime.store
        if record.next_run_at is None:
            raise StoreError("a due schedule has no next run", {"schedule_id": record.id})
        # The time it was read due at: a skip moves it on only while it still is (item 1047).
        due: float = record.next_run_at
        zone: ZoneInfo = await schedule_zone(record.options, self._zone)
        following: float | None = _following(record, now, zone)
        config: SchedulerConfig = self._runtime.config.scheduler
        one_time: bool = record.cron is None
        # The firing that runs: a repeating schedule's newest missed one (item 1204), how late it is, and the older
        # ones it missed, told only once the oldest is past the grace (within it they collapse unsaid, as before).
        missed: MissedFirings = (
            MissedFirings(newest=due, older=0) if record.cron is None else newest_missed(record.cron, due, now, zone)
        )
        overdue_s: float = now - missed.newest
        late: bool = overdue_s > config.misfire_grace_s
        too_late: bool = late and not one_time and overdue_s > config.catch_up_s
        told_missed: int = missed.older if now - due > config.misfire_grace_s else 0
        if await _emergency_stop_engaged():
            if one_time:
                if record.last_status != HELD_STATUS:
                    log.warning("schedule_held_emergency_stop", schedule_id=record.id)
                    await store.set_schedule_status(record.id, HELD_STATUS)
                return
            log.warning("schedule_skipped_emergency_stop", schedule_id=record.id)
            await store.record_schedule_skip(record.id, due, following, "skipped: the emergency stop is engaged")
            return
        paused: AgentRecord | None = await self._paused_agent(record)
        if paused is not None:
            # A pause is the owner's: not a failure, so nobody is told. The firing it skips is counted (item 1401),
            # so the count of firings skipped in a row grows for as long as the pause lasts.
            if one_time:
                waiting: str = f"waiting: {paused.name} is paused"
                if record.last_status != waiting:
                    log.info("schedule_waiting_agent_paused", schedule_id=record.id, agent_id=paused.id)
                    await store.set_schedule_status(record.id, waiting)
                return
            log.info("schedule_skipped_agent_paused", schedule_id=record.id, agent_id=paused.id)
            await store.record_schedule_skip(record.id, due, following, f"skipped: {paused.name} is paused")
            return
        if one_time:
            # The wait is that of a try of this time again: a time given since the last failure fires when it comes.
            failure: FiringFailure | None = await firing_failure(store, record.id)
            if failure is not None and failure.last_failure_at >= record.next_run_at and now < retry_after(failure):
                return
        await self._adopt_resumed(record)
        if record.id in self._in_flight or record.id in self._starting:
            if one_time:
                # Only a run by hand is at work while a one-time schedule is still due (item 455): the schedule waits,
                # due, and fires once that run has ended, as it waits for the release of the emergency stop.
                return
            log.warning("schedule_skipped_running", schedule_id=record.id, run_id=self._in_flight.get(record.id))
            await store.record_schedule_skip(record.id, due, following, "skipped: the previous task was still going")
            return
        if too_late:
            log.warning("schedule_skipped_overdue", schedule_id=record.id, overdue_s=round(overdue_s))
            # The daemon was off or the Mac slept past the catch-up window: the clock cannot tell which (item 553).
            failed_before: FiringFailure | None = await firing_failure(store, record.id)
            before: str = f"{_firings(missed.older)} missed before it; " if missed.older else ""
            overdue: str = (
                f"skipped: it could not fire for {round((now - due) / 60)} minutes: {failed_before.last_error}"
                if failed_before is not None and failed_before.last_failure_at >= record.next_run_at
                else f"skipped: {before}{round(overdue_s / 60)} minutes overdue (Dot was off or the Mac slept)"
            )
            await store.record_schedule_skip(record.id, due, following, overdue)
            await self._tell_missed(record, overdue)
            return
        if late:
            # A late start waits its turn (item 1204): the firing stays due, so a later tick, or the next process
            # after a restart, starts it; the claim moves the schedule on before its run starts, so none runs twice.
            if now < self._next_late_start:
                return
            self._next_late_start = now + CATCH_UP_GAP_S
        self._starting.add(record.id)
        try:
            woken: CheckVerdict | None = None
            if record.options.precheck is not None:
                woken = await self._woken(record, record.options.precheck, due, following)
                if woken is None:
                    return
            await self._start_due(record, missed.newest, told_missed, now, zone, following, late, overdue_s, woken)
        finally:
            self._starting.discard(record.id)
            if late and record.id not in self._in_flight:
                # No run started (the claim was dropped, the start failed): the next late firing need not wait.
                self._next_late_start = now

    async def _check(self, name: str) -> CheckVerdict:
        """Run the check ``name`` (item 1855) as a run's commands start: in the sandbox while it is on, from the
        workspace, with the environment of a run's commands."""
        return await run_check(
            name,
            self._runtime.sandbox.current().commands,
            workspace_dir(),
            self._runtime.shell_env,
            CHECK_TIMEOUT_S,
        )

    async def _woken(
        self, record: ScheduleRecord, name: str, due: float, following: float | None
    ) -> CheckVerdict | None:
        """What the check ``name`` of ``record`` said when it said to wake; ``None`` when it did not, the firing moved
        on to the schedule's next time with its status saying why: quiet when the check said there is nothing to wake
        for, which is no skip, or failed, which is told to the owner as a missed firing is. A failure is never taken as
        quiet."""
        store: Store = self._runtime.store
        try:
            verdict: CheckVerdict = await self._check(name)
        except PrecheckError as error:
            log.warning(
                "schedule_check_failed", schedule_id=record.id, check=name, error=error.message, context=error.context
            )
            failed: str = f"skipped: its check failed: {failure_line(error)}"
            await store.record_schedule_skip(record.id, due, following, failed)
            await self._tell_missed(record, failed)
            return None
        if verdict.wake:
            return verdict
        log.info("schedule_check_quiet", schedule_id=record.id, check=name)
        await store.record_schedule_quiet(record.id, due, following, QUIET_STATUS)
        return None

    async def _adopt_resumed(self, record: ScheduleRecord) -> None:
        """Keep as the schedule's run at work one that was resumed by hand (item 553): the daemon's end cut a firing's
        run, the owner pressed Resume, and the run that goes on from it works in the schedule's chat though this
        scheduler did not start it. Left alone, the schedule fires beside it and its end never reaches the schedule.
        Only a run that resumes one of a firing counts: the resumed follow-up of the owner's is not the schedule's."""
        if record.id in self._in_flight or record.thread_id is None:
            return
        store: Store = self._runtime.store
        newest: list[RunRecord] = await store.list_runs(1, record.thread_id)
        if not newest or newest[0].source != RESUME_SOURCE or newest[0].status not in WORKING_STATUSES:
            return
        resumed: EventRecord | None = await store.last_event(newest[0].id, RESUMED_EVENT)
        origin: object = None if resumed is None else resumed.data.get("from_run_id")
        if not isinstance(origin, str):
            return
        try:
            earlier: RunRecord = await store.get_run(origin)
        except NotFoundError:
            return
        if earlier.source not in (SCHEDULE_SOURCE, RESUME_SOURCE):
            return
        log.info("schedule_resumed_run_followed", schedule_id=record.id, run_id=newest[0].id)
        await self._track(record.id, newest[0], self._level(record))

    async def _recover_cut(self, record: ScheduleRecord) -> None:
        """The task of ``record`` that an earlier process left running, which its end cut (item 1206): the newest run
        of the schedule's chat; it is tried once more when it may be (:meth:`_recover`)."""
        if record.thread_id is None:
            return
        newest: list[RunRecord] = await self._runtime.store.list_runs(1, record.thread_id)
        if newest:
            await self._recover(record.id, newest[0])

    async def _recover(self, schedule_id: str, run: RunRecord) -> None:
        """Try the ended task ``run`` of ``schedule_id`` once more when its end calls for it (item 1206,
        :func:`~dotagent.scheduler.recovery.decide`): the try is kept, due 15 s on or a minute after the Claude plan's
        reset, and the schedule's status says when; a task that would be tried and is not is told to the owner. Only a
        run of the schedule's own source is tried, never a delegated run nor one the owner resumed. A schedule deleted
        meanwhile, or a repeating one switched off, tries nothing. What the store fails is logged, and the end
        stands."""
        if run.source != SCHEDULE_SOURCE or run.parent_run_id is not None:
            return
        store: Store = self._runtime.store
        try:
            now: float = time.time()
            facts: EndFacts = await end_facts(self._runtime, run, schedule_id, now)
            verdict: Retry | NoRetry | None = decide(facts, now)
            if verdict is None:
                return
            record: ScheduleRecord = await store.get_schedule(schedule_id)
            if isinstance(verdict, NoRetry):
                await self._not_retried(record, run.id, verdict)
                return
            if record.cron is not None and not record.enabled:
                log.info("schedule_retry_not_queued_off", schedule_id=schedule_id, run_id=run.id)
                return
            zone: ZoneInfo = await schedule_zone(record.options, self._zone)
            await queue_stored(store, schedule_id, verdict.pending)
            await store.set_schedule_status(schedule_id, retrying_status(verdict.pending, zone))
            log.warning(
                "schedule_retry_queued",
                schedule_id=schedule_id,
                run_id=run.id,
                why=verdict.pending.why,
                due_at=verdict.pending.due_at,
            )
        except NotFoundError:
            log.info("schedule_retry_schedule_gone", schedule_id=schedule_id, run_id=run.id)
        except DotError as exc:
            log.error(
                "schedule_retry_not_decided",
                schedule_id=schedule_id,
                run_id=run.id,
                error=exc.message,
                context=exc.context,
            )

    async def _not_retried(self, record: ScheduleRecord, run_id: str, refused: NoRetry) -> None:
        """Tell the owner that the task ``run_id`` of ``record`` is not tried again and why, whatever the schedule's
        level of notices (item 1206: the owner asked to be told), its tap opening that task; the schedule's status says
        so unless it says the schedule was switched off."""
        status: str = not_retried_status(refused)
        log.warning("schedule_task_not_retried", schedule_id=record.id, run_id=run_id, why=refused.why)
        if not record.last_status.startswith("switched off:"):
            await self._runtime.store.set_schedule_status(record.id, status)
        await tell_owner(
            self._runtime.bus,
            "A schedule's task was not tried again",
            f"{record.name}: {status}",
            f"#/runs/{run_id}",
            "schedule_end",
            record.id,
        )

    async def _retry_due(self, now: float) -> None:
        """Start every try of a schedule's task that is due at ``now``, each on its own (:meth:`_retry_alone`)."""
        retries: ScheduleRetries = await retries_stored(self._runtime.store)
        for schedule_id, pending in retries.pending.items():
            if pending.due_at <= now:
                await self._retry_alone(schedule_id, pending, now)

    async def _retry_alone(self, schedule_id: str, pending: PendingRetry, now: float) -> None:
        """Start one try (:meth:`_retry`). One that cannot start is dropped, so that it is not started again at every
        tick, and told to the owner as a task not tried again; the other tries and schedules go on."""
        try:
            await self._retry(schedule_id, pending, now)
        except DotError as exc:
            log.error("schedule_retry_failed", schedule_id=schedule_id, error=exc.message, context=exc.context)
            await drop_stored(self._runtime.store, schedule_id, pending.from_run_id)
            record: ScheduleRecord = await self._runtime.store.get_schedule(schedule_id)
            refused: NoRetry = NoRetry(why=pending.why, reason=f"the try could not start: {exc.message}")
            await self._not_retried(record, pending.from_run_id, refused)

    async def _retry(self, schedule_id: str, pending: PendingRetry, now: float) -> None:
        """Start the try ``pending`` of a task of ``schedule_id`` (item 1206) unless something drops it: the schedule
        deleted or (a repeating one) switched off, its agent paused, or a run of it at work, which does the task; one
        Dot was off for longer than ``catch_up_s`` past its time, or that comes while the emergency stop is engaged, is
        dropped and told."""
        store: Store = self._runtime.store
        try:
            record: ScheduleRecord = await store.get_schedule(schedule_id)
        except NotFoundError:
            log.info("schedule_retry_dropped_gone", schedule_id=schedule_id, run_id=pending.from_run_id)
            await drop_stored(store, schedule_id, pending.from_run_id)
            return
        if record.cron is not None and not record.enabled:
            log.info("schedule_retry_dropped_off", schedule_id=schedule_id, run_id=pending.from_run_id)
            await drop_stored(store, schedule_id, pending.from_run_id)
            return
        refused: str | None = None
        if now - pending.due_at > self._runtime.config.scheduler.catch_up_s:
            refused = "Dot was off when it was due, for longer than a schedule waits to start late"
        elif await _emergency_stop_engaged():
            refused = "the emergency stop is engaged"
        if refused is not None:
            await drop_stored(store, schedule_id, pending.from_run_id)
            await self._not_retried(record, pending.from_run_id, NoRetry(why=pending.why, reason=refused))
            return
        paused: AgentRecord | None = await self._paused_agent(record)
        if paused is not None:
            # A pause is the owner's: nobody is told, as for a firing it skips.
            log.info("schedule_retry_dropped_paused", schedule_id=schedule_id, agent_id=paused.id)
            await drop_stored(store, schedule_id, pending.from_run_id)
            await store.set_schedule_status(schedule_id, f"not tried again: {paused.name} is paused")
            return
        await self._adopt_resumed(record)
        if schedule_id in self._in_flight or schedule_id in self._starting:
            log.info("schedule_retry_dropped_running", schedule_id=schedule_id, run_id=self._in_flight.get(schedule_id))
            await drop_stored(store, schedule_id, pending.from_run_id)
            return
        self._starting.add(schedule_id)
        try:
            await self._start_retry(record, pending, now)
        finally:
            self._starting.discard(schedule_id)

    async def _start_retry(self, record: ScheduleRecord, pending: PendingRetry, now: float) -> None:
        """Start the run of the try ``pending`` as a firing starts its run: the schedule's own choices and source, the
        chat a firing goes in (one made for it titled ``... · retry``), its first message saying what it tries again
        and why. The try is claimed in the store before its run starts, so that a crash loses it rather than repeating
        it, and its run is marked with a ``schedule_retry`` event, so that it is never tried itself."""
        store: Store = self._runtime.store
        zone: ZoneInfo = await schedule_zone(record.options, self._zone)
        same_chat, digest = await self._history(record, zone)
        record, digest, title = await self._briefed(
            record, digest, now, zone, thread_title(f"{record.name} · {format_local(now, zone)}")
        )
        thread_id, made = await self._firing_chat(record, same_chat, f"{title} · retry")
        if not await claim_stored(store, record.id, pending.from_run_id, thread_id, now):
            # Claimed elsewhere, or superseded by a firing, since it was read due.
            log.info("schedule_retry_claim_dropped", schedule_id=record.id, run_id=pending.from_run_id)
            if made:
                await self._drop_chat(record.id, thread_id)
            return
        started: RunRecord | None = None
        try:
            carried: tuple[str, ...] = await self._carried(digest, made, thread_id)
            opening: str = firing_opening(retry_goal(record.goal, pending, zone), digest)
            started = await self._start(record, opening, thread_id, carried)
        finally:
            if started is None and made:
                await self._drop_chat(record.id, thread_id)
        run: RunRecord = started
        # Followed first, so that a write that fails below leaves no run of the schedule unwatched; until the mark is
        # written, the claim tells the try apart (:func:`~dotagent.scheduler.recovery.is_try_run`).
        await self._track(record.id, run, self._level(record))
        try:
            await started_stored(store, record.id, pending.from_run_id, run.id)
            await self._runtime.bus.emit(
                run.id,
                SCHEDULE_RETRY_EVENT,
                {"from_run_id": pending.from_run_id, "why": pending.why, "ended_at": pending.ended_at},
                True,
            )
            # Its chat is the schedule's last, as a run by hand's is (nothing of when it fires next changes): a crash
            # while the try works finds it there as the run cut (:meth:`_recover_cut`), not the task it tried again.
            await store.record_schedule_hand_run(record.id, now, thread_id, run.id)
        except DotError as exc:
            # The run started all the same; its watch writes how it ended.
            log.error(
                "schedule_status_not_written",
                schedule_id=record.id,
                run_id=run.id,
                error=exc.message,
                context=exc.context,
            )
        log.warning(
            "schedule_retried",
            schedule_id=record.id,
            run_id=run.id,
            from_run_id=pending.from_run_id,
            why=pending.why,
            thread_id=thread_id,
            same_chat=not made,
        )

    async def _paused_agent(self, record: ScheduleRecord) -> AgentRecord | None:
        """The agent ``record`` runs as when the owner paused it (item 465), else ``None``. An agent that cannot be read
        fails the firing: nothing starts for a pause that is not known."""
        if record.options.agent_id is None:
            return None
        agent: AgentRecord = await self._runtime.store.get_agent(record.options.agent_id)
        return None if agent.paused_at is None else agent

    async def _start_due(
        self,
        record: ScheduleRecord,
        occurrence: float,
        missed: int,
        now: float,
        zone: ZoneInfo,
        following: float | None,
        late: bool,
        overdue_s: float,
        woken: CheckVerdict | None,
    ) -> None:
        """Start the run of the firing of ``record`` that was due at ``occurrence``, once :meth:`_fire` let it, with the
        ``missed`` firings before it that are told: the firing is written (its next time, its chat) before the run
        starts, so a crash can lose a firing but never repeat one. ``woken`` is what the schedule's check said when it
        woke the firing (item 1855), which the run reads; ``None`` for a schedule without one."""
        store: Store = self._runtime.store
        if record.next_run_at is None:
            raise StoreError("a due schedule has no next run", {"schedule_id": record.id})
        # The schedule's next time as the store has it, which the claim compares; ``occurrence`` is the newest firing.
        due: float = record.next_run_at
        one_time: bool = record.cron is None
        # As the store had it when it was found due: what the claim below compares (the brief rewrites its goal).
        found: ScheduleRecord = record
        same_chat, digest = await self._history(record, zone)
        record, digest, title = await self._briefed(
            record, digest, now, zone, thread_title(f"{record.name} · {format_local(now, zone)}")
        )
        # After the cut to a title's length, so that a long name keeps it (item 1204); the brief's own title too.
        thread_id, made = await self._firing_chat(record, same_chat, f"{title}{firing_tail(late, missed)}")
        if not await store.claim_schedule_fire(record.id, due, found, now, following, thread_id):
            # Changed, switched off or deleted while the firing was being prepared: the owner's change stands.
            log.info("schedule_fire_dropped", schedule_id=record.id)
            if made:
                await self._drop_chat(record.id, thread_id)
            return
        if late:
            log.warning("schedule_fired_late", schedule_id=record.id, overdue_s=round(overdue_s), missed=missed)
        told: str = caught_up_goal(record.goal, occurrence, now, zone, missed) if late else record.goal
        started: RunRecord | None = None
        try:
            # Read inside the try (item 785): a store that fails here keeps a one-time schedule due, as a run that did
            # not start does, where the firing above had switched it off for good.
            carried: tuple[str, ...] = await self._carried(digest, made, thread_id)
            started = await self._start(*_with_check(record, firing_opening(told, digest), woken), thread_id, carried)
        except DotError as exc:
            if _chat_busy(exc) and not one_time:
                # A run going on in a recurring schedule's own chat (the owner's message in it, item 257) is no
                # failure: the firing is skipped, as one under the emergency stop is, and nobody is told of a fault
                # (item 723). A one-time schedule's is kept as a failure, whose wait spaces its next try.
                # Counted with the firings skipped in a row (item 1401), so that where the owner looks it says how many.
                skipped: int | None = await store.count_schedule_skip(record.id, f"skipped: {exc.message}")
                log.info("schedule_skipped_chat_busy", schedule_id=record.id, error=exc.message, skipped_in_row=skipped)
                return
            log.error("schedule_start_failed", schedule_id=record.id, error=exc.message, context=exc.context)
            failed: str = f"failed to start: {exc.message}"
            await store.set_schedule_status(record.id, failed)
            # A firing that failed to start is no skip: the skips in a row (item 1401) are over.
            await store.clear_schedule_skips(record.id)
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
        await self._track(record.id, run, self._level(record))
        if missed:
            # Once its run started: one that could not start is told as such, not as running late.
            await self._tell_missed(record, missed_status(missed, occurrence, late, zone))
        try:
            await store.mark_schedule_running(record.id, run.id)
            # The firings skipped in a row are over (item 1401), whether or not the run is still at work.
            await store.clear_schedule_skips(record.id)
            await record_firing_started(store, record.id)
            # The firing's run does the task: a try of an earlier one waiting for the schedule is dropped (item 1206).
            await supersede_stored(store, record.id)
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
        await self._adopt_resumed(record)
        if schedule_id in self._in_flight or schedule_id in self._starting:
            raise RunConflict(
                "a run of this schedule is already working",
                {"schedule_id": schedule_id, "run_id": self._in_flight.get(schedule_id)},
            )
        self._starting.add(schedule_id)
        try:
            # The owner asked for the run: its check runs for what it prints, and the run starts whatever it says;
            # a check that fails is the caller's error, before any chat is made (item 1855).
            woken: CheckVerdict | None = None
            if record.options.precheck is not None:
                woken = await self._check(record.options.precheck)
            zone: ZoneInfo = await schedule_zone(record.options, self._zone)
            same_chat, digest = await self._history(record, zone)
            out_of_time: str = thread_title(f"{record.name} · {format_local(now, zone)} · by hand")
            record, digest, title = await self._briefed(record, digest, now, zone, out_of_time)
            thread_id, made = await self._firing_chat(record, same_chat, title)
            goal: str = by_hand_goal(record.goal, now, zone)
            started: RunRecord | None = None
            try:
                # Inside the try, as a firing's (item 785): a store that fails here drops the chat made for the run.
                carried: tuple[str, ...] = await self._carried(digest, made, thread_id)
                checked: tuple[ScheduleRecord, str] = _with_check(record, firing_opening(goal, digest), woken)
                started = await self._start(*checked, thread_id, carried)
            finally:
                if started is None and made:
                    await self._drop_chat(record.id, thread_id)
            run: RunRecord = started
            await self._track(record.id, run, BY_HAND_LEVEL, by_hand=True)
            try:
                await store.record_schedule_hand_run(record.id, now, thread_id, run.id)
                # Its run does the task: a try waiting for the schedule is dropped (item 1206).
                await supersede_stored(store, record.id)
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

    async def _briefed(
        self, record: ScheduleRecord, digest: Digest, now: float, zone: ZoneInfo, title: str
    ) -> tuple[ScheduleRecord, Digest, str]:
        """The firing of ``record`` as its run takes it: the morning brief (item 570) fires with the goal built for it,
        what Dot knows of itself in the digest's place and the chat's own name; any other schedule as it is, with the
        ``digest`` and the ``title`` it was given."""
        briefing: BriefFiring | None = await prepare(self._runtime, record, now, zone)
        if briefing is None:
            # The evening check-in (item 930) fires as the brief does, with a record of the day.
            briefing = await prepare_checkin(self._runtime, record, now, zone)
        if briefing is None:
            return record, digest, title
        return briefing.record, briefing.context, briefing.title

    async def _carried(self, digest: Digest, made: bool, thread_id: str) -> tuple[str, ...]:
        """The earlier runs whose words a firing's run reads: the digest's in a chat made for it, else that chat's last
        run and the digest's. A schedule that goes on in its chat has no digest of earlier runs, so for it the digest's
        runs are none, except for the morning brief, whose digest names the runs its record of Dot quotes (item 570)."""
        if made:
            return digest.run_ids
        last: tuple[str, ...] = tuple(run.id for run in await self._runtime.store.list_runs(1, thread_id))
        return tuple(dict.fromkeys((*last, *digest.run_ids)))

    async def _start(self, record: ScheduleRecord, opening: str, thread_id: str, carried: tuple[str, ...]) -> RunRecord:
        """Start the run of a firing of ``record`` in the chat ``thread_id`` with the schedule's own choices and skills
        (item 666), as the owner's agent the schedule names, within that agent's bounds (item 462), or as Dot."""
        options: ScheduleOptions = record.options
        speed: Speed = "standard" if options.speed is None else options.speed
        asked: Effort | None = options.effort
        model: str | None = await self._firing_model(options)
        kept: Effort | None = await self._corrected_effort(record, model)
        if kept != asked:
            options = options.model_copy(update={"effort": kept})
        run: RunRecord = await self._started(record, options, opening, thread_id, carried, speed)
        if kept != asked and kept is not None and asked is not None:
            await self._runtime.bus.emit(
                run.id,
                SCHEDULE_EFFORT_EVENT,
                {"schedule_id": record.id, "name": record.name, "asked": asked, "kept": kept, "model": model},
                True,
            )
        return run

    async def _firing_model(self, options: ScheduleOptions) -> str | None:
        """The model a firing with ``options`` runs on, as the run takes it: the schedule's own, for an agent's
        schedule else the agent's, else ``None`` for the configured main model (:func:`agent_bounds`, so that the level
        is judged against the model that will run it). An agent that cannot be read leaves the schedule's own: the
        start that follows fails on it as it did."""
        if options.agent_id is None:
            return options.model
        try:
            agent: AgentRecord = await self._runtime.store.get_agent(options.agent_id)
        except NotFoundError:
            return options.model
        return agent_bounds(agent, options.model, None, None).model

    async def _corrected_effort(self, record: ScheduleRecord, model: str | None) -> Effort | None:
        """The level ``record``'s firing on ``model`` runs at: its own when that model offers it. When the model does
        not, the level the model takes for it is written into the schedule, once, so that the next firings find it
        offered and say nothing (item 1307); a model with no choice leaves the schedule as it is and runs at none."""
        options: ScheduleOptions = record.options
        if options.effort is None:
            return None
        kept: Effort | None = await self._manager.firing_effort(model, options.effort)
        if kept is not None and kept != options.effort:
            log.warning(
                "schedule_effort_corrected",
                schedule_id=record.id,
                model=model,
                asked=options.effort,
                kept=kept,
            )
            try:
                await self._runtime.store.set_schedule_effort(record.id, kept)
            except StoreError as exc:
                # The firing goes on at the corrected level; the next one corrects it again.
                log.error("schedule_effort_not_written", schedule_id=record.id, error=exc.message)
        return kept

    async def _started(
        self,
        record: ScheduleRecord,
        options: ScheduleOptions,
        opening: str,
        thread_id: str,
        carried: tuple[str, ...],
        speed: Speed,
    ) -> RunRecord:
        """The run of a firing of ``record`` with ``options``, as Dot or as the agent they name."""
        if options.agent_id is None:
            return await self._manager.start_firing(
                record.goal,
                opening,
                record.mode,
                options.model,
                thread_id,
                options.effort,
                speed,
                options.approvals,
                options.goal_mode,
                carried,
                options.skills,
                options.from_outside,
            )
        agent: AgentRecord = await self._runtime.store.get_agent(options.agent_id)
        return await self._manager.start_firing_for_agent(
            agent,
            record.goal,
            opening,
            record.mode,
            options.model,
            thread_id,
            options.effort,
            speed,
            options.approvals,
            options.goal_mode,
            carried,
            options.skills,
            options.from_outside,
        )

    def _level(self, record: ScheduleRecord) -> NotifyLevel:
        """How the ends of ``record``'s runs are told (item 461): its own level, else the configured one."""
        return notify_level(record.options.notify, self._runtime.config.scheduler.notify)

    async def _track(self, schedule_id: str, run: RunRecord, level: NotifyLevel, by_hand: bool = False) -> None:
        """Keep ``run`` as the schedule's run at work until it ends; its watch, which starts first, frees the schedule
        and writes the end even when the store fails a write after this, and tells the end at ``level``, read when
        the run started, so a schedule deleted meanwhile still tells it. An emergency stop engaged while it was being
        started ends it as one."""
        if await _emergency_stop_engaged():
            log.warning("schedule_run_stopped_emergency_stop", schedule_id=schedule_id, run_id=run.id)
            self._manager.end_by_emergency_stop(run.id)
        self._in_flight[schedule_id] = run.id
        watcher: asyncio.Task[None] = asyncio.create_task(
            self._watch(schedule_id, run.id, level, by_hand), name=f"watch-{run.id}"
        )
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
        why; not again when its status said so already, so a schedule that keeps failing the same way is told once,
        and not at all when its level tells nothing (item 461)."""
        if record.last_status == status or self._level(record) == "never":
            return
        missed: ScheduleMissed = ScheduleMissed(schedule_id=record.id, name=record.name, status=status)
        await self._runtime.bus.emit(SYSTEM_RUN_ID, SCHEDULE_MISSED_EVENT, missed.model_dump(), False)

    async def _start_session(self, campaign_id: str) -> None:
        try:
            await self._manager.start_session(campaign_id)
        except DotError as exc:
            log.error("campaign_session_start_failed", campaign_id=campaign_id, error=exc.message, context=exc.context)

    async def _watch(self, schedule_id: str, run_id: str, level: NotifyLevel, by_hand: bool = False) -> None:
        """Write the outcome of a run to its schedule once the run is over, with its row of failed runs (item 458); a
        repeating schedule whose runs failed so many times in a row is switched off, and the owner told once. The end
        is told at ``level`` (item 461) even when the write or the switch-off failed. Then the task is tried once more
        when its end calls for it (item 1206, :meth:`_recover`)."""
        try:
            finished: RunRecord = await self._manager.wait(run_id)
            failed_in_row: int | None = None
            try:
                failed_in_row = await self._runtime.store.record_schedule_outcome(schedule_id, finished.status)
            except DotError as exc:
                log.error(
                    "schedule_outcome_not_written",
                    schedule_id=schedule_id,
                    run_id=run_id,
                    error=exc.message,
                    context=exc.context,
                )
            try:
                if finished.status == "failed" and failed_in_row is not None:
                    await self._stop_if_failing(schedule_id, failed_in_row)
            finally:
                await self._tell_end(schedule_id, finished, level, failed_in_row, by_hand)
            await self._recover(schedule_id, finished)
        finally:
            self._in_flight.pop(schedule_id, None)

    async def _tell_end(
        self, schedule_id: str, finished: RunRecord, level: NotifyLevel, failed_in_row: int | None, by_hand: bool
    ) -> None:
        """Tell the end of a schedule's run when ``level`` tells it: what it needs from the owner is in its last
        ``run_finished`` event."""
        ended: EventRecord | None = await self._runtime.store.last_event(finished.id, "run_finished")
        needs: object = None if ended is None else ended.data.get("needs")
        # A run started by hand is told whatever it found: the owner asked for it (item 880).
        quiet: bool = not by_hand and await said_nothing_new(self._runtime.store, finished.id)
        if not tells(level, finished.status, needs if isinstance(needs, str) else "", failed_in_row, quiet):
            log.info("schedule_end_not_told", schedule_id=schedule_id, run_id=finished.id, nothing_new=quiet)
            return
        if finished.status == "done" and await is_brief(self._runtime.store, schedule_id):
            # The brief is told in its own words, its first line, and opens in its chat (item 570).
            await tell_brief(self._runtime.bus, finished)
            return
        if finished.status == "done" and await is_checkin(self._runtime.store, schedule_id):
            await tell_checkin(self._runtime.bus, finished)
            return
        told: ScheduleRunEnded = ScheduleRunEnded(
            schedule_id=schedule_id, run_id=finished.id, status=finished.status, result=finished.result
        )
        await self._runtime.bus.emit(SYSTEM_RUN_ID, SCHEDULE_RUN_ENDED_EVENT, told.model_dump(), False)

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
        status: str = f"switched off: its last {failed_in_row} tasks failed"
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
