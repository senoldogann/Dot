"""The pass_reply tool (item 926): hand a reply that came to a mail Dot sent to the chat that sent it.

The owner's mail is read by a schedule of theirs ("Müşteri postası", every two hours), whose run finds the replies to
what Dot wrote. Before, such a run answered them in the schedule's own chat, which knew nothing of the work the mail
was part of, or only told of them. The DeepSeek Harness super-agent article's thread pass-through has an event that
belongs to a thread go to the worker of that thread. Here the chat a mail was sent from is kept by the address it went
to, when it was sent (:meth:`dotagent.agent.gate.RunGate._note_correspondents`), and this tool finds it by the sender
of a reply: the code decides where the reply goes, not the model. It starts a run there that reads where the reply came
from and what the run that found it said of it, and goes on with that chat's work. That run starts as untrusted as the
run that found the reply, nobody watches it, and in the owner's chat the review model judges what it would send. A
reply from an address no chat wrote to in the last :data:`_SINCE_DAYS` days is the finding run's to deal with, and one
from the same sender handed in the last :data:`_AGAIN_HOURS` hours is not handed again.
"""

import re
import time

from pydantic import Field

from dotagent.core.errors import PolicyDenied, RunConflict, ToolExecutionError, ToolInputError
from dotagent.core.text import truncate_tail
from dotagent.policy.types import ActionInfo
from dotagent.store.models import Correspondent, RunRecord
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, ok
from dotagent.tools.context import Spawner, ToolContext

PASS_REPLY_TOOL: str = "pass_reply"
_SINCE_DAYS: float = 60.0
_AGAIN_HOURS: float = 12.0
_WHAT_CHARS: int = 1500
_ADDRESS: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


class PassReplyArgs(ToolArgs):
    sender: str = Field(min_length=3, max_length=254, description="The reply's From address.")
    what: str = Field(min_length=1, max_length=_WHAT_CHARS, description="Where the reply is and what it asks.")


def _pass_reply_tool() -> RegisteredTool:
    def classify(args: PassReplyArgs, _ctx: ToolContext) -> ActionInfo:
        sender: str = truncate_tail(args.sender.strip(), 80)
        return ActionInfo(
            tool=PASS_REPLY_TOOL, kind="delegate", risk="safe", summary=f"Hand the reply of {sender} to its chat",
            target=sender, flags=(),
        )  # fmt: skip

    async def run(args: PassReplyArgs, ctx: ToolContext) -> ToolResult:
        sender: str = args.sender.strip().lower()
        if _ADDRESS.fullmatch(sender) is None:
            raise ToolInputError("the sender is a mail address, as the reply's From line has it", {"sender": sender})
        spawner: Spawner | None = ctx.spawner
        if spawner is None:
            raise ToolExecutionError("this run cannot start a run in another chat", {"sender": sender})
        now: float = time.time()
        found: Correspondent | None = await ctx.runtime.store.chat_that_wrote_to(
            sender, ctx.thread_id, now - _SINCE_DAYS * 86_400.0
        )
        if found is None:
            return ok(
                f"No other chat of Dot's wrote to {sender} in the last {_SINCE_DAYS:.0f} days: deal with the reply "
                "here."
            )
        if found.handed_at is not None and now - found.handed_at < _AGAIN_HOURS * 3600.0:
            return ok(f"A reply of {sender} was handed to its chat {found.thread_id} already: nothing more to do.")
        goal: str = f"A reply came from {sender}"
        opening: str = (
            f"A reply came from {sender} to the mail this chat sent (run {found.run_id}). Dot's run that reads the "
            f"owner's mail found it and said of it, in its own words, which come of a mail a third party wrote:\n"
            f"{args.what.strip()}\n\nRead the reply where it is, and go on with this chat's work: answer it or do what "
            "it needs, as the earlier messages here say; what is the owner's alone to decide, leave as a question."
        )
        try:
            started: RunRecord = await spawner.hand_reply(ctx.run_id, found.thread_id, goal, opening)
        except RunConflict:
            return ok(f"A run works in the chat {found.thread_id} of {sender} now: hand the reply again later.")
        except PolicyDenied as refused:
            return fail(f"The reply stays with this run: {refused.message}.")
        await ctx.runtime.store.note_handed(sender, found.thread_id, now)
        return ok(f"Handed to the chat {found.thread_id}, where the work it belongs to goes on (run {started.id}).")

    return make_tool(
        name=PASS_REPLY_TOOL,
        description="Hand a reply to a mail Dot sent to the chat that sent it, where that work goes on.",
        args_model=PassReplyArgs,
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=30.0,
        classify=classify,
        run=run,
    )


def build_reply_tools() -> tuple[RegisteredTool, ...]:
    return (_pass_reply_tool(),)
