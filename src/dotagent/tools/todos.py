"""Dot's own list (item 715): a run puts on it a thing it can do for the owner later, on its own, while the Mac is idle
(:mod:`dotagent.proactive.initiative`). It is Dot's note to itself, as a memory note is, so it is a write of Dot's
memory to the policy: a research run may add one too. The thing keeps whether its run had read content third parties
can author, and the run that does it starts so."""

from pydantic import Field

from dotagent.policy.types import ActionInfo
from dotagent.proactive.text import one_line
from dotagent.proactive.todos import TITLE_CHARS, WHY_CHARS, add_todo
from dotagent.store.models import TodoRecord
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, ok
from dotagent.tools.context import ToolContext

TODO_ADD_TOOL: str = "todo_add"
_SUMMARY_CHARS: int = 120
_TIMEOUT_S: float = 15.0


class TodoArgs(ToolArgs):
    title: str = Field(
        min_length=3, max_length=TITLE_CHARS, description="The work, as an instruction you can do alone."
    )
    why: str = Field(min_length=3, max_length=WHY_CHARS, description="Why it helps the owner.")


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
        added: TodoRecord | str = await add_todo(
            ctx.runtime.store, args.title, args.why, "run", ctx.run_id, ctx.tainted()
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


def build_todo_tools() -> tuple[RegisteredTool, ...]:
    return (_todo_tool(),)
