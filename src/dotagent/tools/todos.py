"""Dot's own list (item 715): a run puts on it a thing it can do for the owner later, on its own, while the Mac is idle
(:mod:`dotagent.proactive.initiative`). It is Dot's note to itself, as a memory note is, so it is a write of Dot's
memory to the policy: a research run may add one too. The thing keeps whether its run had read content third parties
can author, and the run that does it starts so.

A run may take back a thing it put on the list itself, with a reason (``todo_drop``, item 821): the run that looked for
work on 2026-10-29 put an empty "placeholder" on it by mistake and could not take it off, so the next run did it, and
then changed Dot's database with ``sqlite3`` to close it. A thing whose title is one word (by its spaces: a title in a
script written without them is refused too), or the same as its reason, is refused: it says no work. The owner's box
(:func:`dotagent.proactive.todos.owners_todo`) takes what they write."""

import time

from pydantic import Field, model_validator

from dotagent.policy.types import ActionInfo
from dotagent.proactive.text import one_line
from dotagent.proactive.todos import (
    LET_GO_BY_ITS_RUN,
    TITLE_CHARS,
    WHY_CHARS,
    add_todo,
    alike_ended,
    alike_refusal,
    todo_key,
)
from dotagent.store.models import TodoRecord
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, ok
from dotagent.tools.context import ToolContext

TODO_ADD_TOOL: str = "todo_add"
TODO_DROP_TOOL: str = "todo_drop"
_SUMMARY_CHARS: int = 120
_TIMEOUT_S: float = 15.0
# The fewest characters of again_because that say what is new (item 911).
_AGAIN_CHARS: int = 10


class TodoArgs(ToolArgs):
    title: str = Field(
        min_length=3, max_length=TITLE_CHARS, description="The work, as an instruction you can do alone."
    )
    why: str = Field(min_length=3, max_length=WHY_CHARS, description="Why it helps the owner.")
    again_because: str | None = Field(
        default=None,
        max_length=WHY_CHARS,
        description="Only when todo_add said it is like a thing done lately: what is new since.",
    )

    @model_validator(mode="after")
    def _says_work(self) -> "TodoArgs":
        if len(self.title.split()) < 2 or todo_key(self.title) == todo_key(self.why):
            raise ValueError(
                "say the work as an instruction of a few words, and why it helps the owner in other words: a title of "
                "one word, or the same as its reason, says no work"
            )
        return self


class TodoDropArgs(ToolArgs):
    title: str = Field(min_length=1, max_length=TITLE_CHARS, description="Its title.")
    reason: str = Field(min_length=3, max_length=WHY_CHARS, description="Why.")


def _todo_tool() -> RegisteredTool:
    def classify(args: TodoArgs, ctx: ToolContext) -> ActionInfo:
        return ActionInfo(
            tool=TODO_ADD_TOOL,
            kind="memory_write",
            risk="low",
            summary=one_line(f"Put on Dot's own list: {args.title}", _SUMMARY_CHARS),
            target="todos",
            flags=("persistence",),
        )

    async def run(args: TodoArgs, ctx: ToolContext) -> ToolResult:
        # Like a thing done or let go lately, it goes on the list only with what is new since (item 911).
        again: str = " ".join((args.again_because or "").split())
        if len(again) < _AGAIN_CHARS:
            alike: TodoRecord | None = await alike_ended(ctx.runtime.store, args.title, ctx.runtime.memory.embedder)
            if alike is not None:
                return fail(await alike_refusal(ctx.runtime.store, alike))
        why: str = args.why if len(again) < _AGAIN_CHARS else f"{args.why} Again because: {again}"
        added: TodoRecord | str = await add_todo(
            ctx.runtime.store, args.title, why, "run", ctx.run_id, ctx.ever_tainted()
        )
        if isinstance(added, str):
            return fail(added)
        return ok(f"On Dot's list: {added.title}. It is done on its own while the Mac is idle, when that is on.")

    return make_tool(
        name=TODO_ADD_TOOL,
        description=(
            "Put on your own list work you can do later for the owner, alone, while the Mac is idle: what they asked "
            "for later, a follow-up, a chore you noticed. Not this run's work, nor what needs their decision."
        ),
        args_model=TodoArgs,
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def _drop_tool() -> RegisteredTool:
    def classify(args: TodoDropArgs, ctx: ToolContext) -> ActionInfo:
        return ActionInfo(
            tool=TODO_DROP_TOOL,
            kind="memory_write",
            risk="low",
            summary=one_line(f"Take back from Dot's own list: {args.title}", _SUMMARY_CHARS),
            target="todos",
            flags=(),
        )

    async def run(args: TodoDropArgs, ctx: ToolContext) -> ToolResult:
        store = ctx.runtime.store
        kept: TodoRecord | None = await store.same_todo(todo_key(args.title), time.time())
        # The run that works on a thing lets it go when the owner said not to do it (item 878): on the owner's Mac
        # one such thing was tried three times, each run refusing it, before it was let go.
        works_on: bool = kept is not None and kept.run_id == ctx.run_id and kept.status == "doing"
        if kept is None or not (works_on or kept.source_run_id == ctx.run_id):
            return fail(
                "This run put no such thing on the list and does not work on it: a run takes back only what it put "
                "on it, or lets go of the thing it works on. The owner takes anything off it on the Schedules page."
            )
        if not works_on and kept.status != "open":
            return fail(f"It is {kept.status} already, not to do: {kept.title}")
        note: str = f"{LET_GO_BY_ITS_RUN}{args.reason}" if works_on else f"Taken back: {args.reason}"
        await store.set_todo(kept.id, "dropped", kept.attempts, kept.run_id, one_line(note, 200))
        return ok(f"Off Dot's list: {kept.title}")

    return make_tool(
        name=TODO_DROP_TOOL,
        description="Take back a thing this run put on your list, or let go of the one it works on.",
        args_model=TodoDropArgs,
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def build_todo_tools() -> tuple[RegisteredTool, ...]:
    return (_todo_tool(), _drop_tool())
