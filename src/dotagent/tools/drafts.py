"""``draft_message``: an email or a message a run writes for the owner (item 314). The conversation shows it as a card
on which the owner changes the recipients and the words and sends it, or discards it; nothing goes out before, whatever
the run's approvals. The run goes on at once: a draft does not wait for its answer."""

from typing import Literal, Self

from pydantic import Field, model_validator

from dotagent.core.errors import InvalidValueError, ToolInputError
from dotagent.drafts.service import (
    MAX_BODY_CHARS,
    MAX_RECIPIENTS,
    MAX_SUBJECT_CHARS,
    create_draft,
    draft_problems,
)
from dotagent.policy.elements import looks_like_key
from dotagent.policy.types import ActionInfo, Flag
from dotagent.store.models import DraftRecord
from dotagent.tools.base import ACTING_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, ok
from dotagent.tools.context import ToolContext

DRAFT_TOOL: str = "draft_message"
_TIMEOUT_S: float = 30.0
_SUMMARY_CHARS: int = 200
_WHAT: dict[str, str] = {"mail": "email", "message": "message"}
_A_WHAT: dict[str, str] = {"mail": "an email", "message": "a message"}


class DraftArgs(ToolArgs):
    channel: Literal["mail", "message"] = Field(description="mail: an email; message: an iMessage.")
    to: list[str] = Field(
        min_length=1,
        max_length=MAX_RECIPIENTS,
        description="Email addresses; for a message, phone numbers with the country code work too.",
    )
    cc: list[str] = Field(default_factory=list, max_length=MAX_RECIPIENTS, description="Mail only.")
    subject: str = Field(default="", max_length=MAX_SUBJECT_CHARS, description="Mail only.")
    body: str = Field(
        min_length=1, max_length=MAX_BODY_CHARS, description="The final text, in the owner's voice, no placeholders."
    )

    @model_validator(mode="after")
    def _sendable(self) -> Self:
        problems: list[str] = draft_problems(self.channel, self.to, self.cc, self.subject, self.body)
        if problems:
            raise ValueError(f"this draft could not be sent: {'; '.join(problems)}")
        return self


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def build_draft_tool() -> RegisteredTool:
    def classify(args: DraftArgs, ctx: ToolContext) -> ActionInfo:
        # A draft changes nothing outside Dot: the owner's press of Send on its card is what sends it. A key in it, in
        # an address too, is refused as anywhere else.
        words: str = "\n".join([*args.to, *args.cc, args.subject, args.body])
        flags: tuple[Flag, ...] = ("exfiltration",) if looks_like_key(words) else ()
        about: str = f": {args.subject}" if args.subject.strip() else ""
        return ActionInfo(
            tool=DRAFT_TOOL,
            kind="notes_write",
            risk="safe",
            summary=_clip(f"Draft {_A_WHAT[args.channel]} to {', '.join(args.to)}{about}", _SUMMARY_CHARS),
            target="drafts",
            flags=flags,
        )

    async def run(args: DraftArgs, ctx: ToolContext) -> ToolResult:
        try:
            draft: DraftRecord = await create_draft(
                ctx.runtime.store, ctx.runtime.bus, ctx.run_id, args.channel, args.to, args.cc, args.subject, args.body
            )
        except InvalidValueError as refused:
            # The owner's own conversation with themselves (item 572): the words tell the run what to do instead.
            raise ToolInputError(refused.message, refused.context) from refused
        what: str = _WHAT[draft.channel]
        return ok(
            f"Draft {draft.id} is in the conversation for the owner, who sends it from there, after changing it if "
            f"they wish, or discards it. Nothing has been sent, and nothing goes out until they press Send: do not "
            f"send this {what} another way. Say in your answer that it waits for them."
        )

    return make_tool(
        name=DRAFT_TOOL,
        description=(
            "Draft an email or an iMessage for the owner: a card in the conversation, which they edit and send or "
            "discard. Use it whenever you would send one on their behalf, unless they told you to send it yourself."
        ),
        args_model=DraftArgs,
        modes=ACTING_MODES,
        parallel_safe=True,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )
