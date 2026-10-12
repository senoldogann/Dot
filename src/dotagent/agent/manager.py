"""Run lifecycle: start, queue, steer, cancel and finish agent runs, each in its own asyncio task."""

import asyncio
import time
from collections.abc import Collection, Coroutine, Iterator, Sequence
from contextlib import AbstractAsyncContextManager, contextmanager, nullcontext
from dataclasses import dataclass

from pydantic import JsonValue

from dotagent.agent.attachments import StoredAttachment, UserInput, attachment_summary
from dotagent.agent.campaign import (
    BRIEF_ENTRIES,
    BRIEF_QUESTIONS,
    Brief,
    BriefQuestion,
    LastSession,
    campaign_browser,
    next_session_at,
    render_brief,
    sessions_without_progress,
)
from dotagent.agent.gate import HANDBACK_EVENT, TAKEOVER_EVENT, RunGate
from dotagent.agent.lessons import LessonCase, dismiss_lesson, learn, save_lesson
from dotagent.agent.loop import AgentRun, RunEnd, RunOpening, run_agent
from dotagent.agent.plan import EMPTY_PLAN, PLAN_EVENT, plan_from_event
from dotagent.agent.playbooks import learn_playbook, playbook_case
from dotagent.agent.reflection import Reflection, reflect
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
from dotagent.agent.review import reviewer_for
from dotagent.browser.attach import read_debug_endpoint
from dotagent.core.errors import (
    ConfigError,
    DotError,
    FailSafeTriggered,
    InvalidValueError,
    RunCancelled,
    RunConflict,
)
from dotagent.core.logs import get_logger
from dotagent.core.paths import chrome_data_dir
from dotagent.core.text import redact_secrets, truncate_tail
from dotagent.llm.router import ModelRef, parse_model_ref
from dotagent.llm.types import Effort, ModelInfo, user_message
from dotagent.memory.service import SavedMemory
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.scheduler.timing import next_firing
from dotagent.store.db import Store
from dotagent.store.endings import (
    PREFIX_EMERGENCY_STOP,
    PREFIX_INTERNAL_ERROR,
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
    EventRecord,
    RunMode,
    RunRecord,
    RunStatus,
    ThreadRecord,
)
from dotagent.tools.context import DelegateOutcome
from dotagent.tools.delegate import DELEGATE_SOURCE
from dotagent.tools.meta import ASK_USER_TOOL
from dotagent.tools.team import TEAM_SOURCE

log = get_logger(__name__)

_TITLE_CHARS: int = 60
_RESULT_CHARS: int = 20_000
# How much of a run's result a refusal to resume it shows.
_REFUSED_RESULT_CHARS: int = 200
# The earlier runs of a thread whose goals count as the user's instructions to a reviewer.
_THREAD_INSTRUCTION_RUNS: int = 30


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


def thread_title(goal: str) -> str:
    lines: list[str] = goal.strip().splitlines()
    first_line: str = lines[0] if lines else "Untitled"
    return first_line if len(first_line) <= _TITLE_CHARS else f"{first_line[: _TITLE_CHARS - 1]}…"


def _answer_text(question: ApprovalRecord, answer: str) -> str:
    """The user's message that carries the answer to a question a run left."""
    return f"Answer to your question “{question.summary}”: {answer}"


async def _uncancelled(work: Coroutine[object, object, None]) -> None:
    """Run ``work`` to its end even when the caller is cancelled meanwhile (a second stop of a run while its end is
    being recorded), then raise the cancellation (item 116)."""
    task: asyncio.Task[None] = asyncio.ensure_future(work)
    cancelled: bool = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    task.result()


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
        # Runs that ended and whose lessons are being drawn (:mod:`dotagent.agent.lessons`), by run id.
        self._learning: dict[str, asyncio.Task[None]] = {}
        # Runs that ended whose playbook is being written or whose use of playbooks is being recorded, by run id.
        self._distilling: dict[str, asyncio.Task[None]] = {}
        # Held while a lesson is saved or dismissed, by the owner or by ``auto``, so that each is decided once.
        self._deciding: asyncio.Lock = asyncio.Lock()
        # Held while an approval or a question is decided, so that the answer to a question a run left goes on once.
        self._answering: asyncio.Lock = asyncio.Lock()

    async def recover(self) -> int:
        """Close out what a previous process left behind. Returns the number of interrupted runs."""
        interrupted: int = await self._runtime.store.mark_interrupted_runs()
        await self._runtime.store.expire_pending_approvals()
        return interrupted

    def active_run_ids(self) -> list[str]:
        return list(self._handles)

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
            raise RunConflict("chats with a run under way cannot be deleted", {"thread_ids": named})
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
        approvals: ApprovalMode | None,
        goal_mode: bool | None,
        files: tuple[StoredAttachment, ...],
    ) -> RunRecord:
        """Queue a run. ``model``, ``effort``, ``approvals`` and ``goal_mode`` fall back to the configured ones when
        ``None``; ``files`` are the attachments that come with the goal.

        An ``effort`` that is given has to be one of the levels the model offers. ``goal_mode`` is for task and research
        runs: asking for it on a chat is refused, and the configured default does not apply to a chat.
        """
        if effort is not None:
            await self._check_effort(
                parse_model_ref(model if model is not None else self._runtime.config.models.main), effort
            )
        run, _ = await self._launch(
            goal,
            mode,
            model,
            thread_id,
            source,
            parent_run_id,
            effort,
            approvals,
            goal_mode,
            RunOpening(text=goal, files=files, resumed=None),
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
        approvals: ApprovalMode | None,
        goal_mode: bool | None,
        files: tuple[StoredAttachment, ...],
    ) -> RunRecord:
        """Queue a run of ``agent`` (item 86): what the request leaves out, the agent's own bounds fill in (its model,
        effort and approvals), and what the agent leaves out, the configured ones; the run carries the agent's id and
        the loop adds its instructions to the prompt and keeps its tools."""
        model_ref: str | None = model if model is not None else (agent.model if agent.model != "" else None)
        level: Effort | None = effort if effort is not None else agent.effort
        approval_mode: ApprovalMode | None = approvals if approvals is not None else agent.approvals
        if level is not None:
            await self._check_effort(
                parse_model_ref(model_ref if model_ref is not None else self._runtime.config.models.main), level
            )
        run, _ = await self._launch(
            goal,
            mode,
            model_ref,
            thread_id,
            source,
            None,
            level,
            approval_mode,
            goal_mode,
            RunOpening(text=goal, files=files, resumed=None),
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
        approvals: ApprovalMode | None,
        goal_mode: bool | None,
        files: tuple[StoredAttachment, ...],
        browser: BrowserMode | None,
    ) -> RunRecord:
        """Start a campaign on ``goal`` ("Keep going until done") with its opening session: a run of ``source`` on
        ``thread_id`` (a new thread when ``None``), which becomes the campaign's conversation. Every session keeps the
        model, reasoning level and approval mode the opening one starts with (``None`` takes the configured one), and
        works in the Chrome ``browser`` names (``None``: ``[agent] campaign_browser``); a campaign that asks for the
        owner's Chrome is refused while that Chrome is closed or does not allow the connection, so that its sessions do
        not fail one after the other.

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
        model_ref: str = model if model is not None else runtime.config.models.main
        if effort is not None:
            await self._check_effort(parse_model_ref(model_ref), effort)
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
            thread_title(goal), goal, thread.id, model_ref, level, approval_mode, [file.path for file in files], chrome
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
                approval_mode,
                False,
                RunOpening(text=goal, files=files, resumed=None),
                campaign.id,
                None,
            )
        except DotError:
            # A campaign whose opening session never started was never under way, nor was a thread made for it.
            await runtime.store.delete_campaign(campaign.id)
            if thread_id is None:
                await runtime.store.delete_thread(thread.id)
            raise
        log.info("campaign_started", campaign_id=campaign.id, run_id=run.id)
        return run

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
        thread: ThreadRecord = await runtime.store.create_thread(
            f"{campaign.title} · session {number}", SCHEDULE_SOURCE
        )
        try:
            run, _ = await self._launch(
                campaign.goal,
                "task",
                campaign.model,
                thread.id,
                SCHEDULE_SOURCE,
                None,
                campaign.effort,
                campaign.approvals,
                False,
                RunOpening(text=brief, files=(), resumed=None),
                campaign.id,
                None,
            )
        except DotError:
            await runtime.store.delete_thread(thread.id)
            await runtime.store.set_next_session(campaign.id, now + runtime.config.agent.campaign_gap_minutes * 60)
            raise
        log.info("campaign_session_started", campaign_id=campaign.id, run_id=run.id, session=number)
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
        again. It is stopped when the waiting caller is cancelled. With ``agent_id`` (item 87, a message to an agent)
        the run is the agent's: its source is ``team``, it carries the agent's id, and the agent's own model, effort
        and approvals bound it where the agent names them, the parent's where it does not.
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
            parent.approvals if agent is None or agent.approvals is None else agent.approvals,
            False,
            RunOpening(text=goal, files=(), resumed=None),
            None,
            agent_id,
        )
        try:
            await handle.finished.wait()
        except asyncio.CancelledError:
            if handle.run_id in self._handles:
                self.cancel(handle.run_id)
            raise
        return DelegateOutcome(run=await self._runtime.store.get_run(handle.run_id), tainted=handle.gate.tainted)

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
        """Resume by hand a run that the daemon cut off: a new run on its thread goes on from where it stopped. Refused
        unless :meth:`resumable` allows it; any source may be resumed this way."""
        old: RunRecord = await self._runtime.store.get_run(run_id)
        newest_run_id: str = await self._newest_run_id(old.thread_id)
        if not can_resume(old, newest_run_id):
            raise RunConflict(
                "only the newest run of a thread that the daemon cut off, and that no other run started, can be "
                "resumed",
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

    def held(self, run_id: str) -> bool:
        """Whether the user holds the run (took its browser over); false for a run that is not active."""
        handle: RunHandle | None = self._handles.get(run_id)
        return handle is not None and handle.gate.held

    async def take_over(self, run_id: str, source: str) -> str | None:
        """Hold the run at its next tool call and bring its tab to the front, for the user to use the browser
        themselves; returns the tab's address, ``None`` when the run has no tab. Refused when the browser has no
        window (``[browser] headless``), when the run is not active or when it is held already."""
        if self._runtime.config.browser.headless:
            raise RunConflict(
                "the browser is headless, so there is no window to take over; set [browser] headless = false",
                {"run_id": run_id},
            )
        handle: RunHandle = self._handle(run_id)
        handle.gate.hold()
        url: str | None = await self._runtime.browser.focus(handle.thread_id)
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

    async def shutdown(self) -> None:
        """Stop every live run. They end as cancelled with a result that says the daemon shut down, so the owner can tell
        them from runs stopped by hand; their thread keeps everything up to the step they were in. Lessons still being
        drawn from runs that ended are dropped."""
        self._stopping = True
        handles: list[RunHandle] = list(self._handles.values())
        for handle in handles:
            self.cancel(handle.run_id)
        await asyncio.gather(*(handle.finished.wait() for handle in handles))
        learning: list[asyncio.Task[None]] = [*self._learning.values(), *self._distilling.values()]
        for task in learning:
            task.cancel()
        if learning:
            await asyncio.wait(learning)

    def _handle(self, run_id: str) -> RunHandle:
        handle: RunHandle | None = self._handles.get(run_id)
        if handle is None:
            raise RunConflict("the run is not active", {"run_id": run_id})
        return handle

    async def _newest_run_id(self, thread_id: str) -> str:
        newest: list[RunRecord] = await self._runtime.store.list_runs(1, thread_id)
        return newest[0].id

    async def _require_newest(self, thread_id: str, run_id: str) -> None:
        newest_run_id: str = await self._newest_run_id(thread_id)
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
            asked.approvals,
            asked.goal_mode,
            RunOpening(text=goal, files=(), resumed=None),
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
            raise RunConflict("the daemon did not cut this run off", {"run_id": old.id, "status": old.status})
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
            old.approvals,
            old.goal_mode,
            RunOpening(text=resume_note(old.goal, resumption), files=(), resumed=resumption),
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
        approvals: ApprovalMode | None,
        goal_mode: bool | None,
        opening: RunOpening,
        campaign_id: str | None,
        agent_id: str | None,
    ) -> tuple[RunRecord, RunHandle]:
        """Create the run, register it and start its task. A run launched by an active, untrusted parent is untrusted;
        ``campaign_id`` makes it a session of that campaign."""
        runtime: Runtime = self._runtime
        model_ref: str = model if model is not None else runtime.config.models.main
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
                approval_mode,
                with_goal_mode,
                opening,
                campaign_id,
                agent_id,
            )
        finally:
            self._claimed.discard(thread_id)

    def _claim_thread(self, thread_id: str) -> None:
        """Reserve a thread for a run that is about to be registered. The check and the claim have no await between
        them, so two sends to one thread cannot both pass: the second meets the claim of the first, or its handle."""
        if thread_id in self._claimed or any(handle.thread_id == thread_id for handle in self._handles.values()):
            raise RunConflict("a run is already active on this thread", {"thread_id": thread_id})
        self._claimed.add(thread_id)

    async def _register_run(
        self,
        goal: str,
        mode: RunMode,
        model_ref: str,
        thread_id: str,
        source: str,
        parent_run_id: str | None,
        level: Effort,
        approval_mode: ApprovalMode,
        with_goal_mode: bool,
        opening: RunOpening,
        campaign_id: str | None,
        agent_id: str | None,
    ) -> tuple[RunRecord, RunHandle]:
        """Create the run record and its gate, start its task and register its handle."""
        runtime: Runtime = self._runtime
        parent: RunHandle | None = None if parent_run_id is None else self._handles.get(parent_run_id)
        instructions: tuple[str, ...] = (
            await self._instructions_for(goal, thread_id, parent_run_id, parent)
            if campaign_id is None
            else await self._session_instructions(goal, campaign_id)
        )
        run: RunRecord = await runtime.store.create_run(
            thread_id,
            goal,
            mode,
            model_ref,
            parent_run_id,
            source,
            level,
            approval_mode,
            with_goal_mode,
            campaign_id,
            agent_id,
        )
        # Until the handle is registered no task will end the stored run: one that fails or is cancelled before
        # that ends here, failed, instead of staying queued.
        registered: bool = False
        try:
            await runtime.bus.emit(
                run.id,
                "run_queued",
                {
                    "goal": goal,
                    "mode": mode,
                    "model": model_ref,
                    "effort": level,
                    "approvals": approval_mode,
                    "goal_mode": with_goal_mode,
                    "parent_run_id": parent_run_id,
                    "agent_id": agent_id,
                    "models": {
                        "steps": model_ref,
                        "compaction": model_ref,
                        "review": runtime.config.models.review,
                        "vision": runtime.config.models.vision,
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
                    runtime.config.models.review,
                    runtime.config.agent.model_call_timeout_s,
                    f"{thread_id}:review",
                    run.id,
                ),
                instructions,
            )
            if parent is not None and parent.gate.tainted:
                gate.taint()
            cancel: asyncio.Event = asyncio.Event()
            inbox: asyncio.Queue[UserInput] = asyncio.Queue()
            finished: asyncio.Event = asyncio.Event()
            task: asyncio.Task[None] = asyncio.create_task(
                self._drive(run, gate, cancel, inbox, opening, finished), name=run.id
            )
            handle: RunHandle = RunHandle(run.id, thread_id, parent_run_id, task, gate, cancel, inbox, finished)
            self._handles[run.id] = handle
            registered = True
            return run, handle
        finally:
            if not registered:
                await _uncancelled(runtime.store.finish_run(run.id, "failed", "the run could not be started"))

    async def _instructions_for(
        self, goal: str, thread_id: str, parent_run_id: str | None, parent: RunHandle | None
    ) -> tuple[str, ...]:
        """What the user has told a run that is about to start, which is all a reviewer takes as authority: the goals
        of the thread's earlier runs and this goal, or, for a delegated run, what its parent was told (its own goal was
        written by the parent and is not an instruction)."""
        if parent_run_id is not None:
            return () if parent is None else parent.gate.instructions
        recent: list[RunRecord] = await self._runtime.store.list_runs(_THREAD_INSTRUCTION_RUNS, thread_id)
        earlier: list[str] = [record.goal for record in reversed(recent) if record.parent_run_id is None]
        return (*earlier, goal)

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
        return (*await self._instructions_for(goal, campaign.thread_id, None, None), *answers)

    async def _check_effort(self, ref: ModelRef, effort: Effort) -> None:
        """A level chosen for a run has to be one the model offers (a model with fewer than two has no choice)."""
        info: ModelInfo = await self._runtime.router.model_info(ref)
        if len(info.efforts) < 2 or effort not in info.efforts:
            raise ConfigError(
                "the model does not offer that reasoning effort",
                {"model": ref.label, "effort": effort, "offered": list(info.efforts)},
            )

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

        Every way a run can end is turned into a stored status plus an event. Unexpected exceptions are logged with
        their traceback and recorded as a failed run; they are not hidden, because nothing awaits this task. The inbox
        is shut before anything awaits, so that what is sent from then on is refused, and answers the run got too late
        to read are kept in its thread (:meth:`_keep_answers`). The handle is released and ``finished`` is set even when
        recording the end fails, so a waiter and the thread never hang. What follows a session of a campaign is planned
        once its end is recorded and before ``finished`` is set, so a waiter finds it planned. The lessons of a run that
        may hold some are drawn after that, in a task of its own, so that they hold up nothing.
        """
        runtime: Runtime = self._runtime
        status: RunStatus = "failed"
        result: str = ""
        needs: str = ""
        lessons: LessonCase | None = None
        try:
            async with self._place_for(run):
                await runtime.store.set_run_status(run.id, "running")
                await runtime.bus.emit(
                    run.id, "run_started", {"model": run.model, "parent_run_id": run.parent_run_id}, True
                )
                ended: RunEnd = await run_agent(AgentRun(runtime, run, gate, cancel, inbox, self, opening))
                status, result, needs, lessons = (
                    ended.outcome.status,
                    ended.outcome.result,
                    ended.outcome.needs,
                    ended.lessons,
                )
        except (RunCancelled, asyncio.CancelledError):
            status, result = "cancelled", RESULT_SHUTDOWN if self._stopping else RESULT_STOPPED
        except FailSafeTriggered as exc:
            status, result = "cancelled", f"{PREFIX_EMERGENCY_STOP}{exc.message}"
        except DotError as exc:
            status, result = "failed", str(exc)
            log.error("run failed", run_id=run.id, error=exc.message, context=exc.context)
        except Exception as exc:
            status, result = "failed", f"{PREFIX_INTERNAL_ERROR}{type(exc).__name__}: {exc}"
            log.exception("run crashed", run_id=run.id)
        finally:
            try:
                await _uncancelled(self._record_end(run, status, result, needs, inbox))
            finally:
                self._handles.pop(run.id, None)
                finished.set()
        # Registered before anything awaits, so that a waiter that ``finished`` wakes finds it.
        if lessons is not None and not self._stopping:
            self._learning[run.id] = asyncio.create_task(self._learn(lessons))
        if not self._stopping and playbook_case(runtime.config, run, status) is not None:
            self._distilling[run.id] = asyncio.create_task(self._distil(run, status))

    async def _record_end(
        self, run: RunRecord, status: RunStatus, result: str, needs: str, inbox: asyncio.Queue[UserInput]
    ) -> None:
        """Store how the run ended and tell about it: its waiters are released and its inbox shut, the answers it got
        too late are kept, the status and result are stored and sent, and what follows a campaign's session is
        planned."""
        runtime: Runtime = self._runtime
        runtime.approvals.release_run(run.id)
        inbox.shutdown()
        await self._keep_answers(run, [inbox.get_nowait() for _ in range(inbox.qsize())])
        clean: str = truncate_tail(redact_secrets(result), _RESULT_CHARS)
        await runtime.store.finish_run(run.id, status, clean)
        await runtime.store.close_memory_uses(run.id, status)
        await runtime.bus.emit(
            run.id, "run_finished", {"status": status, "result": clean, "needs": redact_secrets(needs)}, True
        )
        if run.campaign_id is not None and not self._stopping:
            await self._follow_session(run, run.campaign_id, classify_ending(status, clean), needs)

    async def _follow_session(self, run: RunRecord, campaign_id: str, ending: Ending, needs: str) -> None:
        """Plan what follows a session of a campaign that ended as ``ending`` (one the daemon stopped is planned by
        :meth:`resume_campaigns` when it starts again). A campaign that is no longer active plans nothing; the owner's
        emergency stop of desktop control stops the campaign too, and so does running the sessions
        ``[agent] campaign_max_sessions`` allows; otherwise the next session starts as
        :func:`dotagent.agent.campaign.next_session_at` says. A failure is logged: the run's end is recorded already,
        and the daemon plans the campaign again when it starts."""
        runtime: Runtime = self._runtime
        try:
            campaign: CampaignRecord = await runtime.store.get_campaign(campaign_id)
            if campaign.status != "active":
                return
            if ending == "emergency_stop":
                await runtime.store.set_campaign_status(campaign.id, "stopped", None)
                await runtime.store.add_campaign_entry(
                    campaign.id, run.id, "note", "Stopped: the owner's emergency stop ended this session.", {}
                )
                log.info("campaign_stopped_by_an_emergency_stop", campaign_id=campaign.id, run_id=run.id)
                return
            sessions: int = len(await runtime.store.campaign_sessions(campaign.id))
            limit: int = runtime.config.agent.campaign_max_sessions
            if 0 < limit <= sessions:
                await runtime.store.set_campaign_status(campaign.id, "stopped", None)
                await runtime.store.add_campaign_entry(
                    campaign.id,
                    None,
                    "note",
                    f"Stopped after {sessions} sessions: [agent] campaign_max_sessions is {limit}.",
                    {},
                )
                log.info("campaign_stopped_at_its_limit", campaign_id=campaign.id, sessions=sessions)
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

    async def _distil(self, run: RunRecord, status: RunStatus) -> None:
        """Task body of the playbook work of a run that ended (:func:`dotagent.agent.playbooks.learn_playbook`).
        Unexpected exceptions are logged with their traceback; the run keeps its end either way."""
        try:
            await learn_playbook(self._runtime, run, status)
        except Exception:
            log.exception("playbook crashed", run_id=run.id)
        finally:
            self._distilling.pop(run.id, None)

    async def _learn(self, case: LessonCase) -> None:
        """Task body of drawing the lessons of a run that ended. Unexpected exceptions are logged with their traceback;
        the run keeps its end either way."""
        try:
            await learn(self._runtime, self._deciding, case)
        except Exception:
            log.exception("lessons crashed", run_id=case.run_id)
        finally:
            self._learning.pop(case.run_id, None)
