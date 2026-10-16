"""Run lifecycle: start, queue, steer, cancel and finish agent runs, each in its own asyncio task."""

import asyncio
import sqlite3
import time
from collections.abc import Awaitable, Callable, Collection, Coroutine, Iterator, Sequence
from contextlib import AbstractAsyncContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from dotagent.agent.attachments import StoredAttachment, UserInput, attachment_summary, load_attachments
from dotagent.agent.campaign import (
    BRIEF_ENTRIES,
    BRIEF_QUESTIONS,
    CAMPAIGN_ENDED_EVENT,
    Brief,
    BriefQuestion,
    CampaignEnded,
    LastSession,
    campaign_browser,
    next_session_at,
    render_brief,
    sessions_without_progress,
)
from dotagent.agent.gate import HANDBACK_EVENT, TAINT_EVENTS, TAKEOVER_EVENT, RunGate, tainted_after
from dotagent.agent.keep_going import keep_going_ask
from dotagent.agent.learning_health import (
    LearningOutcome,
    LearningWork,
    failed_with,
    quiet_limit,
    record_learning,
)
from dotagent.agent.lessons import LESSONS_FAILED, LessonCase, dismiss_lesson, learn, save_lesson, undo_lesson
from dotagent.agent.loop import AgentRun, RunEnd, RunOpening, browser_of, run_agent
from dotagent.agent.owners import RunOwner, clear_gone_owners, owner_alive
from dotagent.agent.plan import EMPTY_PLAN, PLAN_EVENT, plan_from_event
from dotagent.agent.playbooks import PLAYBOOK_FAILED, learn_playbook, playbook_case
from dotagent.agent.reflection import reflect
from dotagent.agent.reflection_record import Reflection
from dotagent.agent.resume import (
    RESUME_SOURCE,
    CutOff,
    InterruptedCall,
    Resumption,
    can_resume,
    cut_off_by,
    interrupted_calls,
    resume_note,
    resumes_on_start,
)
from dotagent.agent.review import reviewer_for, shorten_reason
from dotagent.browser.attach import read_debug_endpoint
from dotagent.browser.session import BrowserSession
from dotagent.config import ResumeMode
from dotagent.core.errors import (
    ConfigError,
    DotError,
    FailSafeTriggered,
    InvalidValueError,
    NotFoundError,
    RunCancelled,
    RunConflict,
    StoreError,
)
from dotagent.core.logs import get_logger
from dotagent.core.paths import chrome_data_dir, failsafe_path, run_owners_dir
from dotagent.core.text import redact_json, redact_secrets, truncate_tail
from dotagent.core.types import JsonObject
from dotagent.llm.opencode import OPENCODE_PROVIDER
from dotagent.llm.router import ModelRef, offers_fast_mode, parse_model_ref
from dotagent.llm.types import ONE_MESSAGE_LEVELS, Effort, ModelInfo, OneMessage, Speed, user_message
from dotagent.memory.service import SavedMemory
from dotagent.opencode.run import run_in_opencode
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.scheduler.timing import next_firing
from dotagent.store.db import Store
from dotagent.store.endings import (
    PREFIX_EMERGENCY_STOP,
    PREFIX_INTERNAL_ERROR,
    RESULT_COMMAND_DIED,
    RESULT_EMERGENCY_STOPPED,
    RESULT_NOT_STARTED,
    RESULT_PROCESS_ENDED,
    RESULT_SHUTDOWN,
    RESULT_STOPPED,
    Ending,
    classify_ending,
)
from dotagent.store.models import (
    WORKING_STATUSES,
    AgentRecord,
    ApprovalRecord,
    BrowserMode,
    CampaignEntry,
    CampaignRecord,
    ChatChoices,
    EventRecord,
    QueuedMessage,
    RunMode,
    RunRecord,
    RunStatus,
    ThreadRecord,
    WorkingRun,
)
from dotagent.terminal.session import TerminalSession, ended_notice
from dotagent.tools.context import DelegateOutcome
from dotagent.tools.delegate import DELEGATE_SOURCE
from dotagent.tools.meta import ASK_USER_TOOL
from dotagent.tools.team import TEAM_SOURCE

log = get_logger(__name__)

# The live event that tells the pages a chat's queue changed (item 148); it carries the chat's id.
QUEUE_CHANGED_EVENT: str = "queue_changed"
# The live event that tells the pages a chat's terminal sessions changed (item 229); it carries the chat's id.
TERMINALS_CHANGED_EVENT: str = "terminals_changed"
# The source of the message that tells a chat that its command in the background ended (item 229).
BACKGROUND_SOURCE: str = "background"
# How often the daemon stores the ends the store refused and looks for the runs of dotx commands that died
# (:meth:`RunManager.watch_commands`).
COMMAND_CHECK_S: float = 30.0
# How often the daemon tells its pages what the runs of dotx commands store (:meth:`RunManager.relay_elsewhere`), and
# how often a process that drives runs reads the stops and the answers asked of them through another process
# (:meth:`RunManager._answer_asks`).
RELAY_S: float = 0.5
ASK_CHECK_S: float = 0.5
# The most stored events one relay tells; the rest follow with the next.
_RELAY_PAGE: int = 500
# Why a pending approval expired: its run was closed when the daemon started, or when its dotx command was found dead.
_EXPIRED_ON_START: str = "daemon restarted"
_EXPIRED_COMMAND_DIED: str = "its dotx command died"
_TITLE_CHARS: int = 60
_RESULT_CHARS: int = 20_000
# How much of a run's result a refusal to resume it shows.
_REFUSED_RESULT_CHARS: int = 200
# The earlier runs of a thread whose goals count as the user's instructions to a reviewer.
_THREAD_INSTRUCTION_RUNS: int = 30
# The approval modes from the strictest to the loosest: the user answers, the review model answers, no one is asked.
_APPROVALS_STRICTEST_FIRST: tuple[ApprovalMode, ...] = ("ask", "auto", "yolo")


@dataclass(frozen=True)
class RunHandle:
    run_id: str
    thread_id: str
    parent_run_id: str | None
    task: asyncio.Task[None]
    gate: RunGate
    cancel: asyncio.Event
    inbox: asyncio.Queue[UserInput]
    finished: asyncio.Event


@dataclass(frozen=True)
class _LeftEnd:
    """The end of a run that the store refused when the run ended, as it is to be stored: its status, its result, what
    it needs from the owner, and the data of its ``run_finished`` event."""

    run: RunRecord
    status: RunStatus
    result: str
    needs: str
    data: JsonObject


def thread_title(goal: str) -> str:
    lines: list[str] = goal.strip().splitlines()
    first_line: str = lines[0] if lines else "Untitled"
    return first_line if len(first_line) <= _TITLE_CHARS else f"{first_line[: _TITLE_CHARS - 1]}…"


def _answer_text(question: ApprovalRecord, answer: str) -> str:
    """The user's message that carries the answer to a question a run left."""
    return f"Answer to your question “{question.summary}”: {answer}"


def stricter_approvals(first: ApprovalMode, second: ApprovalMode) -> ApprovalMode:
    """The stricter of two approval modes."""
    return min(first, second, key=_APPROVALS_STRICTEST_FIRST.index)


def _offers(info: ModelInfo, effort: Effort) -> bool:
    """Whether the model ``info`` tells of offers the level ``effort``: a model with fewer than two has no choice."""
    return len(info.efforts) >= 2 and effort in info.efforts


def _check_speed(ref: ModelRef, speed: Speed) -> None:
    """Fast mode is asked only of a model that has it (item 223)."""
    if speed == "fast" and not offers_fast_mode(ref):
        raise ConfigError("the model does not offer fast mode", {"model": ref.label, "speed": speed})


def goal_mode_asked(goal: str, mode: RunMode, goal_mode: bool | None) -> bool | None:
    """The goal mode of a run a person starts: on for a task or research run whose goal asks it to keep going until it
    is done (item 132), on every model, whatever was chosen; otherwise as chosen. A delegated run, a campaign's session,
    a resumed run and one that an answer starts do not come here: they keep the goal mode they are given."""
    return True if mode != "chat" and keep_going_ask(goal) is not None else goal_mode


def _crash(exc: Exception) -> str:
    """An unexpected error as the owner reads it on a run's page: its kind and its words, secrets masked."""
    return shorten_reason(redact_secrets(f"{type(exc).__name__}: {exc}"))


async def _uncancelled[T](work: Coroutine[object, object, T]) -> T:
    """Run ``work`` to its end even when the caller is cancelled meanwhile (a second stop of a run while its end is
    being recorded), then raise the cancellation (item 116); returns what ``work`` returns."""
    task: asyncio.Task[T] = asyncio.ensure_future(work)
    cancelled: bool = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return task.result()


async def _to_its_end[T](work: Coroutine[object, object, T]) -> T:
    """Run ``work`` to its end even when the caller is cancelled meanwhile, and return what it returns or raise what it
    raises: a cancellation that comes meanwhile is dropped, as what it came to stop is over once ``work`` is (a stop of
    a run while its end is being recorded, the code review's O5)."""
    task: asyncio.Task[T] = asyncio.ensure_future(work)
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise


class RunManager:
    """Owns the live runs of one process. At most ``max_concurrent_runs`` execute at the same time.

    Runs that other runs wait for (a delegated run, a message to an agent) do not count against that limit: their parent
    holds a place while it waits for them, so a full set of waiting parents would otherwise keep every one of them from
    ever starting.
    """

    def __init__(self, runtime: Runtime) -> None:
        self._runtime: Runtime = runtime
        self._slots: asyncio.Semaphore = asyncio.Semaphore(runtime.config.agent.max_concurrent_runs)
        self._handles: dict[str, RunHandle] = {}
        # Threads that a run is being launched on and whose handle is not registered yet.
        self._claimed: set[str] = set()
        self._stopping: bool = False
        # Active runs the owner's emergency stop is ending (:meth:`emergency_stop`).
        self._emergency: set[str] = set()
        # Active runs that the end of the ``dotx`` command driving them is cutting off (:meth:`cut_off`).
        self._cutting: set[str] = set()
        # How many emergency stops there were: a run being launched as one comes ends as one too (:meth:`_launch`).
        self._stops: int = 0
        # This process's hold on the runs it drives, taken with its first run (:mod:`dotagent.agent.owners`).
        self._owner: RunOwner | None = None
        # Runs that ended and whose lessons are being drawn (:mod:`dotagent.agent.lessons`), by run id.
        self._learning: dict[str, asyncio.Task[None]] = {}
        # Runs that ended whose playbook is being written or whose use of playbooks is being recorded, by run id.
        self._distilling: dict[str, asyncio.Task[None]] = {}
        # Held while a lesson is saved or dismissed, by the owner or by ``auto``, so that each is decided once.
        self._deciding: asyncio.Lock = asyncio.Lock()
        # Held while an approval or a question is decided, so that the answer to a question a run left goes on once.
        self._answering: asyncio.Lock = asyncio.Lock()
        # Held while the next message of a chat's queue is started, so that each starts once (:meth:`_drain`).
        self._draining: asyncio.Lock = asyncio.Lock()
        # The ends the store refused, by run id, until they are stored (:meth:`store_left_ends`).
        self._left_ends: dict[str, _LeftEnd] = {}
        # The claims on queued messages the store refused to end (:meth:`_end_claim`), by chat, with the store's way to
        # end each: its message started (``Store.drop_claimed``) or could not (``Store.hold_claimed``).
        self._left_claims: dict[str, Callable[[str], Awaitable[None]]] = {}
        # The runs at work that other processes (dotx commands) drive, as the relay saw them last
        # (:meth:`relay_elsewhere`).
        self._elsewhere: frozenset[str] = frozenset()
        # The runs of other processes whose end the relay told, and whose chat's queue a store refused to move so far.
        self._unfollowed: set[str] = set()
        # Reads the stops and the answers asked of this process's runs through another process, while it has runs.
        self._asks: asyncio.Task[None] | None = None
        # Whether the daemon moves the queues of the chats whose runs this process ends (:meth:`leave_queues`).
        self._leaves_queues: bool = False
        # The ends of the commands in the background are told to their chats from here (item 229).
        runtime.terminals.watch(self)
        # The model a run that names none starts on: ``[models] main``, or the one the owner made the default since
        # (:meth:`set_main_model`).
        self._main_model: str = runtime.config.models.main
        # Whether the daemon's next start resumes on its own the runs it cut off: ``[agent] resume``, or what the owner
        # chose since (:meth:`set_resume_mode`).
        self._resume_mode: ResumeMode = runtime.config.agent.resume

    async def recover(self) -> int:
        """Close out what processes that are gone left behind: the runs they left active, the approvals of runs no
        longer active, and their lock files; and hold the queue of each chat (:meth:`queue`) whose run the daemon's stop
        did not cut. The runs of a process that lives (``dotx run`` in a terminal) go on, with the approvals they wait
        on. Returns the number of runs closed."""
        store: Store = self._runtime.store
        directory: Path = run_owners_dir()
        gone: list[str] = []
        for owner in await store.active_run_owners():
            if owner is not None and not await asyncio.to_thread(owner_alive, directory, owner):
                gone.append(owner)
        interrupted: int = await store.mark_interrupted_runs(gone)
        await store.expire_pending_approvals(_EXPIRED_ON_START)
        await store.forget_stops_of_ended_runs()
        await self._hold_claims_of_gone(directory)
        # A stop of the daemon is not the owner's stop of the chat: a queue whose run the stop cut goes on once that
        # run, continued, ends done (:meth:`_follow_queue`). Any other waits for the owner to resume it: its run
        # failed, was stopped, was cut by a crash, works in a process that lives, or ended done as the daemon stopped.
        for thread_id in await store.queued_threads():
            newest: list[RunRecord] = await store.list_runs(1, thread_id)
            if not newest or cut_off_by(newest[0]) != "daemon_shutdown":
                await store.hold_queue(thread_id)
        await asyncio.to_thread(clear_gone_owners, directory)
        return interrupted

    async def close_runs_of_dead_commands(self) -> int:
        """Close the runs that ``dotx`` commands (``run``, ``resume``, ``chat``) left active when they died without
        recording their end (killed, or crashed): failed, with a result that names the command rather than the daemon,
        and ``run_finished`` stored with that end and told to the pages that follow them. Their pending approvals
        expire, the queue of their chat is held until the owner resumes it, as after any end that is not done, and a
        campaign whose session one was plans its next session, as after any end of a session (:meth:`_follow_session`).
        The files of the owners that are gone go, those of commands that ended well too. For the daemon, every
        ``COMMAND_CHECK_S`` while it runs; :meth:`recover` closes, when it starts, what any process that is gone left.
        Returns the number of runs closed."""
        store: Store = self._runtime.store
        directory: Path = run_owners_dir()
        gone: list[str] = []
        for owner in await store.active_run_owners():
            # This process holds its own owner's lock: its runs are never taken for a dead command's.
            if owner is not None and not await asyncio.to_thread(owner_alive, directory, owner):
                gone.append(owner)
        await self._hold_claims_of_gone(directory)
        told: list[EventRecord] = await store.close_runs_of(gone, RESULT_COMMAND_DIED) if gone else []
        for finished in told:
            self._runtime.bus.publish(finished)
        if told:
            await store.expire_pending_approvals(_EXPIRED_COMMAND_DIED)
        for finished in told:
            closed: RunRecord = await store.get_run(finished.run_id)
            await self._follow_queue(closed, "failed")
            if closed.campaign_id is not None and not self._stopping:
                # The command recorded nothing, so the session left no word of what it needs.
                await self._follow_session(
                    closed, closed.campaign_id, classify_ending(closed.status, closed.result), ""
                )
        await asyncio.to_thread(clear_gone_owners, directory)
        return len(told)

    async def relay_elsewhere(self) -> None:
        """Until cancelled, every ``RELAY_S``, tell this process's followers (the pages of the daemon) what the runs of
        other processes (``dotx`` commands) store, as the events of its own runs are told (:meth:`_relay_once`),
        from the newest event at its start on. A relay that the store or the files of the owners refuse is logged, and
        the next goes on from where it stopped."""
        cursor: int | None = None
        while True:
            try:
                if cursor is None:
                    cursor = await self._runtime.store.last_event_id()
                else:
                    cursor = await self._relay_once(cursor)
            except DotError as exc:
                log.warning("relay_not_done", after=cursor, error=exc.message, context=exc.context)
            except (sqlite3.Error, OSError) as exc:
                log.warning("relay_not_done", after=cursor, error=str(exc), error_type=type(exc).__name__)
            await asyncio.sleep(RELAY_S)

    async def _relay_once(self, after: int) -> int:
        """Publish the events that other processes stored after the event ``after`` (one this process told already
        passes: it stored it for them, as when it closed a dead command's run), keep the runs at work those that live
        drive (:meth:`elsewhere_run_ids`), and move the queue of a chat whose run of theirs ended, as the end of a run
        of this process does (:meth:`_follow_queue`); a move the store refuses is tried again at the next relay.
        Returns the id of the event to go on after."""
        store: Store = self._runtime.store
        mine: str = self._owner_id()
        upto: int = await store.last_event_id()
        events: list[EventRecord] = await store.events_of_others(mine, after, upto, _RELAY_PAGE)
        directory: Path = run_owners_dir()
        working: list[WorkingRun] = await store.working_runs_of_others(mine)
        alive: set[str] = set()
        for owner in {run.owner for run in working}:
            if await asyncio.to_thread(owner_alive, directory, owner):
                alive.add(owner)
        # Before the events go out: a follower that hears one finds its run at work.
        self._elsewhere = frozenset(run.run_id for run in working if run.owner in alive)
        ended: list[str] = []
        for event in events:
            if self._runtime.bus.told(event.id):
                continue
            self._runtime.bus.publish(event)
            if event.kind == "run_finished":
                ended.append(event.run_id)
        self._unfollowed.update(ended)
        for run_id in sorted(self._unfollowed):
            try:
                run: RunRecord = await store.get_run(run_id)
                await self._follow_queue(run, run.status)
            except NotFoundError:
                # Its chat was deleted meanwhile: no queue is left to move.
                pass
            except (DotError, sqlite3.Error, OSError) as exc:
                log.warning("queue_not_followed_yet", run_id=run_id, error=str(exc), error_type=type(exc).__name__)
                continue
            except Exception:
                log.exception("queue_not_followed", run_id=run_id)
            self._unfollowed.discard(run_id)
        return events[-1].id if len(events) == _RELAY_PAGE else upto

    async def watch_commands(self) -> None:
        """Every ``COMMAND_CHECK_S`` until cancelled, store the ends the store refused (:meth:`store_left_ends`) and
        close the runs of dead ``dotx`` commands (:meth:`close_runs_of_dead_commands`); a check that fails, on the store
        or on the files of the owners (a read of the store fails with sqlite3's own error), is logged and the next one
        goes on."""
        while True:
            await asyncio.sleep(COMMAND_CHECK_S)
            try:
                stored: int = await self.store_left_ends()
            except DotError as exc:
                log.warning("left_ends_not_stored", runs=len(self._left_ends), error=exc.message, context=exc.context)
            except (sqlite3.Error, OSError) as exc:
                log.warning(
                    "left_ends_not_stored", runs=len(self._left_ends), error=str(exc), error_type=type(exc).__name__
                )
            else:
                if stored:
                    log.info("left_ends_stored", runs=stored)
            try:
                closed: int = await self.close_runs_of_dead_commands()
            except DotError as exc:
                log.warning("dead_commands_not_checked", error=exc.message, context=exc.context)
                continue
            except (sqlite3.Error, OSError) as exc:
                log.warning("dead_commands_not_checked", error=str(exc), error_type=type(exc).__name__)
                continue
            if closed:
                log.info("runs_of_dead_commands_closed", runs=closed)

    async def _hold_claims_of_gone(self, directory: Path) -> None:
        """End the claims on queued messages (:meth:`Store.claim_queued`) of the processes that are gone, and hold the
        queues of their chats until the owner resumes them."""
        store: Store = self._runtime.store
        gone: list[str] = [
            owner for owner in await store.claim_owners() if not await asyncio.to_thread(owner_alive, directory, owner)
        ]
        if gone:
            held: int = await store.hold_claims_of(gone)
            log.warning("queue_claims_of_gone_processes_held", claims=held)

    async def store_left_ends(self) -> int:
        """Store the ends that the store refused when their runs ended (:meth:`_record_end`, :meth:`_end_unstarted`),
        each as it was to be stored then, tell the pages, and move on what follows it: the chat's queue, a campaign's
        next session. An end the store refuses again waits for the next try; the daemon's next start closes the runs
        whose ends are still left (:meth:`recover`). Returns the number of ends stored."""
        stored: int = 0
        for left in list(self._left_ends.values()):
            ended: EventRecord = await self._runtime.store.end_run(left.run.id, left.status, left.result, left.data)
            del self._left_ends[left.run.id]
            self._runtime.bus.publish(ended)
            stored += 1
            if left.run.campaign_id is not None and not self._stopping:
                await self._follow_session(
                    left.run, left.run.campaign_id, classify_ending(left.status, left.result), left.needs
                )
            if not self._stopping:
                try:
                    await self._follow_queue(left.run, left.status)
                except Exception:
                    log.exception("queue_not_followed", run_id=left.run.id, thread_id=left.run.thread_id)
        return stored

    def active_run_ids(self) -> list[str]:
        return list(self._handles)

    def main_model(self) -> str:
        """The model a run that names none starts on (:meth:`set_main_model`)."""
        return self._main_model

    def set_main_model(self, model: str) -> None:
        """Make ``model`` the one a run that names none starts on from now on (item 199); the runs under way keep
        theirs, and so does a chat that has its own (``ChatChoices``)."""
        self._main_model = model
        log.info("main_model_set", model=model)

    def resume_mode(self) -> ResumeMode:
        """Whether the daemon's next start resumes on its own the runs it cut off (:meth:`set_resume_mode`)."""
        return self._resume_mode

    def set_resume_mode(self, mode: ResumeMode) -> None:
        """Have the daemon's next start resume on its own the runs it cut off (``"safe"``: :meth:`resume_safe`) or
        leave them to the owner (``"off"``), as the config file says once the owner chose (item 231); this start's
        runs were resumed or left as it began."""
        self._resume_mode = mode
        log.info("resume_mode_set", mode=mode)

    def elsewhere_run_ids(self) -> list[str]:
        """The runs at work that other processes drive (``dotx`` commands), as the relay saw them last; none unless this
        process relays them (:meth:`relay_elsewhere`)."""
        return sorted(self._elsewhere)

    def leave_queues(self) -> None:
        """Leave the queues of the chats whose runs this process ends to the daemon, which moves each as it sees the
        end (:meth:`relay_elsewhere`): a ``dotx`` command ends with its run, and a queued message started there would
        be cut off with it. Until a daemon runs, such a queue waits; the daemon holds it when it starts
        (:meth:`recover`). Nor does such a process tell a chat of a command in the background: the command would end
        with the process, so it opens none (item 229)."""
        self._leaves_queues = True
        self._runtime.terminals.unwatch(self)

    @contextmanager
    def deleting_threads(self, thread_ids: Collection[str]) -> Iterator[None]:
        """Keep new runs off ``thread_ids`` while they are deleted. A thread with a run under way or about to start
        stops it: ``RunConflict`` names them, and nothing is held. The check and the claim have no await between
        them, as in :meth:`_claim_thread`."""
        wanted: set[str] = set(thread_ids)
        busy: list[str] = sorted(
            thread_id
            for thread_id in wanted
            if thread_id in self._claimed or any(handle.thread_id == thread_id for handle in self._handles.values())
        )
        if busy:
            named: list[JsonValue] = list(busy)
            runs: list[JsonValue] = list(
                sorted(handle.run_id for handle in self._handles.values() if handle.thread_id in wanted)
            )
            raise RunConflict("chats with a run under way cannot be deleted", {"thread_ids": named, "run_ids": runs})
        self._claimed.update(wanted)
        try:
            yield
        finally:
            self._claimed.difference_update(wanted)

    async def start(
        self,
        goal: str,
        mode: RunMode,
        model: str | None,
        thread_id: str | None,
        source: str,
        parent_run_id: str | None,
        effort: Effort | None,
        speed: Speed,
        once: OneMessage | None,
        approvals: ApprovalMode | None,
        goal_mode: bool | None,
        files: tuple[StoredAttachment, ...],
    ) -> RunRecord:
        """Queue a run. ``model``, ``effort``, ``approvals`` and ``goal_mode`` fall back to the configured ones when
        ``None``; ``files`` are the attachments that come with the goal; ``once`` is a choice for this message only
        (item 251), whose level the run asks for instead of ``effort``, which its chat keeps.

        An ``effort`` that is given has to be one of the levels the model offers, and ``speed`` can be ``fast`` only
        for a model with fast mode (item 223). ``goal_mode`` is for task and research runs: asking for it on a chat is
        refused, and the configured default does not apply to a chat. A task or research run whose goal asks it to
        keep going until it is done runs in goal mode whatever was chosen (see :func:`goal_mode_asked`).
        """
        ref: ModelRef = parse_model_ref(model if model is not None else self._main_model)
        _check_speed(ref, speed)
        if effort is not None:
            await self._check_effort(ref, effort)
        await self._check_once(ref, once)
        run, _ = await self._launch(
            goal,
            mode,
            model,
            thread_id,
            source,
            parent_run_id,
            effort,
            speed,
            approvals,
            goal_mode_asked(goal, mode, goal_mode),
            RunOpening(text=goal, files=files, resumed=None, once=once, carried=()),
            None,
            None,
        )
        return run

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
        """Queue the run of a schedule's firing in the chat ``thread_id`` (item 257). ``goal`` is the schedule's, the
        owner's words, which the run keeps, its reviewer takes as the owner's instruction and its chat shows;
        ``opening`` is its first message to the model: the goal, after the note of a late start and the digest of the
        schedule's earlier runs (``scheduler/history.py``). ``carried`` are the earlier runs whose words the run reads,
        in the digest or in its chat: it starts tainted when one of them ended so. Goal mode is asked by the goal
        alone; fast mode only of a model that has it (item 291). The rest is as for :meth:`start`."""
        ref: ModelRef = parse_model_ref(model if model is not None else self._main_model)
        _check_speed(ref, speed)
        if effort is not None:
            await self._check_effort(ref, effort)
        run, _ = await self._launch(
            goal,
            mode,
            model,
            thread_id,
            SCHEDULE_SOURCE,
            None,
            effort,
            speed,
            approvals,
            goal_mode_asked(goal, mode, goal_mode),
            RunOpening(text=opening, files=(), resumed=None, once=None, carried=carried),
            None,
            None,
        )
        return run

    async def start_for_agent(
        self,
        agent: AgentRecord,
        goal: str,
        mode: RunMode,
        model: str | None,
        thread_id: str | None,
        source: str,
        effort: Effort | None,
        speed: Speed,
        once: OneMessage | None,
        approvals: ApprovalMode | None,
        goal_mode: bool | None,
        files: tuple[StoredAttachment, ...],
    ) -> RunRecord:
        """Queue a run of ``agent`` (item 86): what the request leaves out, the agent's own bounds fill in (its model,
        effort and approvals), and what the agent leaves out, the configured ones; the run carries the agent's id and
        the loop adds its instructions to the prompt and keeps its tools. Goal mode and ``once`` are as for
        :meth:`start`."""
        model_ref: str | None = model if model is not None else (agent.model if agent.model != "" else None)
        level: Effort | None = effort if effort is not None else agent.effort
        approval_mode: ApprovalMode | None = approvals if approvals is not None else agent.approvals
        agent_ref: ModelRef = parse_model_ref(model_ref if model_ref is not None else self._main_model)
        _check_speed(agent_ref, speed)
        if level is not None:
            await self._check_effort(agent_ref, level)
        await self._check_once(agent_ref, once)
        run, _ = await self._launch(
            goal,
            mode,
            model_ref,
            thread_id,
            source,
            None,
            level,
            speed,
            approval_mode,
            goal_mode_asked(goal, mode, goal_mode),
            RunOpening(text=goal, files=files, resumed=None, once=once, carried=()),
            None,
            agent.id,
        )
        return run

    async def start_campaign(
        self,
        goal: str,
        mode: RunMode,
        model: str | None,
        thread_id: str | None,
        source: str,
        effort: Effort | None,
        speed: Speed,
        once: OneMessage | None,
        approvals: ApprovalMode | None,
        goal_mode: bool | None,
        files: tuple[StoredAttachment, ...],
        browser: BrowserMode | None,
    ) -> RunRecord:
        """Start a campaign on ``goal`` ("Keep going until done") with its opening session: a run of ``source`` on
        ``thread_id`` (a new thread when ``None``), which becomes the campaign's conversation. Every session keeps the
        model, reasoning level, speed, choice for one message (item 251: the campaign is that message's work, and the
        level the choice asks for is the campaign's) and approval mode the opening one starts with (``None`` takes the
        configured one), and works in the Chrome ``browser`` names (``None``: ``[agent] campaign_browser``); a campaign
        that asks for the owner's Chrome is refused while that Chrome is closed or does not allow the connection, so
        that its sessions do not fail one after the other.

        A campaign is a task, so a chat or a research run is refused. Goal mode is refused too: ``campaign_achieved``
        checks the campaign's outcome, and a session ends when its own work is done.
        """
        if mode != "task":
            raise ConfigError("a campaign is a task: only a task run can keep going until done", {"mode": mode})
        if goal_mode is True:
            raise ConfigError(
                "goal mode does not apply to a campaign: campaign_achieved checks its outcome", {"goal_mode": goal_mode}
            )
        runtime: Runtime = self._runtime
        model_ref: str = model if model is not None else self._main_model
        _check_speed(parse_model_ref(model_ref), speed)
        if effort is not None:
            await self._check_effort(parse_model_ref(model_ref), effort)
        await self._check_once(parse_model_ref(model_ref), once)
        level: Effort = runtime.config.models.effort if effort is None else effort
        approval_mode: ApprovalMode = runtime.config.policy.approvals if approvals is None else approvals
        chrome: BrowserMode = runtime.config.agent.campaign_browser if browser is None else browser
        if chrome == "attach":
            await asyncio.to_thread(read_debug_endpoint, chrome_data_dir())
        thread: ThreadRecord = (
            await runtime.store.create_thread(thread_title(goal), source)
            if thread_id is None
            else await runtime.store.get_thread(thread_id)
        )
        campaign: CampaignRecord = await runtime.store.create_campaign(
            thread_title(goal),
            goal,
            thread.id,
            model_ref,
            level if once is None else ONE_MESSAGE_LEVELS[once],
            speed,
            once,
            approval_mode,
            [file.path for file in files],
            chrome,
        )
        try:
            run, _ = await self._launch(
                goal,
                "task",
                model_ref,
                thread.id,
                source,
                None,
                level,
                speed,
                approval_mode,
                False,
                RunOpening(text=goal, files=files, resumed=None, once=once, carried=()),
                campaign.id,
                None,
            )
        except BaseException:
            # A campaign whose opening session never started was never under way, nor was a thread made for it,
            # whatever stopped the start: one of Dot's errors, a fault, the request's cancel. Kept, it would start at
            # the daemon's next start (:meth:`resume_campaigns`) with no one having asked again.
            await _uncancelled(self._forget_campaign(campaign.id, None if thread_id is not None else thread.id))
            raise
        log.info("campaign_started", campaign_id=campaign.id, run_id=run.id)
        return run

    async def _forget_campaign(self, campaign_id: str, made_thread_id: str | None) -> None:
        """Delete a campaign whose opening session never started, and the thread made for it when one was. What cannot
        be deleted (the store fails, or the session's failed start is not stored yet) is logged, not raised: the error
        of the start is the one its caller raises."""
        try:
            await self._runtime.store.delete_campaign(campaign_id)
            if made_thread_id is not None:
                await self._runtime.store.delete_threads([made_thread_id])
        except DotError as exc:
            log.error("campaign_not_forgotten", campaign_id=campaign_id, error=exc.message, context=exc.context)

    async def start_session(self, campaign_id: str) -> RunRecord:
        """Start the next session of an active campaign in a thread of its own, with the brief as its opening message
        and the campaign's settings; for the scheduler, once the session is due. Refused while a session of the campaign
        is still working, in this process or another. When the run cannot start, the session is tried again after
        ``[agent] campaign_gap_minutes``."""
        runtime: Runtime = self._runtime
        campaign: CampaignRecord = await runtime.store.get_campaign(campaign_id)
        if campaign.status != "active":
            raise RunConflict("the campaign is not active", {"campaign_id": campaign.id, "status": campaign.status})
        sessions: list[RunRecord] = await runtime.store.campaign_sessions(campaign.id)
        working: list[str] = [session.id for session in sessions if session.status in WORKING_STATUSES]
        if working:
            raise RunConflict(
                "a session of the campaign is still working", {"campaign_id": campaign.id, "run_id": working[-1]}
            )
        now: float = time.time()
        number: int = len(sessions) + 1
        brief: str = render_brief(await self._brief(campaign, sessions, number, now))
        # A rhythm plans the session after this one now; otherwise the end of this one does.
        following: float | None = (
            None if campaign.cron is None else next_firing(campaign.cron, now, runtime.schedule_zone)
        )
        await runtime.store.set_next_session(campaign.id, following)
        made_thread_id: str | None = None
        try:
            thread: ThreadRecord = await runtime.store.create_thread(
                f"{campaign.title} · session {number}", SCHEDULE_SOURCE
            )
            made_thread_id = thread.id
            run, _ = await self._launch(
                campaign.goal,
                "task",
                campaign.model,
                thread.id,
                SCHEDULE_SOURCE,
                None,
                campaign.effort,
                campaign.speed,
                campaign.approvals,
                False,
                RunOpening(text=brief, files=(), resumed=None, once=campaign.once, carried=()),
                campaign.id,
                None,
            )
        except Exception:
            # Whatever failed, the session is tried again (its next one was cleared above); what cannot be undone is
            # logged, not raised: the error of the start is the one to raise.
            try:
                await runtime.store.set_next_session(campaign.id, now + runtime.config.agent.campaign_gap_minutes * 60)
                if made_thread_id is not None:
                    await runtime.store.delete_threads([made_thread_id])
            except DotError as exc:
                log.error("session_start_not_undone", campaign_id=campaign.id, error=exc.message, context=exc.context)
            raise
        log.info("campaign_session_started", campaign_id=campaign.id, run_id=run.id, session=number)
        # The owner's Stop (or Mark achieved) while this session was being started found no session to stop: it ends
        # here, as a session that Stop finds working does.
        if (await runtime.store.get_campaign(campaign.id)).status != "active" and run.id in self._handles:
            log.info("campaign_session_stopped_at_its_start", campaign_id=campaign.id, run_id=run.id)
            self.cancel(run.id)
        # So does the owner's emergency stop, under which no session starts: the session ends as one, and its campaign
        # stops with it.
        elif await asyncio.to_thread(failsafe_path().exists):
            log.info(
                "campaign_session_stopped_by_an_emergency_stop_at_its_start", campaign_id=campaign.id, run_id=run.id
            )
            self.end_by_emergency_stop(run.id)
        return run

    async def resume_campaigns(self) -> int:
        """Plan a session for every active campaign with no session planned and none working: one whose session the
        daemon stopped, since a session plans the next one only when it ends. For the daemon, when it starts, after
        :meth:`recover`. The session starts now, or ``[agent] campaign_gap_minutes`` later when the daemon died while the
        last session worked: a session that brings the daemon down does not start again and again. Returns how many
        campaigns got a session."""
        store: Store = self._runtime.store
        now: float = time.time()
        gap_s: float = self._runtime.config.agent.campaign_gap_minutes * 60
        planned: int = 0
        for campaign in await store.list_campaigns():
            if campaign.status != "active" or campaign.next_session_at is not None:
                continue
            sessions: list[RunRecord] = await store.campaign_sessions(campaign.id)
            if any(session.status in WORKING_STATUSES for session in sessions):
                continue
            died: bool = bool(sessions) and cut_off_by(sessions[-1]) == "daemon_restarted"
            await store.set_next_session(campaign.id, now + gap_s if died else now)
            planned += 1
        return planned

    async def delegate(
        self, parent_run_id: str, goal: str, mode: RunMode, model: str | None, agent_id: str | None
    ) -> DelegateOutcome:
        """Run ``goal`` as a run of its own on ``model``, or on the parent's model as it is stored now when that is
        ``None`` (after a switch to a standby model, the standby), and wait until it ends.

        The delegated run starts as untrusted as its parent is, can do no more than its parent (a research run only
        delegates research) and cannot delegate again. It answers the policy's questions the way its parent does and
        takes the parent's instructions as its own; the parent checks its result, so goal mode is not applied to it
        again. It is stopped when the waiting caller is cancelled, and the cancellation goes on once its end is stored,
        so that the end of a run is stored after the ends of the runs it delegated (item 197). With ``agent_id`` (item
        87, a message to an agent) the run is the agent's: its source is ``team``, it carries the agent's id, the
        agent's own model and effort bound it where the agent names them, the parent's where it does not, and it
        answers the policy's questions the stricter way of the agent's and the parent's (item 135). A delegated run
        whose end the store could not record is a ``StoreError``, not an outcome: what the store holds of it is how it
        was while it worked.
        """
        parent: RunRecord = await self._runtime.store.get_run(parent_run_id)
        agent: AgentRecord | None = None if agent_id is None else await self._runtime.store.get_agent(agent_id)
        if parent.parent_run_id is not None:
            raise RunConflict(
                "a delegated run cannot delegate further", {"run_id": parent.id, "parent_run_id": parent.parent_run_id}
            )
        if parent.mode == "research" and mode != "research":
            raise RunConflict("a research run can only delegate research", {"run_id": parent.id, "mode": mode})
        own_model: str = parent.model if model is None else model
        _, handle = await self._launch(
            goal,
            mode,
            own_model if agent is None or agent.model == "" else agent.model,
            None,
            DELEGATE_SOURCE if agent is None else TEAM_SOURCE,
            parent.id,
            parent.effort if agent is None or agent.effort is None else agent.effort,
            parent.speed,
            parent.approvals
            if agent is None or agent.approvals is None
            else stricter_approvals(parent.approvals, agent.approvals),
            False,
            RunOpening(text=goal, files=(), resumed=None, once=None, carried=()),
            None,
            agent_id,
        )
        try:
            await handle.finished.wait()
        except asyncio.CancelledError:
            if handle.run_id in self._handles:
                self.cancel(handle.run_id)
            # The page of the run that waits reads its events up to its own end: the delegated run's end is
            # stored first.
            await _uncancelled(handle.finished.wait())
            raise
        child: RunRecord = await self._runtime.store.get_run(handle.run_id)
        if child.status in WORKING_STATUSES:
            raise StoreError(
                "the delegated run ended, but the store could not record its end",
                {"run_id": child.id, "stored_status": child.status},
            )
        return DelegateOutcome(run=child, tainted=handle.gate.tainted)

    def steer(self, run_id: str, text: str, files: tuple[StoredAttachment, ...]) -> None:
        """Hand the running agent an extra instruction and its attachments; it reads them before its next step, and a
        run about to end with its answer reads them first, unless it is past its limit and ends with that reply. A run
        whose end is being recorded reads nothing more, so a message to it is refused like one to a run that has
        ended."""
        handle: RunHandle = self._handle(run_id)
        try:
            handle.inbox.put_nowait(UserInput(text=text, files=files, question_id=None))
        except asyncio.QueueShutDown as exc:
            raise RunConflict("the run is not active", {"run_id": run_id}) from exc

    async def queue(
        self,
        thread_id: str,
        text: str,
        files: tuple[StoredAttachment, ...],
        mode: RunMode,
        model: str | None,
        effort: Effort | None,
        speed: Speed,
        once: OneMessage | None,
        approvals: ApprovalMode | None,
        goal_mode: bool | None,
        agent_id: str | None,
        source: str,
    ) -> QueuedMessage:
        """Put a message at the end of a chat's queue (item 148): it starts as the chat's next run, with these choices,
        once the run at work there has ended done (:meth:`_follow_queue`), one message after the other. A chat where no
        run works starts it at once, unless its queue is held. The chat of a campaign takes no queue: the campaign
        plans what follows its sessions; nor does the thread of a delegated run, which is no chat: the run that
        delegated it decides what follows (:meth:`_follow_queue` leaves it alone). Fast mode is refused at once for a
        model without it (item 223), and so is a choice for one message whose level the model does not offer (item
        251)."""
        ref: ModelRef = parse_model_ref(model if model is not None else self._main_model)
        _check_speed(ref, speed)
        await self._check_once(ref, once)
        store: Store = self._runtime.store
        await store.get_thread(thread_id)
        newest: list[RunRecord] = await store.list_runs(1, thread_id)
        if not newest:
            raise RunConflict("a chat takes a queue once a run has started there", {"thread_id": thread_id})
        if newest[0].campaign_id is not None:
            raise RunConflict("the chat of a campaign takes no queue: steer its session", {"thread_id": thread_id})
        if newest[0].parent_run_id is not None:
            raise RunConflict(
                "the thread of a delegated run takes no queue: the run that delegated it decides what follows",
                {"thread_id": thread_id, "parent_run_id": newest[0].parent_run_id},
            )
        message: QueuedMessage = await store.add_queued(
            thread_id,
            text,
            [file.id for file in files],
            mode,
            model,
            effort,
            speed,
            once,
            approvals,
            goal_mode,
            agent_id,
            source,
        )
        await self._queue_changed(thread_id, None, None, None)
        await self._drain(thread_id, newest[0].id)
        return message

    async def edit_queued(self, message_id: str, text: str) -> str:
        """Change the text of a queued message; returns its chat. One starting as the chat's next run is refused."""
        store: Store = self._runtime.store
        await store.edit_queued(message_id, text)
        thread_id: str = (await store.get_queued(message_id)).thread_id
        await self._queue_changed(thread_id, None, None, None)
        return thread_id

    async def remove_queued(self, message_id: str) -> str:
        """Take a message out of its chat's queue; returns its chat. One starting as the chat's next run is refused."""
        store: Store = self._runtime.store
        thread_id: str = (await store.get_queued(message_id)).thread_id
        await store.delete_queued(message_id)
        await self._queue_changed(thread_id, None, None, None)
        return thread_id

    async def order_queue(self, thread_id: str, message_ids: Sequence[str]) -> None:
        await self._runtime.store.order_queue(thread_id, message_ids)
        await self._queue_changed(thread_id, None, None, None)

    async def steer_queued(self, message_id: str) -> str:
        """Send a queued message, with its files, to the run at work on its chat now (:meth:`steer`) and take it out of
        the queue; returns its chat. A chat where no run of this process works has none to steer, and a message starting
        as the chat's next run is refused. The message leaves the queue as it is before it is sent, so that nothing
        starts, changes or removes it meanwhile, and goes back when the run ended in that moment."""
        store: Store = self._runtime.store
        message: QueuedMessage = await store.get_queued(message_id)
        if self._active_on(message.thread_id) is None:
            raise RunConflict("no run works on the chat to steer", {"thread_id": message.thread_id})
        files: tuple[StoredAttachment, ...] = await load_attachments(self._runtime.workspace, message.attachments)
        taken: QueuedMessage = await store.take_queued(message_id)
        handle: RunHandle | None = self._active_on(taken.thread_id)
        try:
            if handle is None:
                raise RunConflict("no run works on the chat to steer", {"thread_id": taken.thread_id})
            self.steer(handle.run_id, taken.text, files)
        except BaseException:
            await _uncancelled(self._put_back(taken))
            raise
        await self._queue_changed(taken.thread_id, None, None, None)
        return taken.thread_id

    async def _put_back(self, message: QueuedMessage) -> None:
        """Put a message taken to steer the run at work back where it was, now that the run ended in that moment, and
        move the queue on as the end of that run does (:meth:`_follow_queue`), since that end may have found the queue
        without it; a run whose end is not stored yet moves the queue on itself, and finds it there."""
        store: Store = self._runtime.store
        await store.put_back_queued(message)
        newest: list[RunRecord] = await store.list_runs(1, message.thread_id)
        if newest and newest[0].status not in WORKING_STATUSES:
            await self._follow_queue(newest[0], newest[0].status)

    async def resume_queue(self, thread_id: str) -> None:
        """Let a held queue go on: its next message starts now when no run works on the chat, else after that run."""
        await self._runtime.store.release_queue(thread_id)
        await self._queue_changed(thread_id, None, None, None)
        newest_run_id: str | None = await self._newest_run_id(thread_id)
        # A chat that a rewind emptied holds no queue: a rewind waits for its queue to be sent or taken out.
        if newest_run_id is not None:
            await self._drain(thread_id, newest_run_id)

    async def decide(self, approval_id: str, approve: bool, source: str, note: str) -> ApprovalRecord:
        """Record the user's decision on an approval or a question (``ApprovalBroker.resolve``); ``source`` is where it
        was made (``ui``, ``cli``).

        The answer to a question a run left carries the work on: it goes to the question's thread as the user's
        message, into the run active there, or else, after the goal of the run that asked, as the goal of a new run on
        the thread, of ``source`` and with the mode, model, effort, approvals and goal mode of the run that asked; so
        that run's goal check, the task its continuity note pins and its title keep the task. A run of another process
        (``dotx run``) working on the thread cannot be steered from here, so the answer is refused until it has ended.
        The answer is delivered before the question is decided, so an answer that cannot be delivered leaves the
        question open; an empty answer is refused. Skipping such a question only denies it.

        A question that a session of a campaign left starts nothing and goes to no other run: the answer reaches the
        session while it runs in this process, and is otherwise kept for the brief of the next session. Once the session
        has ended, the answer, or the skip, brings that next session forward to now.
        """
        async with self._answering:
            record: ApprovalRecord = await self._runtime.store.get_approval(approval_id)
            if record.status != "left":
                return await self._runtime.approvals.resolve(approval_id, approve, source, note)
            if approve and note.strip() == "":
                raise InvalidValueError("the answer to the question is empty", {"approval_id": approval_id})
            asked: RunRecord = await self._runtime.store.get_run(record.run_id)
            if asked.campaign_id is None:
                if approve:
                    await self._carry_on(record, source, note)
                return await self._runtime.approvals.resolve(approval_id, approve, source, note)
            campaign_id: str = asked.campaign_id
            decided, ended = await self._decide_for_session(record, approve, source, note)
        if ended:
            ending: RunHandle | None = self._handles.get(asked.id)
            if ending is not None:
                # It plans the next session before it is finished: that plan is what comes forward.
                await ending.finished.wait()
            await self._bring_session_forward(campaign_id)
        return decided

    async def resumable(self, run: RunRecord) -> bool:
        """Whether ``run`` can be resumed: see :func:`dotagent.agent.resume.can_resume`."""
        return can_resume(run, await self._newest_run_id(run.thread_id))

    async def resume(self, run_id: str) -> RunRecord:
        """Resume by hand a run that was cut off (:func:`dotagent.agent.resume.cut_off_by`): a new run on its thread goes
        on from where it stopped. Refused unless :meth:`resumable` allows it; any source may be resumed this way."""
        old: RunRecord = await self._runtime.store.get_run(run_id)
        newest_run_id: str | None = await self._newest_run_id(old.thread_id)
        if not can_resume(old, newest_run_id):
            raise RunConflict(
                "only the newest run of a thread that was cut off, by the daemon or by the end of its dotx command, "
                "and that no other run started, can be resumed",
                {
                    "run_id": old.id,
                    "status": old.status,
                    "result": truncate_tail(old.result, _REFUSED_RESULT_CHARS),
                    "parent_run_id": old.parent_run_id,
                    "newest_run_id": newest_run_id,
                },
            )
        return await self._resume(old, await interrupted_calls(self._runtime, old), False)

    async def resume_safe(self) -> tuple[RunRecord, ...]:
        """Resume the runs that :func:`dotagent.agent.resume.resumes_on_start` allows among those that ended at most
        ``[agent] resume_within_s`` seconds ago; for the daemon, when it starts. A run that cannot be resumed is logged
        and left for the owner, so it never keeps the daemon from starting."""
        since: float = time.time() - self._runtime.config.agent.resume_within_s
        resumed: list[RunRecord] = []
        for old in await self._runtime.store.runs_finished_since(since):
            if cut_off_by(old) is None:
                continue
            try:
                calls: tuple[InterruptedCall, ...] = await interrupted_calls(self._runtime, old)
                if resumes_on_start(old, await self._newest_run_id(old.thread_id), calls):
                    resumed.append(await self._resume(old, calls, True))
            except DotError as exc:
                log.warning("resume_on_start_failed", run_id=old.id, error=exc.message, context=exc.context)
        return tuple(resumed)

    def cancel(self, run_id: str) -> None:
        handle: RunHandle = self._handle(run_id)
        handle.cancel.set()
        self._runtime.approvals.release_run(run_id)
        handle.task.cancel()

    async def stop(self, run_id: str) -> None:
        """Stop the run ``run_id``, as the Stop of the pages asks: a run of this process is cancelled (:meth:`cancel`),
        and the process that lives and drives one of the others (a ``dotx`` command, :meth:`elsewhere_run_ids`) is
        asked to, which it does within ``ASK_CHECK_S`` (:meth:`_answer_asks`). Raises ``RunConflict`` for a run that
        is not at work."""
        if run_id in self._elsewhere and run_id not in self._handles:
            await self._runtime.store.ask_stop(run_id)
            log.info("run_stop_asked", run_id=run_id)
            return
        self.cancel(run_id)

    def _watch_asks(self) -> None:
        """Read the asks of other processes (:meth:`_answer_asks`) from this process's first run at work on."""
        if self._asks is None:
            self._asks = asyncio.create_task(self._answer_asks(), name="asks")

    async def _answer_asks(self) -> None:
        """While this process drives runs, every ``ASK_CHECK_S``: stop those another process asked to stop
        (:meth:`stop`), and wake the questions of theirs that a person answered there
        (``ApprovalBroker.take_stored_decisions``). A read the store refuses is logged, and the next goes on; once
        this process has no run, or the loop ended otherwise, the next run starts it again."""
        store: Store = self._runtime.store
        try:
            while self._handles:
                await asyncio.sleep(ASK_CHECK_S)
                try:
                    asked: list[str] = await store.stops_asked(self.active_run_ids())
                    for run_id in asked:
                        if run_id in self._handles:
                            log.info("run_stopped_as_asked", run_id=run_id)
                            self.cancel(run_id)
                    if asked:
                        await store.forget_stops(asked)
                    await self._runtime.approvals.take_stored_decisions()
                except DotError as exc:
                    log.warning("asks_not_read", error=exc.message, context=exc.context)
                except sqlite3.Error as exc:
                    log.warning("asks_not_read", error=str(exc), error_type=type(exc).__name__)
        finally:
            self._asks = None

    def emergency_stop(self) -> list[str]:
        """Stop every active run as the owner's emergency stop: each ends with :data:`RESULT_EMERGENCY_STOPPED`, so
        the campaign of a session stops instead of planning the next one; a run being launched meanwhile ends so once it
        is registered (:meth:`_launch`). Returns the ids of the runs stopped."""
        self._stops += 1
        active: list[str] = self.active_run_ids()
        for run_id in active:
            self.end_by_emergency_stop(run_id)
        return active

    def end_by_emergency_stop(self, run_id: str) -> None:
        """End the run ``run_id`` as the owner's emergency stop, when it is still active."""
        if run_id in self._handles:
            self._emergency.add(run_id)
            self.cancel(run_id)

    def cut_off(self, run_id: str) -> None:
        """End the run ``run_id``, when it is still active, as cut off by the end of the ``dotx`` command that drives it
        (its terminal was closed, or it got SIGTERM): unlike the owner's stop, it can be resumed (:meth:`resume`)."""
        if run_id in self._handles:
            self._cutting.add(run_id)
            self.cancel(run_id)

    def held(self, run_id: str) -> bool:
        """Whether the user holds the run (took its browser over); false for a run that is not active."""
        handle: RunHandle | None = self._handles.get(run_id)
        return handle is not None and handle.gate.held

    async def take_over(self, run_id: str, source: str) -> str | None:
        """Hold the run at its next tool call and bring its tab to the front, in the browser the run works in
        (:func:`browser_of`), for the user to use the browser themselves; returns the tab's address, ``None`` when the
        run has no tab. Refused when the browser has no window (``[browser] headless``), when the run is not active or
        when it is held already."""
        if self._runtime.config.browser.headless:
            raise RunConflict(
                "the browser is headless, so there is no window to take over; set [browser] headless = false",
                {"run_id": run_id},
            )
        handle: RunHandle = self._handle(run_id)
        browser: BrowserSession = await browser_of(self._runtime, await self._runtime.store.get_run(run_id))
        handle.gate.hold()
        try:
            url: str | None = await browser.focus(handle.thread_id)
        except BaseException:
            # No tab came to the front for the user: the run goes on.
            handle.gate.release()
            raise
        await self._runtime.bus.emit(run_id, TAKEOVER_EVENT, {"source": source, "url": url}, True)
        return url

    async def hand_back(self, run_id: str, source: str) -> float:
        """Let the run go on after a takeover; returns how long it was held. Refused when it is not held."""
        handle: RunHandle = self._handle(run_id)
        held_s: float = handle.gate.release()
        await self._runtime.bus.emit(run_id, HANDBACK_EVENT, {"source": source, "held_s": round(held_s, 1)}, True)
        return held_s

    async def wait(self, run_id: str) -> RunRecord:
        """Block until the run is over (returns immediately for runs that already ended)."""
        handle: RunHandle | None = self._handles.get(run_id)
        if handle is not None:
            await handle.finished.wait()
        return await self._runtime.store.get_run(run_id)

    def learning(self, run_id: str) -> bool:
        """Whether the lessons of a run that ended are being drawn."""
        return run_id in self._learning

    async def reflect(self, now: float) -> Reflection:
        """One reflection at ``now`` (:func:`dotagent.agent.reflection.reflect`), under the lock of the owner's
        decisions on lessons, so a lesson it saves with ``auto`` is decided once like any other."""
        return await reflect(self._runtime, self._deciding, now)

    async def learned(self, run_id: str) -> None:
        """Block until the lessons of a run that ended are drawn (returns immediately when none are being drawn).
        Cancelling the caller cancels the drawing."""
        task: asyncio.Task[None] | None = self._learning.get(run_id)
        if task is not None:
            await task

    async def save_lesson(self, run_id: str, index: int) -> SavedMemory:
        """Save a lesson proposed for a run that ended (see :func:`dotagent.agent.lessons.save_lesson`)."""
        return await save_lesson(self._runtime, self._deciding, run_id, index)

    async def dismiss_lesson(self, run_id: str, index: int) -> None:
        """Dismiss a lesson proposed for a run that ended (see :func:`dotagent.agent.lessons.dismiss_lesson`)."""
        await dismiss_lesson(self._runtime, self._deciding, run_id, index)

    async def undo_lesson(self, run_id: str, index: int) -> bool:
        """Take back the save of a lesson of a run (see :func:`dotagent.agent.lessons.undo_lesson`)."""
        return await undo_lesson(self._runtime, self._deciding, run_id, index)

    async def shutdown(self) -> None:
        """Stop every live run. They end as cancelled with a result that says the daemon shut down, so the owner can tell
        them from runs stopped by hand; their thread keeps everything up to the step they were in. Lessons still being
        drawn from runs that ended are dropped. A command in the background that ends from now on is told to no
        chat: the daemon's stop ends it too."""
        self._stopping = True
        self._runtime.terminals.unwatch(self)
        handles: list[RunHandle] = list(self._handles.values())
        for handle in handles:
            self.cancel(handle.run_id)
        await asyncio.gather(*(handle.finished.wait() for handle in handles))
        if self._asks is not None:
            self._asks.cancel()
        learning: list[asyncio.Task[None]] = [*self._learning.values(), *self._distilling.values()]
        for task in learning:
            task.cancel()
        if learning:
            await asyncio.wait(learning)
        if self._owner is not None:
            # Its runs have ended: this process lets go of them.
            await asyncio.to_thread(self._owner.release)
            self._owner = None

    def _handle(self, run_id: str) -> RunHandle:
        handle: RunHandle | None = self._handles.get(run_id)
        if handle is None and run_id in self._left_ends:
            raise RunConflict(
                "the run has ended; its end is stored as soon as the database takes it", {"run_id": run_id}
            )
        if handle is None:
            raise RunConflict("the run is not active", {"run_id": run_id})
        return handle

    async def _newest_run_id(self, thread_id: str) -> str | None:
        """The newest run still in the chat, ``None`` once a rewind took them all out (item 282)."""
        newest: list[RunRecord] = await self._runtime.store.list_runs(1, thread_id)
        return newest[0].id if newest else None

    async def _require_newest(self, thread_id: str, run_id: str) -> None:
        newest_run_id: str | None = await self._newest_run_id(thread_id)
        if newest_run_id != run_id:
            raise RunConflict(
                "the thread went on after the run to resume", {"run_id": run_id, "newest_run_id": newest_run_id}
            )

    async def _carry_on(self, question: ApprovalRecord, source: str, answer: str) -> None:
        """Deliver the answer to a question a run left (see :meth:`decide`). A run whose end is being recorded reads
        nothing more: the answer waits until it has ended and then goes on as one that came after."""
        asked: RunRecord = await self._runtime.store.get_run(question.run_id)
        text: str = _answer_text(question, answer)
        active: RunHandle | None = self._active_on(asked.thread_id)
        if active is not None:
            try:
                active.inbox.put_nowait(UserInput(text=text, files=(), question_id=question.id))
            except asyncio.QueueShutDown:
                await active.finished.wait()
                await self._carry_on(question, source, answer)
                return
            log.info("question_answered", approval_id=question.id, run_id=active.run_id, delivered="steer")
            return
        newest: RunRecord = (await self._runtime.store.list_runs(1, asked.thread_id))[0]
        if newest.status in WORKING_STATUSES:
            # A run of another process (``dotx run``) works on the thread, and only that process can steer it.
            raise RunConflict(
                "a run of another process is working on this thread; answer the question once it has ended",
                {"approval_id": question.id, "thread_id": asked.thread_id, "run_id": newest.id},
            )
        goal: str = f"{asked.goal}\n\n{text}"
        run, _ = await self._launch(
            goal,
            asked.mode,
            asked.model,
            asked.thread_id,
            source,
            None,
            asked.effort,
            asked.speed,
            asked.approvals,
            asked.goal_mode,
            RunOpening(text=goal, files=(), resumed=None, once=asked.once, carried=()),
            None,
            None,
        )
        log.info("question_answered", approval_id=question.id, run_id=run.id, delivered="run")

    async def _decide_for_session(
        self, question: ApprovalRecord, approve: bool, source: str, note: str
    ) -> tuple[ApprovalRecord, bool]:
        """Decide a question that a session of a campaign left (see :meth:`decide`); also whether the session has ended,
        which brings the next one forward."""
        session: RunHandle | None = self._handles.get(question.run_id)
        if approve and session is not None:
            try:
                session.inbox.put_nowait(
                    UserInput(text=_answer_text(question, note), files=(), question_id=question.id)
                )
            except asyncio.QueueShutDown:
                # It is recording its end and reads nothing more.
                return await self._runtime.approvals.resolve(question.id, approve, source, note), True
            log.info("question_answered", approval_id=question.id, run_id=session.run_id, delivered="steer")
            return await self._runtime.approvals.resolve(question.id, approve, source, note), False
        decided: ApprovalRecord = await self._runtime.approvals.resolve(question.id, approve, source, note)
        # Read once the decision is stored: a session that has not recorded its end yet reads the decision when it plans
        # the next session.
        status: RunStatus = (await self._runtime.store.get_run(question.run_id)).status
        return decided, status not in WORKING_STATUSES

    def _active_on(self, thread_id: str) -> RunHandle | None:
        """The run of this process that is active on ``thread_id``, if any."""
        return next((handle for handle in self._handles.values() if handle.thread_id == thread_id), None)

    async def _bring_session_forward(self, campaign_id: str) -> None:
        """Plan the next session of an active campaign for now, unless one is planned earlier."""
        campaign: CampaignRecord = await self._runtime.store.get_campaign(campaign_id)
        now: float = time.time()
        if campaign.status == "active" and (campaign.next_session_at is None or campaign.next_session_at > now):
            await self._runtime.store.set_next_session(campaign.id, now)
            log.info("campaign_session_brought_forward", campaign_id=campaign.id)

    async def _brief(self, campaign: CampaignRecord, sessions: list[RunRecord], number: int, now: float) -> Brief:
        """What the brief of session ``number`` says; ``sessions`` are the campaign's sessions so far, oldest first."""
        store: Store = self._runtime.store
        progress: CampaignEntry | None = await store.latest_campaign_entry(campaign.id, "progress")
        questions: list[ApprovalRecord] = await store.campaign_questions(campaign.id, ASK_USER_TOOL, BRIEF_QUESTIONS)
        conversation: list[RunRecord] = await store.list_runs(_THREAD_INSTRUCTION_RUNS, campaign.thread_id)
        last: RunRecord | None = sessions[-1] if sessions else None
        return Brief(
            goal=campaign.goal,
            browser=campaign_browser(self._runtime.browser.config.mode, campaign),
            targets=campaign.targets,
            verification=campaign.verification,
            files=campaign.files,
            session=number,
            now=now,
            # The owner's messages in the campaign's conversation: the goals of the runs they started there since (a
            # resumed run repeats the goal of the run it resumes).
            instructions=tuple(
                record.goal
                for record in reversed(conversation)
                if record.parent_run_id is None
                and record.campaign_id is None
                and record.source != RESUME_SOURCE
                and record.created_at > campaign.created_at
            ),
            strategies=tuple(await store.campaign_entries_of_kind(campaign.id, "strategy")),
            progress=progress,
            entries=tuple(await store.campaign_entries(campaign.id, BRIEF_ENTRIES)),
            questions=tuple(
                BriefQuestion(summary=asked.summary, asked_at=asked.created_at, status=asked.status, answer=asked.note)
                for asked in questions
            ),
            last=None
            if last is None
            else LastSession(
                number=len(sessions),
                started_at=last.created_at,
                finished_at=last.finished_at,
                status=last.status,
                result=last.result,
                cut_off=cut_off_by(last) is not None,
            ),
            without_progress=sessions_without_progress(
                [session.created_at for session in sessions], None if progress is None else progress.ts
            ),
            patience=self._runtime.config.agent.campaign_patience_sessions,
        )

    async def _resume(self, old: RunRecord, calls: tuple[InterruptedCall, ...], automatic: bool) -> RunRecord:
        """Launch the run that resumes ``old`` on its thread, with its settings and its newest plan: its first message
        is the note of :func:`dotagent.agent.resume.resume_note`. ``automatic`` is recorded in the ``resumed`` event."""
        cut_off: CutOff | None = cut_off_by(old)
        if cut_off is None:
            raise RunConflict("this run was not cut off", {"run_id": old.id, "status": old.status})
        plan: EventRecord | None = await self._runtime.store.last_event(old.id, PLAN_EVENT)
        resumption: Resumption = Resumption(
            from_run_id=old.id,
            cut_off=cut_off,
            automatic=automatic,
            calls=calls,
            plan=EMPTY_PLAN if plan is None else plan_from_event(plan),
        )
        run, _ = await self._launch(
            old.goal,
            old.mode,
            old.model,
            old.thread_id,
            RESUME_SOURCE,
            None,
            old.effort,
            old.speed,
            old.approvals,
            old.goal_mode,
            RunOpening(text=resume_note(old.goal, resumption), files=(), resumed=resumption, once=old.once, carried=()),
            None,
            None,
        )
        log.info("run_resumed", run_id=run.id, from_run_id=old.id, cut_off=cut_off, automatic=automatic)
        return run

    async def _launch(
        self,
        goal: str,
        mode: RunMode,
        model: str | None,
        thread_id: str | None,
        source: str,
        parent_run_id: str | None,
        effort: Effort | None,
        speed: Speed,
        approvals: ApprovalMode | None,
        goal_mode: bool | None,
        opening: RunOpening,
        campaign_id: str | None,
        agent_id: str | None,
    ) -> tuple[RunRecord, RunHandle]:
        """Create the run, register it and start its task (:meth:`_launch_run`). An emergency stop that comes while it
        is being launched found it not registered yet: the run ends as one once it is."""
        stops: int = self._stops
        run, handle = await self._launch_run(
            goal,
            mode,
            model,
            thread_id,
            source,
            parent_run_id,
            effort,
            speed,
            approvals,
            goal_mode,
            opening,
            campaign_id,
            agent_id,
        )
        if self._stops != stops:
            log.info("run_stopped_by_an_emergency_stop_at_its_start", run_id=run.id)
            self.end_by_emergency_stop(run.id)
        return run, handle

    async def _launch_run(
        self,
        goal: str,
        mode: RunMode,
        model: str | None,
        thread_id: str | None,
        source: str,
        parent_run_id: str | None,
        effort: Effort | None,
        speed: Speed,
        approvals: ApprovalMode | None,
        goal_mode: bool | None,
        opening: RunOpening,
        campaign_id: str | None,
        agent_id: str | None,
    ) -> tuple[RunRecord, RunHandle]:
        """Create the run, register it and start its task. A run launched by an active, untrusted parent is untrusted;
        ``campaign_id`` makes it a session of that campaign."""
        runtime: Runtime = self._runtime
        model_ref: str = model if model is not None else self._main_model
        parse_model_ref(model_ref)
        level: Effort = runtime.config.models.effort if effort is None else effort
        approval_mode: ApprovalMode = runtime.config.policy.approvals if approvals is None else approvals
        if goal_mode is True and mode == "chat":
            raise ConfigError("goal mode is for task and research runs, not for a chat", {"mode": mode})
        with_goal_mode: bool = mode != "chat" and (runtime.config.agent.goal_mode if goal_mode is None else goal_mode)
        if thread_id is None:
            thread: ThreadRecord = await runtime.store.create_thread(thread_title(goal), source)
            return await self._register_run(
                goal,
                mode,
                model_ref,
                thread.id,
                source,
                parent_run_id,
                level,
                speed,
                approval_mode,
                with_goal_mode,
                opening,
                campaign_id,
                agent_id,
            )
        self._claim_thread(thread_id)
        try:
            await runtime.store.get_thread(thread_id)
            if opening.resumed is not None:
                # Checked again under the claim: a run that started on the thread since, such as another resume of the
                # same run, went on from it already.
                await self._require_newest(thread_id, opening.resumed.from_run_id)
            return await self._register_run(
                goal,
                mode,
                model_ref,
                thread_id,
                source,
                parent_run_id,
                level,
                speed,
                approval_mode,
                with_goal_mode,
                opening,
                campaign_id,
                agent_id,
            )
        finally:
            self._claimed.discard(thread_id)

    def _owner_id(self) -> str:
        """The owner of this process's runs, taken with the first of them; nothing awaits between the check and the
        take, so two runs that start at once share it."""
        if self._owner is None:
            self._owner = RunOwner(run_owners_dir())
        return self._owner.id

    def _claim_thread(self, thread_id: str) -> None:
        """Reserve a thread for a run that is about to be registered. The check and the claim have no await between
        them, so two sends to one thread cannot both pass: the second meets the claim of the first, or its handle."""
        if thread_id in self._claimed or any(handle.thread_id == thread_id for handle in self._handles.values()):
            raise RunConflict("a run is already active on this thread", {"thread_id": thread_id})
        self._claimed.add(thread_id)

    async def _starts_tainted(self, parent: RunHandle | None, thread_id: str, source: str, opening: RunOpening) -> bool:
        """Whether a run starts tainted, as the run it goes on from is (item 243): a delegated run as its parent is now,
        a resumed run as the run it resumes was when it was cut off, and the run that tells its chat of a command in the
        background as the chat's last run ended, since that message is Dot's and only a message of the owner restores
        trust; a schedule's firing as any earlier run whose words it reads ended (``opening.carried``, item 257), for the
        same reason. Any other run starts untainted. Asked before the run's record is made: the chat's last run is the
        one before it."""
        if parent is not None:
            return parent.gate.tainted
        if opening.resumed is not None:
            return await self._ended_tainted(opening.resumed.from_run_id)
        if source == BACKGROUND_SOURCE:
            # A chat that a rewind emptied cannot say how its last run ended: its notice starts tainted.
            newest_run_id: str | None = await self._newest_run_id(thread_id)
            return newest_run_id is None or await self._ended_tainted(newest_run_id)
        for run_id in opening.carried:
            if await self._ended_tainted(run_id):
                return True
        return False

    async def _ended_tainted(self, run_id: str) -> bool:
        """Whether the run ``run_id`` was tainted when it ended, from its events (:func:`tainted_after`)."""
        store: Store = self._runtime.store
        found: list[EventRecord] = [
            event for kind in TAINT_EVENTS if (event := await store.last_event(run_id, kind)) is not None
        ]
        return tainted_after(found)

    async def _register_run(
        self,
        goal: str,
        mode: RunMode,
        model_ref: str,
        thread_id: str,
        source: str,
        parent_run_id: str | None,
        level: Effort,
        speed: Speed,
        approval_mode: ApprovalMode,
        with_goal_mode: bool,
        opening: RunOpening,
        campaign_id: str | None,
        agent_id: str | None,
    ) -> tuple[RunRecord, RunHandle]:
        """Create the run record and its gate, start its task and register its handle. The run asks for ``level``, or
        for the level of its choice for one message (item 251); its chat keeps ``level``."""
        runtime: Runtime = self._runtime
        asked_level: Effort = level if opening.once is None else ONE_MESSAGE_LEVELS[opening.once]
        parent: RunHandle | None = None if parent_run_id is None else self._handles.get(parent_run_id)
        # The goal of a run that tells its chat of a command in the background is Dot's, not the owner's (item 229).
        own: tuple[str, ...] = () if source == BACKGROUND_SOURCE else (goal,)
        instructions: tuple[str, ...] = (
            await self._instructions_for(own, thread_id, parent_run_id, parent)
            if campaign_id is None
            else await self._session_instructions(goal, campaign_id)
        )
        starts_tainted: bool = await self._starts_tainted(parent, thread_id, source, opening)

        async def stored() -> RunRecord:
            return await runtime.store.create_run(
                thread_id,
                goal,
                mode,
                model_ref,
                parent_run_id,
                source,
                asked_level,
                speed,
                opening.once,
                approval_mode,
                with_goal_mode,
                campaign_id,
                agent_id,
                self._owner_id(),
            )

        run: RunRecord = await self._stored_run(stored)
        # Until the handle is registered no task will end the stored run: one that fails or is cancelled before
        # that ends here, failed, instead of staying queued (:meth:`_end_unstarted`).
        registered: bool = False
        try:
            if parent_run_id is None:
                # What the chat's next follow-up starts with, from its first run on (item 199): a move of the run
                # to a standby model later leaves it as it is.
                await runtime.store.keep_first_chat_choices(
                    thread_id,
                    ChatChoices(
                        mode=mode,
                        model=model_ref,
                        effort=level,
                        speed=speed,
                        approvals=approval_mode,
                        goal_mode=with_goal_mode,
                    ),
                )
            await runtime.bus.emit(
                run.id,
                "run_queued",
                {
                    "goal": goal,
                    "mode": mode,
                    "model": model_ref,
                    "effort": asked_level,
                    "speed": speed,
                    "approvals": approval_mode,
                    "goal_mode": with_goal_mode,
                    "parent_run_id": parent_run_id,
                    "agent_id": agent_id,
                    "tainted": starts_tainted,
                    "models": {
                        "steps": model_ref,
                        "compaction": model_ref,
                        "review": runtime.roles.review(),
                        "vision": runtime.roles.vision(),
                    },
                },
                True,
            )
            gate: RunGate = RunGate(
                runtime.store,
                runtime.bus,
                runtime.approvals,
                run.id,
                run.mode,
                runtime.config.policy.preset,
                runtime.config.policy.pace,
                runtime.config.policy.spend,
                approval_mode,
                reviewer_for(
                    runtime.router,
                    runtime.roles.review(),
                    runtime.config.agent.model_call_timeout_s,
                    f"{thread_id}:review",
                    run.id,
                ),
                instructions,
                runtime.site_locks,
            )
            if starts_tainted:
                gate.taint()
            cancel: asyncio.Event = asyncio.Event()
            inbox: asyncio.Queue[UserInput] = asyncio.Queue()
            finished: asyncio.Event = asyncio.Event()
            # Started eagerly: the body runs up to its first wait before this returns, so a stop that comes before the
            # run's first step lands inside it and ends it as any stop does (a task cancelled before it started never
            # runs its body).
            task: asyncio.Task[None] = asyncio.Task(
                self._drive(run, gate, cancel, inbox, opening, finished),
                loop=asyncio.get_running_loop(),
                name=run.id,
                eager_start=True,
            )
            handle: RunHandle = RunHandle(run.id, thread_id, parent_run_id, task, gate, cancel, inbox, finished)
            self._handles[run.id] = handle
            registered = True
            self._watch_asks()
            return run, handle
        finally:
            if not registered:
                await _uncancelled(self._end_unstarted(run))

    async def _stored_run(self, store_run: Callable[[], Awaitable[RunRecord]]) -> RunRecord:
        """Store a new run with ``store_run`` (:meth:`Store.create_run`). A chat on which the run of a ``dotx`` command
        that died is still at work refuses it until that run is closed: the daemon closes such runs
        (:meth:`close_runs_of_dead_commands`) and stores the run then; a run of a process that lives keeps the refusal.
        A ``dotx`` command leaves that to the daemon, as it leaves what follows such an end (a chat's queue, a
        campaign's next session)."""
        try:
            return await store_run()
        except RunConflict:
            if self._leaves_queues or await self.close_runs_of_dead_commands() == 0:
                raise
            return await store_run()

    async def _end_unstarted(self, run: RunRecord) -> None:
        """End a run whose start failed after it was stored, before its task was registered: failed, stored as every
        end is, with its ``run_finished`` event, and told to the pages that showed it queued. An end the store refuses is
        logged and left to :meth:`store_left_ends`: the error of the start is the one that goes on."""
        data: JsonObject = {"status": "failed", "result": RESULT_NOT_STARTED, "needs": ""}
        try:
            ended: EventRecord = await self._runtime.store.end_run(run.id, "failed", RESULT_NOT_STARTED, data)
        except DotError as exc:
            log.error("run_end_not_recorded", run_id=run.id, status="failed", error=exc.message, context=exc.context)
            self._left_ends[run.id] = _LeftEnd(run=run, status="failed", result=RESULT_NOT_STARTED, needs="", data=data)
            return
        self._runtime.bus.publish(ended)

    async def _instructions_for(
        self, own: tuple[str, ...], thread_id: str, parent_run_id: str | None, parent: RunHandle | None
    ) -> tuple[str, ...]:
        """What the user has told a run that is about to start, which is all a reviewer takes as authority: the goals
        of the thread's earlier runs and the run's ``own`` words (its goal), or, for a delegated run, what its parent
        was told (its own goal was written by the parent and is not an instruction). The goal of a run that told its
        chat of a command in the background is Dot's words, and none (item 229)."""
        if parent_run_id is not None:
            return () if parent is None else parent.gate.instructions
        recent: list[RunRecord] = await self._runtime.store.list_runs(_THREAD_INSTRUCTION_RUNS, thread_id)
        earlier: list[str] = [
            record.goal
            for record in reversed(recent)
            if record.parent_run_id is None and record.source != BACKGROUND_SOURCE
        ]
        return (*earlier, *own)

    async def _session_instructions(self, goal: str, campaign_id: str) -> tuple[str, ...]:
        """What the owner has told a session of a campaign that is about to start: the goals of the runs in the
        campaign's conversation, where they talk to it, and its goal (:meth:`_instructions_for`), and their answers to
        the questions its sessions left them."""
        store: Store = self._runtime.store
        campaign: CampaignRecord = await store.get_campaign(campaign_id)
        asked: list[ApprovalRecord] = await store.campaign_questions(campaign.id, ASK_USER_TOOL, BRIEF_QUESTIONS)
        answers: list[str] = [
            _answer_text(question, question.note) for question in asked if question.status == "approved"
        ]
        return (*await self._instructions_for((goal,), campaign.thread_id, None, None), *answers)

    async def _check_effort(self, ref: ModelRef, effort: Effort) -> None:
        """A level chosen for a run has to be one the model offers (a model with fewer than two has no choice)."""
        info: ModelInfo = await self._runtime.router.model_info(ref)
        if not _offers(info, effort):
            raise ConfigError(
                "the model does not offer that reasoning effort",
                {"model": ref.label, "effort": effort, "offered": list(info.efforts)},
            )

    async def _check_once(self, ref: ModelRef, once: OneMessage | None) -> None:
        """A choice for one message (item 251) is offered by a model that has the level it asks for."""
        if once is not None:
            await self._check_effort(ref, ONE_MESSAGE_LEVELS[once])

    def _place_for(self, run: RunRecord) -> AbstractAsyncContextManager[None]:
        return nullcontext() if run.source in (DELEGATE_SOURCE, TEAM_SOURCE) else self._slots

    async def _drive(
        self,
        run: RunRecord,
        gate: RunGate,
        cancel: asyncio.Event,
        inbox: asyncio.Queue[UserInput],
        opening: RunOpening,
        finished: asyncio.Event,
    ) -> None:
        """Task body and failure boundary of one run.

        Every way a run can end is turned into a stored status plus an event. A run that one of Dot's errors ends keeps
        the error's message as its result, which the pages, the push and ``dotx run`` show, and the error's context (a
        provider's request and answer) with its ``run_finished`` event and in the log. Unexpected exceptions are logged
        with their traceback and recorded as a failed run; they are not hidden, because nothing awaits this task. The
        inbox is shut as the run ends, before anything awaits, so that what is sent from then on is refused, and answers
        the run got too late to read are kept in its thread (:meth:`_keep_answers`). The handle is released and
        ``finished`` is set even when recording the end fails (:meth:`_close`), so a waiter and the thread never hang;
        the lessons and playbooks of a run whose end was not recorded are not drawn. What follows a session of a
        campaign is planned once its end is recorded and before ``finished`` is set, so a waiter finds it planned. The
        lessons of a run that may hold some are drawn after that, in a task of its own, so that they hold up nothing.
        Last, the run's chat moves its queue on (:meth:`_follow_queue`), once the run no longer holds the chat; a stop
        that came while the end was recorded holds the queue, as any stop does.
        """
        runtime: Runtime = self._runtime
        status: RunStatus = "failed"
        result: str = ""
        needs: str = ""
        error: JsonObject | None = None
        lessons: LessonCase | None = None
        try:
            async with self._place_for(run):
                await runtime.store.set_run_status(run.id, "running")
                await runtime.bus.emit(
                    run.id, "run_started", {"model": run.model, "parent_run_id": run.parent_run_id}, True
                )
                agent: AgentRun = AgentRun(runtime, run, gate, cancel, inbox, self, opening)
                # The other runs know it from now on, and what it holds (item 301).
                runtime.peers.begin(run.id, run.thread_id, run.goal)
                # A run on one of Zen's free models goes whole to OpenCode's own agent (item 283).
                in_opencode: bool = parse_model_ref(run.model).provider == OPENCODE_PROVIDER
                ended: RunEnd = await (run_in_opencode(agent) if in_opencode else run_agent(agent))
                status, result, needs, lessons = (
                    ended.outcome.status,
                    ended.outcome.result,
                    ended.outcome.needs,
                    ended.lessons,
                )
        except (RunCancelled, asyncio.CancelledError):
            status, result = "cancelled", self._stop_result(run.id)
        except FailSafeTriggered as exc:
            status, result = "cancelled", f"{PREFIX_EMERGENCY_STOP}{exc.message}"
        except DotError as exc:
            status, result, error = "failed", exc.message, {"message": exc.message, "context": exc.context}
            log.error("run failed", run_id=run.id, error=exc.message, context=exc.context)
        except Exception as exc:
            status, result = "failed", f"{PREFIX_INTERNAL_ERROR}{type(exc).__name__}: {exc}"
            log.exception("run crashed", run_id=run.id)
        finally:
            runtime.peers.end(run.id)
            inbox.shutdown()
            recorded: bool = await self._close(run, status, result, needs, error, inbox, finished)
        # Registered before anything awaits, so that a waiter that ``finished`` wakes finds it.
        if recorded and lessons is not None and not self._stopping:
            self._learning[run.id] = asyncio.create_task(self._learn(lessons))
        if recorded and not self._stopping and playbook_case(runtime.config, run, status) is not None:
            self._distilling[run.id] = asyncio.create_task(self._distil(run, status))
        if recorded and not self._stopping:
            # Nothing awaits a run's task: a queue that cannot be moved on is logged here and stays as it was.
            try:
                await self._follow_queue(run, "cancelled" if cancel.is_set() else status)
            except Exception:
                log.exception("queue_not_followed", run_id=run.id, thread_id=run.thread_id)

    def _stop_result(self, run_id: str) -> str:
        """Why a run that was cancelled ended: the daemon stopped, the owner's emergency stop, the end of the ``dotx``
        command that drove it, or a plain stop."""
        if self._stopping:
            return RESULT_SHUTDOWN
        if run_id in self._emergency:
            return RESULT_EMERGENCY_STOPPED
        if run_id in self._cutting:
            return RESULT_PROCESS_ENDED
        return RESULT_STOPPED

    async def _close(
        self,
        run: RunRecord,
        status: RunStatus,
        result: str,
        needs: str,
        error: JsonObject | None,
        inbox: asyncio.Queue[UserInput],
        finished: asyncio.Event,
    ) -> bool:
        """Record the end of the run (:meth:`_record_end`), then release its handle and set ``finished`` whatever
        happens; returns whether the end was recorded. A stop that comes meanwhile waits for the record and stops
        nothing more, so what follows the end still happens (:func:`_to_its_end`). Nothing awaits a run's task, so a
        failure to record it is logged here with its traceback; an end the store refused is stored later
        (:meth:`store_left_ends`), and the run stays as the store has it until then."""
        try:
            await _to_its_end(self._record_end(run, status, result, needs, error, inbox))
            return True
        except Exception:
            log.exception("run_end_not_recorded", run_id=run.id, status=status)
            return False
        finally:
            self._handles.pop(run.id, None)
            self._emergency.discard(run.id)
            self._cutting.discard(run.id)
            finished.set()

    async def _record_end(
        self,
        run: RunRecord,
        status: RunStatus,
        result: str,
        needs: str,
        error: JsonObject | None,
        inbox: asyncio.Queue[UserInput],
    ) -> None:
        """Store how the run ended and tell about it: its waiters are released, the answers that reached its inbox
        (shut by :meth:`_drive`) too late are kept, the status and result are stored with the scores of the notes it
        used and its ``run_finished`` event in one write (:meth:`Store.end_run`), the event is sent, and what follows a
        campaign's session is planned. The event keeps ``error``, the message and the context of the error that ended
        the run, when one did. An end the store refuses is kept to be stored later (:meth:`store_left_ends`)."""
        runtime: Runtime = self._runtime
        runtime.approvals.release_run(run.id)
        clean: str = truncate_tail(redact_secrets(result), _RESULT_CHARS)
        data: JsonObject = {"status": status, "result": clean, "needs": redact_secrets(needs)}
        if error is not None:
            data["error"] = redact_json(error)
        try:
            await self._keep_answers(run, [inbox.get_nowait() for _ in range(inbox.qsize())])
            ended: EventRecord = await runtime.store.end_run(run.id, status, clean, data)
        except DotError:
            self._left_ends[run.id] = _LeftEnd(run=run, status=status, result=clean, needs=needs, data=data)
            raise
        runtime.bus.publish(ended)
        if run.campaign_id is not None and not self._stopping:
            await self._follow_session(run, run.campaign_id, classify_ending(status, clean), needs)

    async def _follow_queue(self, run: RunRecord, status: RunStatus) -> None:
        """Move the queue of a run's chat on as the run ends: an end done starts its next message (:meth:`_drain`);
        any other end holds it until the owner resumes it, since what failed would likely fail the next message too
        and a stop stops the chat. A delegated run and a campaign's session leave it alone, and so does a process that
        leaves the queues to the daemon (:meth:`leave_queues`)."""
        if run.parent_run_id is not None or run.campaign_id is not None or self._leaves_queues:
            return
        store: Store = self._runtime.store
        if status == "done":
            await self._drain(run.thread_id, run.id)
        elif await store.list_queued(run.thread_id):
            await store.hold_queue(run.thread_id)
            await self._queue_changed(run.thread_id, None, None, None)

    async def _drain(self, thread_id: str, after_run_id: str) -> None:
        """Start the first message of a chat's queue as the chat's next run, unless the queue is held or a run of any
        process works on the chat; ``after_run_id`` is the run it follows, the chat's newest, so that a page showing
        that run follows the chat to the new one. The message is claimed in the store before its run starts
        (:meth:`Store.claim_queued`), so it starts once whichever process moves the queue, and is not edited, taken out
        or steered meanwhile; it leaves the queue once its run has started. A message that cannot start holds the
        queue, and the pages are told why. A cancellation waits for the claim to end (:func:`_uncancelled`): a claim
        left open would keep the chat's queue from moving."""
        async with self._draining:
            if self._stopping or thread_id in self._claimed or self._active_on(thread_id) is not None:
                return
            run: RunRecord | None = await _uncancelled(self._start_next(thread_id))
        if run is not None:
            await self._queue_changed(thread_id, run.id, after_run_id, None)

    async def _start_next(self, thread_id: str) -> RunRecord | None:
        """Claim the first message of the chat's queue (:meth:`Store.claim_queued`), start it as the chat's next run
        and end the claim: the message leaves the queue, or, when it could not start, stays first in a held queue, and
        the pages are told why. A claim of the chat the store refused to end before is ended first. Returns the run
        started, if any."""
        store: Store = self._runtime.store
        left: Callable[[str], Awaitable[None]] | None = self._left_claims.get(thread_id)
        if left is not None:
            await left(thread_id)
            del self._left_claims[thread_id]
        message: QueuedMessage | None = await store.claim_queued(thread_id, self._owner_id())
        if message is None:
            return None
        try:
            run: RunRecord = await self._start_queued(message)
        except DotError as exc:
            log.warning(
                "queued_message_not_started",
                thread_id=thread_id,
                message_id=message.id,
                error=exc.message,
                context=exc.context,
            )
            await self._end_claim(thread_id, store.hold_claimed)
            await self._queue_changed(thread_id, None, None, exc.message)
            return None
        except Exception:
            await self._end_claim(thread_id, store.hold_claimed)
            raise
        await self._end_claim(thread_id, store.drop_claimed)
        return run

    async def _end_claim(self, thread_id: str, end: Callable[[str], Awaitable[None]]) -> None:
        """End the claim on the chat's queued message with ``end``: ``Store.drop_claimed`` when its run started,
        ``Store.hold_claimed`` when it could not. One the store refuses is logged and ended at the chat's next drain
        (:meth:`_start_next`), instead of keeping the queue from moving while this process lives."""
        try:
            await end(thread_id)
        except DotError as exc:
            log.error("queue_claim_not_ended", thread_id=thread_id, error=exc.message, context=exc.context)
            self._left_claims[thread_id] = end

    async def _start_queued(self, message: QueuedMessage) -> RunRecord:
        """Start a queued message as its chat's next run, with the choices and the files it was sent with."""
        files: tuple[StoredAttachment, ...] = await load_attachments(self._runtime.workspace, message.attachments)
        if message.agent_id is not None:
            agent: AgentRecord = await self._runtime.store.get_agent(message.agent_id)
            return await self.start_for_agent(
                agent,
                message.text,
                message.mode,
                message.model,
                message.thread_id,
                message.source,
                message.effort,
                message.speed,
                message.once,
                message.approvals,
                message.goal_mode,
                files,
            )
        return await self.start(
            message.text,
            message.mode,
            message.model,
            message.thread_id,
            message.source,
            None,
            message.effort,
            message.speed,
            message.once,
            message.approvals,
            message.goal_mode,
            files,
        )

    async def _queue_changed(
        self, thread_id: str, started_run_id: str | None, after_run_id: str | None, error: str | None
    ) -> None:
        """Tell the pages that a chat's queue changed, as a live event of its newest run: the run its next message
        started and the run that ended before it, or why that message could not start. A chat that a rewind emptied has
        no page to tell."""
        newest_run_id: str | None = await self._newest_run_id(thread_id)
        if newest_run_id is None:
            return
        await self._runtime.bus.emit(
            newest_run_id,
            QUEUE_CHANGED_EVENT,
            {"thread_id": thread_id, "started_run_id": started_run_id, "after_run_id": after_run_id, "error": error},
            False,
        )

    async def sessions_changed(self, thread_id: str) -> None:
        """Tell the pages that a chat's terminal sessions changed (item 229), as a live event of its newest run; a chat
        that a rewind emptied has no page to tell."""
        newest_run_id: str | None = await self._newest_run_id(thread_id)
        if newest_run_id is not None:
            await self._runtime.bus.emit(newest_run_id, TERMINALS_CHANGED_EVENT, {"thread_id": thread_id}, False)

    async def command_ended(self, thread_id: str, session: TerminalSession) -> None:
        """Tell a chat that its command in the background ended (item 229): a message at the end of its queue, with the
        chat's choices and agent, which starts its next run at once when no run works there and the queue is not held,
        else waits its turn. The message says what Dot saw, and nothing the command printed (:func:`ended_notice`). A
        chat that a rewind emptied (item 282) is not told: the turn that started the command is gone from it."""
        store: Store = self._runtime.store
        runs: list[RunRecord] = await store.list_runs(1, thread_id)
        if not runs:
            log.warning("background_command_ended_in_emptied_chat", thread_id=thread_id, session_id=session.id)
            return
        newest: RunRecord = runs[0]
        kept: ChatChoices | None = await store.chat_choices(thread_id)
        choices: ChatChoices = (
            kept
            if kept is not None
            else ChatChoices(
                mode=newest.mode,
                model=newest.model,
                effort=newest.effort,
                speed=newest.speed,
                approvals=newest.approvals,
                goal_mode=newest.goal_mode,
            )
        )
        if session.exit_status is None:
            raise ValueError(f"the command of session {session.id} has not ended")
        text: str = ended_notice(session.id, session.command, session.exit_status, time.time() - session.started_at)
        # A kept level the model does not offer is not asked for, as the composer does not ask it (item 116).
        info: ModelInfo = await self._runtime.router.model_info(
            parse_model_ref(choices.model if choices.model is not None else self._main_model)
        )
        effort: Effort | None = choices.effort if choices.effort is not None and _offers(info, choices.effort) else None
        await self.queue(
            thread_id,
            text,
            (),
            choices.mode,
            choices.model,
            effort,
            choices.speed,
            None,
            choices.approvals,
            choices.goal_mode,
            newest.agent_id,
            BACKGROUND_SOURCE,
        )

    async def _tell_campaign_stopped(self, run: RunRecord, campaign: CampaignRecord, note: str) -> None:
        ended: CampaignEnded = CampaignEnded(campaign_id=campaign.id, title=campaign.title, status="stopped", note=note)
        await self._runtime.bus.emit(run.id, CAMPAIGN_ENDED_EVENT, ended.model_dump(), False)

    async def _follow_session(self, run: RunRecord, campaign_id: str, ending: Ending, needs: str) -> None:
        """Plan what follows a session of a campaign that ended as ``ending`` (one the daemon stopped is planned by
        :meth:`resume_campaigns` when it starts again). A campaign that is no longer active plans nothing; the owner's
        emergency stop of desktop control stops the campaign too, and so does running the sessions
        ``[agent] campaign_max_sessions`` allows; otherwise the next session starts as
        :func:`dotagent.agent.campaign.next_session_at` says. A campaign that stops is told to the owner
        (``campaign_ended``). A failure is logged: the run's end is recorded already, and the daemon plans the campaign
        again when it starts."""
        runtime: Runtime = self._runtime
        try:
            campaign: CampaignRecord = await runtime.store.get_campaign(campaign_id)
            if campaign.status != "active":
                return
            if ending == "emergency_stop":
                halted: str = "Stopped: the owner's emergency stop ended this session."
                await runtime.store.set_campaign_status(campaign.id, "stopped", None)
                await runtime.store.add_campaign_entry(campaign.id, run.id, "note", halted, {})
                log.info("campaign_stopped_by_an_emergency_stop", campaign_id=campaign.id, run_id=run.id)
                await self._tell_campaign_stopped(run, campaign, halted)
                return
            sessions: int = len(await runtime.store.campaign_sessions(campaign.id))
            limit: int = runtime.config.agent.campaign_max_sessions
            if 0 < limit <= sessions:
                spent: str = f"Stopped after {sessions} sessions: [agent] campaign_max_sessions is {limit}."
                await runtime.store.set_campaign_status(campaign.id, "stopped", None)
                await runtime.store.add_campaign_entry(campaign.id, None, "note", spent, {})
                log.info("campaign_stopped_at_its_limit", campaign_id=campaign.id, sessions=sessions)
                await self._tell_campaign_stopped(run, campaign, spent)
                return
            asked: list[ApprovalRecord] = await runtime.store.run_questions(run.id, ASK_USER_TOOL)
            following: float = next_session_at(
                time.time(),
                campaign.next_session_at,
                needs,
                [question.status for question in asked],
                runtime.config.agent.campaign_gap_minutes,
                runtime.config.agent.campaign_wait_hours,
            )
            await runtime.store.set_next_session(campaign.id, following)
        except DotError as exc:
            log.error(
                "campaign_session_not_followed",
                run_id=run.id,
                campaign_id=campaign_id,
                error=exc.message,
                context=exc.context,
            )

    async def _keep_answers(self, run: RunRecord, unread: Sequence[UserInput]) -> None:
        """Keep in the thread, as the user's messages, the answers to questions that reached a run too late for it to
        read them (it was stopped, failed or was cut off before its next step). Those questions are decided, so the next
        run on the thread finds the answers there. Other messages it did not read are dropped; the run page marks them
        as not read."""
        for sent in unread:
            if sent.question_id is None:
                continue
            await self._runtime.store.append_messages(run.thread_id, [user_message(sent.text, ())])
            await self._runtime.bus.emit(
                run.id, "steer", {"text": sent.text, "files": attachment_summary(sent.files)}, True
            )
            log.warning("answer_kept_in_thread", run_id=run.id, approval_id=sent.question_id)

    async def distilled(self, run_id: str) -> None:
        """Block until the playbook work of a run that ended is done (returns immediately when there is none)."""
        task: asyncio.Task[None] | None = self._distilling.get(run_id)
        if task is not None:
            await task

    async def _record_learning(self, work: LearningWork, outcome: LearningOutcome) -> None:
        """Keep how a piece of learning work ended in the record of how learning fares
        (:func:`dotagent.agent.learning_health.record_learning`), which tells the owner when learning has stopped. A
        store that cannot keep it is logged: the work has ended either way."""
        quiet_s: float | None = quiet_limit(self._runtime.config.agent.reflection_time)
        try:
            await record_learning(self._runtime.store, self._runtime.bus, work, time.time(), outcome, quiet_s)
        except StoreError as error:
            log.error("learning_not_recorded", work=work, state=outcome.state, error=error.message)

    async def _distil(self, run: RunRecord, status: RunStatus) -> None:
        """Task body of the playbook work of a run that ended (:func:`dotagent.agent.playbooks.learn_playbook`), and
        the one place its errors end (review O25): the work says on the run what it expected to fail; anything else is
        logged with its traceback and left on the run as a ``playbook_failed`` event, which its page and ``dotx stats``
        show. Every outcome goes to the record of how learning fares; the run keeps its end either way."""
        try:
            try:
                outcome: LearningOutcome = await learn_playbook(self._runtime, run, status)
            except Exception as exc:
                log.exception("playbook crashed", run_id=run.id)
                outcome = failed_with(f"The skill work failed: {_crash(exc)}")
                await self._runtime.bus.emit(run.id, PLAYBOOK_FAILED, {"host": "", "error": outcome.error}, True)
            await self._record_learning("playbooks", outcome)
        finally:
            self._distilling.pop(run.id, None)

    async def _learn(self, case: LessonCase) -> None:
        """Task body of drawing the lessons of a run that ended (:func:`dotagent.agent.lessons.learn`), and the one
        place its errors end (review O25): the work says on the run what it expected to fail; anything else is logged
        with its traceback and left on the run as a ``lessons_failed`` event, which its page and ``dotx stats`` show.
        Every outcome goes to the record of how learning fares; the run keeps its end either way."""
        try:
            try:
                outcome: LearningOutcome = await learn(self._runtime, self._deciding, case)
            except Exception as exc:
                log.exception("lessons crashed", run_id=case.run_id)
                outcome = failed_with(f"The lessons of this run failed: {_crash(exc)}")
                await self._runtime.bus.emit(case.run_id, LESSONS_FAILED, {"error": outcome.error}, True)
            await self._record_learning("lessons", outcome)
        finally:
            self._learning.pop(case.run_id, None)
