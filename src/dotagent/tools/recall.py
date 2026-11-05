"""The recall tool (item 924): read a record of Dot's own store by its id, with the ids of the records next to it.

A run's context is compacted when it grows, and the note that replaces the earlier conversation lists the ids of Dot's
own records it named (:mod:`dotagent.agent.continuity`): a run, a chat, a thing on Dot's list, a work order, a
schedule, an approval, an objective. ``recall`` reads one of them back, and each answer names the ids of its neighbours
(the chat of a run, the runs of a chat, the run of a thing to do), so that a run that comes back to old work recalls it
step by step, as the DeepSeek Harness super-agent article's Chain of Recall does, rather than from a summary that lost
it. What a record holds that a run which read content third parties can author wrote (its answer, a step it asked
about, a title it put on the list) is fenced and marks the reader untrusted, as reading that run's chat does.
"""

import asyncio
import secrets
from collections.abc import Awaitable, Callable

from pydantic import Field

from dotagent.core.errors import NotFoundError, ToolInputError
from dotagent.core.text import redact_known, truncate_tail, unclosing
from dotagent.policy.types import ActionInfo
from dotagent.secrets import known_secrets
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, RunRecord, ThreadRecord, TodoRecord
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, mark_untrusted, ok
from dotagent.tools.context import ToolContext

RECALL_TOOL: str = "recall"
_NONCE_BYTES: int = 8
# How much of a record's words an answer quotes, and how many of a chat's runs it names.
_GOAL_CHARS: int = 300
_RESULT_CHARS: int = 600
_RUNS_OF_A_CHAT: int = 8


class RecallArgs(ToolArgs):
    id: str = Field(
        min_length=5,
        max_length=64,
        pattern=r"^[a-z]+_[a-z0-9]+_[a-z0-9]+$",
        description="An id of Dot's: run_, thr_ (a chat), todo_, wo_ (work order), sch_, apr_ or obj_.",
    )


class _Recalled:
    """What one record says: its lines, and whether any of them is the words of a run that read content third parties
    can author."""

    def __init__(self, lines: list[str], tainted: bool) -> None:
        self.lines: list[str] = lines
        self.tainted: bool = tainted


def _clip(text: str, chars: int) -> str:
    return truncate_tail(" ".join(text.split()), chars)


async def _run(store: Store, record_id: str) -> _Recalled:
    run: RunRecord = await store.get_run(record_id)
    tainted: bool = await store.run_ever_tainted(run.id)
    lines: list[str] = [
        f"Run {run.id}: {run.status}, a {run.mode} run of {run.source}, chat {run.thread_id}"
        + ("" if run.parent_run_id is None else f", delegated by run {run.parent_run_id}"),
        f"Goal: {_clip(run.goal, _GOAL_CHARS)}",
    ]
    if run.result.strip():
        lines.append(f"Answer: {_clip(run.result, _RESULT_CHARS)}")
    return _Recalled(lines, tainted)


async def _thread(store: Store, record_id: str) -> _Recalled:
    thread: ThreadRecord = await store.get_thread(record_id)
    runs: list[RunRecord] = await store.list_runs(_RUNS_OF_A_CHAT, thread.id)
    tainted: bool = any([await store.run_ever_tainted(run.id) for run in runs])
    lines: list[str] = [f"Chat {thread.id}: {_clip(thread.title, _GOAL_CHARS)} (opened by {thread.source})"]
    lines.extend(f"- run {run.id} ({run.status}): {_clip(run.goal, 120)}" for run in runs)
    return _Recalled(lines, tainted)


async def _todo(store: Store, record_id: str) -> _Recalled:
    todo: TodoRecord = await store.get_todo(record_id)
    tainted: bool = todo.tainted or (todo.run_id is not None and await store.run_ever_tainted(todo.run_id))
    lines: list[str] = [
        f"Thing to do {todo.id}: {todo.status}, {todo.attempts} tries, put on the list by {todo.source}"
        + ("" if todo.run_id is None else f", last run {todo.run_id}"),
        f"Title: {_clip(todo.title, _GOAL_CHARS)}",
    ]
    if todo.note.strip():
        lines.append(f"What came of it: {_clip(todo.note, _RESULT_CHARS)}")
    return _Recalled(lines, tainted)


async def _work_order(store: Store, record_id: str) -> _Recalled:
    order = await store.get_work_order(record_id)
    lines: list[str] = [
        f"Work order {order.id}: {order.status}, {order.attempts} tries, agent {order.agent_id}"
        + ("" if order.run_id is None else f", run {order.run_id}")
        + ("" if order.thread_id is None else f", chat {order.thread_id}")
        + ("" if order.objective_id is None else f", objective {order.objective_id}"),
        f"Title: {_clip(order.title, _GOAL_CHARS)}",
    ]
    if order.note.strip():
        lines.append(f"Note: {_clip(order.note, _RESULT_CHARS)}")
    return _Recalled(lines, order.tainted)


async def _schedule(store: Store, record_id: str) -> _Recalled:
    schedule = await store.get_schedule(record_id)
    lines: list[str] = [
        f"Schedule {schedule.id}: {schedule.name} ({'on' if schedule.enabled else 'off'}, last {schedule.last_status})"
        + ("" if schedule.thread_id is None else f", chat {schedule.thread_id}"),
        f"Goal: {_clip(schedule.goal, _GOAL_CHARS)}",
    ]
    # A schedule a run made after it read outside content may hold those words in its name and goal, as its firings
    # start marked for (item 978, the security audit of 2026-11-05).
    return _Recalled(lines, schedule.options.from_outside)


async def _approval(store: Store, record_id: str) -> _Recalled:
    approval: ApprovalRecord = await store.get_approval(record_id)
    tainted: bool = await store.run_ever_tainted(approval.run_id)
    lines: list[str] = [
        f"Approval {approval.id}: {approval.status} ({approval.tool}), run {approval.run_id}",
        f"Step: {_clip(approval.summary, _GOAL_CHARS)}",
    ]
    if approval.note.strip():
        lines.append(f"The owner's note: {_clip(approval.note, _GOAL_CHARS)}")
    return _Recalled(lines, tainted)


async def _objective(store: Store, record_id: str) -> _Recalled:
    objective = await store.get_objective(record_id)
    lines: list[str] = [
        f"Objective {objective.id}: {objective.status}",
        f"Title: {_clip(objective.title, _GOAL_CHARS)}",
        *(f"- {_clip(note.text, 200)}" for note in objective.notes[-3:]),
    ]
    tainted: bool = objective.tainted or any(note.tainted for note in objective.notes[-3:])
    return _Recalled(lines, tainted)


# The readers of the records a run may recall, by the prefix of their ids (:func:`dotagent.core.ids.new_id`).
_READERS: dict[str, Callable[[Store, str], Awaitable[_Recalled]]] = {
    "run": _run,
    "thr": _thread,
    "todo": _todo,
    "wo": _work_order,
    "sch": _schedule,
    "apr": _approval,
    "obj": _objective,
}


def _recall_tool() -> RegisteredTool:
    def classify(args: RecallArgs, _ctx: ToolContext) -> ActionInfo:
        return ActionInfo(
            tool=RECALL_TOOL, kind="read", risk="safe", summary=f"Recall {args.id}", target=args.id, flags=()
        )

    async def run(args: RecallArgs, ctx: ToolContext) -> ToolResult:
        prefix: str = args.id.split("_", 1)[0]
        reader: Callable[[Store, str], Awaitable[_Recalled]] | None = _READERS.get(prefix)
        if reader is None:
            raise ToolInputError(
                "recall reads a run, a chat, a thing to do, a work order, a schedule, an approval or an objective",
                {"id": args.id, "prefixes": ", ".join(sorted(_READERS))},
            )
        try:
            recalled: _Recalled = await reader(ctx.runtime.store, args.id)
        except NotFoundError as error:
            raise ToolInputError("no record of Dot's has that id", {"id": args.id}) from error
        held: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
        text: str = redact_known("\n".join(recalled.lines), held)
        if not recalled.tainted:
            return ok(text)
        nonce: str = secrets.token_hex(_NONCE_BYTES)
        fenced: str = (
            f'<recalled nonce="{nonce}" id="{args.id}">\n{unclosing(text, "recalled")}\n</recalled nonce="{nonce}">'
        )
        return mark_untrusted(
            ok(f"{args.id} holds the words of a run that read content third parties can author:\n{fenced}")
        )

    return make_tool(
        name=RECALL_TOOL,
        description="Read back a record of Dot's by its id, with the ids next to it.",
        args_model=RecallArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=15.0,
        classify=classify,
        run=run,
    )


def build_recall_tools() -> tuple[RegisteredTool, ...]:
    return (_recall_tool(),)
