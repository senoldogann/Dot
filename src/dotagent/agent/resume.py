"""Resuming a run that a restart or a shutdown of the daemon cut off, or the end of the ``dotx`` command that drove it.

A resumed run is a new run on the same thread, of source :data:`RESUME_SOURCE`, with the goal, mode, model, reasoning
effort, approval mode and goal mode of the run it resumes. Its first message to the model is a note
(:func:`resume_note`): how that run was cut off, its goal, the calls of the step it was in, and its plan, which the
resumed run takes over. Each call of that step is told apart by how it ended, from the run's events (it finished, it was
cut off while it ran, or it had not started), and by its policy kind, from its tool's ``prepare``, which classifies a
call and runs nothing: a call whose tool only reads may run again, any other may have changed something.

By hand, any run that :func:`can_resume` allows can be resumed. With ``[agent] resume = "safe"`` the daemon also resumes
on its own, when it starts, the runs that :func:`resumes_on_start` allows. Every function here is pure except
:func:`interrupted_calls`, which reads the store and classifies the calls.
"""

import asyncio
from collections.abc import Collection, Mapping, Sequence
from typing import Literal

from pydantic import JsonValue

from dotagent.agent.compact import pending_calls
from dotagent.agent.plan import PlanPayload, plan_lines
from dotagent.agent.progress import can_ask_user, question_mode
from dotagent.agent.verify import new_facts
from dotagent.core.errors import DotError, PolicyDenied, StoreError
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_tail
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.types import ToolCall
from dotagent.policy.types import READ_ONLY_KINDS, ActionInfo, ActionKind
from dotagent.runtime import Runtime
from dotagent.server import UI_SOURCE
from dotagent.store.endings import classify_ending
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.base import PreparedCall, RegisteredTool
from dotagent.tools.context import ToolContext
from dotagent.tools.desktop import DESKTOP_ACT_TOOL

log = get_logger(__name__)

# The ``source`` of the runs that resume another run.
RESUME_SOURCE: str = "resume"
RESUMED_EVENT: str = "resumed"

# The policy kinds of calls that change nothing when they run again. A delegated run may have done anything its mode
# allows, so a delegation is not one of them.
REPEATABLE_KINDS: frozenset[ActionKind] = READ_ONLY_KINDS - {"delegate"}

# Tools whose classification looks at the screen as it is now, which can bring an application to the front: their calls
# are not classified again. None of their kinds only reads.
_SCREEN_CLASSIFIED: frozenset[str] = frozenset({DESKTOP_ACT_TOOL})
_STEP_EVENT: str = "assistant"
_ARGUMENTS_CHARS: int = 200
_RESULT_CHARS: int = 300

type CutOff = Literal["daemon_restarted", "daemon_shutdown", "process_ended", "process_died"]
type CallEnd = Literal["succeeded", "failed", "cut_off", "not_started"]

_CUT_OFF_TEXT: dict[CutOff, str] = {
    "daemon_restarted": "the Dot daemon stopped while it ran (a crash, or a restart that did not wait for it)",
    "daemon_shutdown": "the Dot daemon was shut down while it ran",
    "process_ended": (
        "the dotx command that drove it was told to end while it ran (its terminal was closed, or it got SIGTERM)"
    ),
    "process_died": "the dotx command that drove it died while it ran (it was killed, or it crashed)",
}


class InterruptedCall(FrozenModel):
    """A call of the step a run was in when it was cut off. ``arguments`` is the start of its arguments as JSON,
    ``kind`` its policy kind (``None`` when it cannot be told: an unknown tool, arguments its tool refuses, a check that
    looks at the screen), ``end`` how it ended according to the run's events, and ``result`` the start of what it
    returned (empty unless it finished)."""

    tool: str
    arguments: str
    kind: ActionKind | None
    end: CallEnd
    result: str


class Resumption(FrozenModel):
    """What a resumed run takes over from the run it resumes: how that run was cut off, the calls of the step it was in
    and its plan with its targets. ``automatic`` is true when the daemon resumed it on its own."""

    from_run_id: str
    cut_off: CutOff
    automatic: bool
    calls: tuple[InterruptedCall, ...]
    plan: PlanPayload


def cut_off_by(run: RunRecord) -> CutOff | None:
    """How ``run`` was cut off: the daemon stopped while the run was active (a crash, or a restart that did not wait
    for the run), the daemon was shut down, the ``dotx`` command that drove the run was told to end (its terminal was
    closed, or it got SIGTERM), or that command died (it was killed, or it crashed). ``None`` when anything else ended
    the run, or the run has not ended."""
    match classify_ending(run.status, run.result):
        case "daemon_restarted":
            return "daemon_restarted"
        case "daemon_shutdown":
            return "daemon_shutdown"
        case "process_ended":
            return "process_ended"
        case "process_died":
            return "process_died"
        case _:
            return None


def can_resume(run: RunRecord, newest_run_id: str | None) -> bool:
    """Whether ``run`` may be resumed: it was cut off (:func:`cut_off_by`), no other run started it, and it is the
    newest run still in its thread (``newest_run_id``, ``None`` once a rewind took them all out), so nothing went on
    after it there. A run the owner stopped is never
    resumed, nor is a
    session of a campaign: the campaign goes on with its next session, whose brief says that this one was cut off."""
    return (
        run.parent_run_id is None
        and run.campaign_id is None
        and run.id == newest_run_id
        and cut_off_by(run) is not None
    )


def repeatable(call: InterruptedCall) -> bool:
    """Whether running ``call`` again changes nothing: its tool only reads."""
    return call.kind is not None and call.kind in REPEATABLE_KINDS


def resumes_on_start(run: RunRecord, newest_run_id: str | None, calls: Sequence[InterruptedCall]) -> bool:
    """Whether the daemon resumes ``run`` on its own when it starts: a run of the web UI that may be resumed and whose
    calls that were cut off while they ran only read. A call that finished is reported with its result and one that had
    not started did nothing, so neither counts. A run that the command line, a schedule or an evaluation started waits
    for the owner, and so does a resumed run: a run that brings the daemon down is not resumed again and again."""
    return (
        run.source == UI_SOURCE
        and can_resume(run, newest_run_id)
        and all(repeatable(call) for call in calls if call.end == "cut_off")
    )


def _call_line(call: InterruptedCall) -> str:
    head: str = f"- {call.tool} {call.arguments}"
    match call.end:
        case "succeeded":
            return f"{head}: it finished and succeeded. Its result began: {call.result}"
        case "failed":
            return f"{head}: it finished and failed: {call.result}"
        case "not_started":
            return f"{head}: it had not started, so nothing of it ran."
        case "cut_off" if repeatable(call):
            return f"{head}: it was cut off while it ran. It only reads, so it may run again."
        case "cut_off":
            return (
                f"{head}: it was cut off while it ran and may have changed something: check the state before you run "
                "it again."
            )


def resume_note(goal: str, resumption: Resumption) -> str:
    """The first message of a resumed run: how the run it resumes was cut off, its goal, the calls of the step it was in
    and its plan, and to go on from where it stopped."""
    parts: list[str] = [
        f"[system note] The run that worked on this goal was cut off: {_CUT_OFF_TEXT[resumption.cut_off]}. You go on "
        f"with it; the conversation above holds every step it took. Its goal:\n{goal}"
    ]
    if resumption.calls:
        parts.append(
            "The calls of the step it was in, which the conversation shows as interrupted:\n"
            + "\n".join(_call_line(call) for call in resumption.calls)
        )
    if resumption.plan.steps:
        parts.append(f"Its plan, which is your plan now:\n{plan_lines(resumption.plan)}")
    parts.append(
        "Pick up where it left off: do not redo what is done, check what a call that was cut off may have changed, and "
        "finish the goal."
    )
    return "\n\n".join(parts)


def resumed_event_data(resumption: Resumption) -> JsonObject:
    """The data of the ``resumed`` event: the run it resumes, how that run was cut off, whether the daemon resumed it
    on its own, and the calls of the step it was in."""
    return {
        "from_run_id": resumption.from_run_id,
        "cut_off": resumption.cut_off,
        "automatic": resumption.automatic,
        "calls": [{"tool": call.tool, "end": call.end, "repeatable": repeatable(call)} for call in resumption.calls],
    }


def _field[T](event: EventRecord, key: str, kind: type[T]) -> T:
    value: JsonValue = event.data.get(key)
    if not isinstance(value, kind):
        raise StoreError(
            "a stored event lacks a field",
            {"run_id": event.run_id, "event_id": event.id, "kind": event.kind, "field": key},
        )
    return value


def _call_end(call_id: str, started: Collection[str], results: Mapping[str, EventRecord]) -> CallEnd:
    result: EventRecord | None = results.get(call_id)
    if result is not None:
        return "succeeded" if _field(result, "ok", bool) else "failed"
    return "cut_off" if call_id in started else "not_started"


def _result_start(result: EventRecord | None) -> str:
    return "" if result is None else truncate_tail(" ".join(_field(result, "text", str).split()), _RESULT_CHARS)


class _NoAnswers:
    """The gate of a call that is only classified, never run: nothing can be authorised through it."""

    async def authorize(self, info: ActionInfo) -> str:
        raise PolicyDenied("a call that is only classified cannot be authorised", {"tool": info.tool})

    def add_instruction(self, text: str) -> None:
        raise PolicyDenied("a call that is only classified takes no instructions", {"chars": len(text)})


async def _kind_of(call: ToolCall, context: ToolContext) -> ActionKind | None:
    tool: RegisteredTool | None = context.runtime.registry.tools.get(call.name)
    if tool is None or call.name in _SCREEN_CLASSIFIED:
        return None
    try:
        prepared: PreparedCall = await tool.prepare(call.arguments, context)
    except (DotError, OSError, ValueError) as exc:
        log.warning("resume_call_unclassified", run_id=context.run_id, tool=call.name, error=str(exc))
        return None
    return prepared.action.kind


async def interrupted_calls(runtime: Runtime, run: RunRecord) -> tuple[InterruptedCall, ...]:
    """The calls of the step ``run`` was in when it was cut off, with how each ended and its policy kind; empty when the
    run was not in a step's calls. They are the ones without a result at the end of the thread: those that had not ended
    when a stop cut the step, every call of the step after a crash (:func:`~dotagent.agent.compact.pending_calls`). Its
    events tell which of them started and which finished: the events after the step's ``assistant`` event, since a
    call's id is unique only within its step. When that event is missing, the step's message was stored and nothing of
    the step ran. Reads the store."""
    if run.steps == 0:
        return ()
    calls: tuple[ToolCall, ...] = pending_calls(await runtime.store.load_messages(run.thread_id, 0))
    if not calls:
        return ()
    step: EventRecord | None = await runtime.store.last_event(run.id, _STEP_EVENT)
    started: set[str] = set()
    results: dict[str, EventRecord] = {}
    if step is not None and step.data.get("step") == run.steps:
        started = {
            _field(event, "call_id", str)
            for event in await runtime.store.events_of_kind(run.id, "tool_call", len(calls))
            if event.id > step.id
        }
        results = {
            _field(event, "call_id", str): event
            for event in await runtime.store.events_of_kind(run.id, "tool_result", len(calls))
            if event.id > step.id
        }
    context: ToolContext = ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id=run.id,
        agent_id=run.agent_id,
        thread_id=run.thread_id,
        mode=run.mode,
        approvals=run.approvals,
        questions=question_mode(
            runtime.config.agent.questions, can_ask_user(run.source, run.parent_run_id, run.approvals)
        ),
        workspace=runtime.workspace,
        gate=_NoAnswers(),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
        spawner=None,
    )
    return tuple(
        [
            InterruptedCall(
                tool=call.name,
                arguments=truncate_tail(dumps_compact(call.arguments), _ARGUMENTS_CHARS),
                kind=await _kind_of(call, context),
                end=_call_end(call.id, started, results),
                result=_result_start(results.get(call.id)),
            )
            for call in calls
        ]
    )
