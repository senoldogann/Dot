"""Run lifecycle: start, queue, steer, cancel and finish agent runs, each in its own asyncio task."""

import asyncio
import time
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass

from dotagent.agent.attachments import StoredAttachment, UserInput
from dotagent.agent.gate import RunGate
from dotagent.agent.lessons import LessonCase, dismiss_lesson, learn, save_lesson
from dotagent.agent.loop import AgentRun, RunEnd, RunOpening, run_agent
from dotagent.agent.plan import PLAN_EVENT, plan_from_event
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
from dotagent.core.errors import ConfigError, DotError, FailSafeTriggered, RunCancelled, RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.text import redact_secrets, truncate_tail
from dotagent.llm.router import ModelRef, parse_model_ref
from dotagent.llm.types import Effort, ModelInfo
from dotagent.memory.service import SavedMemory
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime
from dotagent.store.endings import (
    PREFIX_EMERGENCY_STOP,
    PREFIX_INTERNAL_ERROR,
    RESULT_SHUTDOWN,
    RESULT_STOPPED,
)
from dotagent.store.models import ApprovalRecord, EventRecord, RunMode, RunRecord, RunStatus, ThreadRecord
from dotagent.tools.context import DelegateOutcome
from dotagent.tools.delegate import DELEGATE_SOURCE

log = get_logger(__name__)

_TITLE_CHARS: int = 60
_RESULT_CHARS: int = 20_000
# How much of a run's result a refusal to resume it shows.
_REFUSED_RESULT_CHARS: int = 200
# The earlier runs of a thread whose goals count as the user's instructions to a reviewer.
_THREAD_INSTRUCTION_RUNS: int = 30
# The statuses of a run that has not ended.
_WORKING: frozenset[RunStatus] = frozenset({"queued", "running", "blocked"})


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


class RunManager:
    """Owns the live runs of one process. At most ``max_concurrent_runs`` execute at the same time.

    Runs that other runs delegate work to do not count against that limit: their parent holds a place while it waits
    for them, so a full set of waiting parents would otherwise keep every delegated run from ever starting.
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
        )
        return run

    async def delegate(self, parent_run_id: str, goal: str, mode: RunMode) -> DelegateOutcome:
        """Run ``goal`` as a run of its own on the parent's model and wait until it ends.

        The delegated run starts as untrusted as its parent is, can do no more than its parent (a research run only
        delegates research) and cannot delegate again. It answers the policy's questions the way its parent does and
        takes the parent's instructions as its own; the parent checks its result, so goal mode is not applied to it
        again. It is stopped when the waiting caller is cancelled.
        """
        parent: RunRecord = await self._runtime.store.get_run(parent_run_id)
        if parent.parent_run_id is not None:
            raise RunConflict(
                "a delegated run cannot delegate further", {"run_id": parent.id, "parent_run_id": parent.parent_run_id}
            )
        if parent.mode == "research" and mode != "research":
            raise RunConflict("a research run can only delegate research", {"run_id": parent.id, "mode": mode})
        _, handle = await self._launch(
            goal,
            mode,
            parent.model,
            None,
            DELEGATE_SOURCE,
            parent.id,
            parent.effort,
            parent.approvals,
            False,
            RunOpening(text=goal, files=(), resumed=None),
        )
        try:
            await handle.finished.wait()
        except asyncio.CancelledError:
            if handle.run_id in self._handles:
                self.cancel(handle.run_id)
            raise
        return DelegateOutcome(run=await self._runtime.store.get_run(handle.run_id), tainted=handle.gate.tainted)

    def steer(self, run_id: str, text: str, files: tuple[StoredAttachment, ...]) -> None:
        """Hand the running agent an extra instruction and its attachments; it reads them before its next step."""
        self._handle(run_id).inbox.put_nowait(UserInput(text=text, files=files))

    async def decide(self, approval_id: str, approve: bool, source: str, note: str) -> ApprovalRecord:
        """Record the user's decision on an approval or a question (``ApprovalBroker.resolve``); ``source`` is where it
        was made (``ui``, ``cli``).

        The answer to a question a run left carries the work on: it goes to the question's thread as the user's
        message, into the run active there, or else as the goal of a new run on the thread, of ``source`` and with the
        mode, model, effort, approvals and goal mode of the run that asked. A run of another process (``dotx run``)
        working on the thread cannot be steered from here, so the answer is refused until it has ended. The answer is
        delivered before the question is decided, so an answer that cannot be delivered leaves the question open.
        Skipping such a question only denies it.
        """
        async with self._answering:
            record: ApprovalRecord = await self._runtime.store.get_approval(approval_id)
            if record.status == "left" and approve:
                await self._carry_on(record, source, note)
            return await self._runtime.approvals.resolve(approval_id, approve, source, note)

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

    async def wait(self, run_id: str) -> RunRecord:
        """Block until the run is over (returns immediately for runs that already ended)."""
        handle: RunHandle | None = self._handles.get(run_id)
        if handle is not None:
            await handle.finished.wait()
        return await self._runtime.store.get_run(run_id)

    def learning(self, run_id: str) -> bool:
        """Whether the lessons of a run that ended are being drawn."""
        return run_id in self._learning

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
        learning: list[asyncio.Task[None]] = list(self._learning.values())
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
        """Deliver the answer to a question a run left (see :meth:`decide`)."""
        asked: RunRecord = await self._runtime.store.get_run(question.run_id)
        text: str = f"Answer to your question “{question.summary}”: {answer}"
        active: RunHandle | None = next(
            (handle for handle in self._handles.values() if handle.thread_id == asked.thread_id), None
        )
        if active is not None:
            self.steer(active.run_id, text, ())
            log.info("question_answered", approval_id=question.id, run_id=active.run_id, delivered="steer")
            return
        newest: RunRecord = (await self._runtime.store.list_runs(1, asked.thread_id))[0]
        if newest.status in _WORKING:
            # A run of another process (``dotx run``) works on the thread, and only that process can steer it.
            raise RunConflict(
                "a run of another process is working on this thread; answer the question once it has ended",
                {"approval_id": question.id, "thread_id": asked.thread_id, "run_id": newest.id},
            )
        run, _ = await self._launch(
            text,
            asked.mode,
            asked.model,
            asked.thread_id,
            source,
            None,
            asked.effort,
            asked.approvals,
            asked.goal_mode,
            RunOpening(text=text, files=(), resumed=None),
        )
        log.info("question_answered", approval_id=question.id, run_id=run.id, delivered="run")

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
            plan=() if plan is None else plan_from_event(plan),
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
    ) -> tuple[RunRecord, RunHandle]:
        """Create the run, register it and start its task. A run launched by an active, untrusted parent is untrusted."""
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
                goal, mode, model_ref, thread.id, source, parent_run_id, level, approval_mode, with_goal_mode, opening
            )
        self._claim_thread(thread_id)
        try:
            await runtime.store.get_thread(thread_id)
            if opening.resumed is not None:
                # Checked again under the claim: a run that started on the thread since, such as another resume of the
                # same run, went on from it already.
                await self._require_newest(thread_id, opening.resumed.from_run_id)
            return await self._register_run(
                goal, mode, model_ref, thread_id, source, parent_run_id, level, approval_mode, with_goal_mode, opening
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
    ) -> tuple[RunRecord, RunHandle]:
        """Create the run record and its gate, start its task and register its handle."""
        runtime: Runtime = self._runtime
        parent: RunHandle | None = None if parent_run_id is None else self._handles.get(parent_run_id)
        instructions: tuple[str, ...] = await self._instructions_for(goal, thread_id, parent_run_id, parent)
        run: RunRecord = await runtime.store.create_run(
            thread_id, goal, mode, model_ref, parent_run_id, source, level, approval_mode, with_goal_mode
        )
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
        return run, handle

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

    async def _check_effort(self, ref: ModelRef, effort: Effort) -> None:
        """A level chosen for a run has to be one the model offers (a model with fewer than two has no choice)."""
        info: ModelInfo = await self._runtime.router.model_info(ref)
        if len(info.efforts) < 2 or effort not in info.efforts:
            raise ConfigError(
                "the model does not offer that reasoning effort",
                {"model": ref.label, "effort": effort, "offered": list(info.efforts)},
            )

    def _place_for(self, run: RunRecord) -> AbstractAsyncContextManager[None]:
        return nullcontext() if run.source == DELEGATE_SOURCE else self._slots

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
        their traceback and recorded as a failed run; they are not hidden, because nothing awaits this task. The handle
        is released and ``finished`` is set even when recording the end fails, so a waiter and the thread never hang.
        The lessons of a run that may hold some are drawn after that, in a task of its own, so that they hold up
        nothing.
        """
        runtime: Runtime = self._runtime
        status: RunStatus = "failed"
        result: str = ""
        lessons: LessonCase | None = None
        try:
            async with self._place_for(run):
                await runtime.store.set_run_status(run.id, "running")
                await runtime.bus.emit(
                    run.id, "run_started", {"model": run.model, "parent_run_id": run.parent_run_id}, True
                )
                ended: RunEnd = await run_agent(AgentRun(runtime, run, gate, cancel, inbox, self, opening))
                status, result, lessons = ended.outcome.status, ended.outcome.result, ended.lessons
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
                runtime.approvals.release_run(run.id)
                clean: str = truncate_tail(redact_secrets(result), _RESULT_CHARS)
                await runtime.store.finish_run(run.id, status, clean)
                await runtime.bus.emit(run.id, "run_finished", {"status": status, "result": clean}, True)
            finally:
                self._handles.pop(run.id, None)
                finished.set()
        # Registered before anything awaits, so that a waiter that ``finished`` wakes finds it.
        if lessons is not None and not self._stopping:
            self._learning[run.id] = asyncio.create_task(self._learn(lessons))

    async def _learn(self, case: LessonCase) -> None:
        """Task body of drawing the lessons of a run that ended. Unexpected exceptions are logged with their traceback;
        the run keeps its end either way."""
        try:
            await learn(self._runtime, self._deciding, case)
        except Exception:
            log.exception("lessons crashed", run_id=case.run_id)
        finally:
            self._learning.pop(case.run_id, None)
