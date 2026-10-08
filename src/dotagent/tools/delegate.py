"""The delegate tool: hand one self-contained part of the work to a run of its own and read its report."""

import secrets
from typing import Literal

from pydantic import Field

from dotagent.core.errors import ToolExecutionError
from dotagent.core.text import truncate_tail
from dotagent.policy.types import ActionInfo
from dotagent.store.models import RunMode, RunRecord
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, mark_untrusted, ok
from dotagent.tools.context import DelegateOutcome, Spawner, ToolContext

DELEGATE_TOOL: str = "delegate"
DELEGATE_SOURCE: str = "delegate"
_NONCE_BYTES: int = 8
_TIMEOUT_S: float = 3600.0
_SUMMARY_CHARS: int = 100
_NO_REPORT: str = "(the run reported nothing)"


class DelegateArgs(ToolArgs):
    goal: str = Field(
        min_length=1,
        description=(
            "A complete instruction for the delegated agent: what to do, every concrete input it needs (paths, URLs, "
            "values) and what its final answer must contain. It cannot see this conversation."
        ),
    )
    mode: Literal["task", "research"] | None = Field(
        default=None,
        description=(
            "task lets it act like you; research keeps it read-only. Defaults to your own mode; a research run can "
            "only delegate research."
        ),
    )


def default_child_mode(parent: RunMode) -> RunMode:
    """A research run hands research on; every other run hands a task."""
    return "research" if parent == "research" else "task"


def _fence(run: RunRecord, body: str, nonce: str) -> str:
    """Wrap a report that may carry third-party text so the prompt can state that only ``nonce`` closes it."""
    safe: str = body.replace("</delegate_content", "<\\/delegate_content")
    return f'<delegate_content nonce="{nonce}" run="{run.id}">\n{safe}\n</delegate_content nonce="{nonce}">'


def render_outcome(outcome: DelegateOutcome, nonce: str) -> ToolResult:
    """What the delegating run reads back: how the run ended and its report, fenced when it saw untrusted content."""
    run: RunRecord = outcome.run
    header: str = f"Delegated run {run.id} ended as {run.status} after {run.steps} steps."
    report: str = run.result.strip() or _NO_REPORT
    text: str = f"{header}\n{_fence(run, report, nonce)}" if outcome.tainted else f"{header}\n{report}"
    result: ToolResult = ok(text) if run.status == "done" else fail(text)
    return mark_untrusted(result) if outcome.tainted else result


def _delegate_tool() -> RegisteredTool:
    def classify(args: DelegateArgs, ctx: ToolContext) -> ActionInfo:
        shown: str = truncate_tail(" ".join(args.goal.split()), _SUMMARY_CHARS)
        return ActionInfo(
            tool=DELEGATE_TOOL, kind="delegate", risk="safe", summary=f"Delegate: {shown}", target="", flags=()
        )

    async def run(args: DelegateArgs, ctx: ToolContext) -> ToolResult:
        spawner: Spawner | None = ctx.spawner
        if spawner is None:
            raise ToolExecutionError("this run cannot start other runs", {"run_id": ctx.run_id})
        mode: RunMode = args.mode if args.mode is not None else default_child_mode(ctx.mode)
        outcome: DelegateOutcome = await spawner.delegate(ctx.run_id, args.goal, mode)
        return render_outcome(outcome, secrets.token_hex(_NONCE_BYTES))

    return make_tool(
        name=DELEGATE_TOOL,
        description=(
            "Run one self-contained part of the work in a separate agent with a clean context and get its final "
            "report back. Use it to keep your own context small when a part is bulky (reading many pages or files, a "
            "deep research question) and to do independent parts at the same time: call it several times in one "
            "turn. The agent uses the same model and tools, cannot ask the user questions and cannot delegate again. "
            "Do not delegate what one or two tool calls can do."
        ),
        args_model=DelegateArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def build_delegate_tools() -> tuple[RegisteredTool, ...]:
    return (_delegate_tool(),)
