"""``mail_draft`` (item 2047): an email a run writes for the owner as a draft in their own Gmail or Outlook, through
the account they signed in, an answer in the thread of a message ``mail_search`` found or a new mail; or a draft of the
chat's that still waits, written again. The conversation shows it as a draft card (item 314); the owner opens it there
or in the mailbox, changes it, and sends it from either. Nothing goes out before: a run never sends it, whatever its
approvals, as with ``draft_message``."""

from typing import Self

from pydantic import Field, model_validator

from dotagent.connectors.types import OutgoingMail
from dotagent.core.errors import ToolInputError
from dotagent.drafts.service import (
    MAX_BODY_CHARS,
    MAX_RECIPIENTS,
    MAX_SUBJECT_CHARS,
    PROVIDER_NAMES,
    create_mailbox_draft,
    draft_problems,
    rewrite_mailbox_draft,
)
from dotagent.policy.elements import looks_like_key
from dotagent.policy.types import ActionInfo, Flag
from dotagent.store.models import DraftRecord
from dotagent.tools.base import ACTING_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, ok
from dotagent.tools.context import ToolContext

MAIL_DRAFT_TOOL: str = "mail_draft"
_TIMEOUT_S: float = 90.0
_SUMMARY_CHARS: int = 200


class MailDraftArgs(ToolArgs):
    account: str = Field(min_length=3, max_length=320, description="The signed-in account the draft is made in.")
    to: list[str] = Field(min_length=1, max_length=MAX_RECIPIENTS, description="Email addresses.")
    cc: list[str] = Field(default_factory=list, max_length=MAX_RECIPIENTS)
    subject: str = Field(min_length=1, max_length=MAX_SUBJECT_CHARS)
    body: str = Field(
        min_length=1, max_length=MAX_BODY_CHARS, description="The final text, in the owner's voice, no placeholders."
    )
    reply_to: str = Field(
        default="", max_length=1000, description="The id mail_search gave the message this answers, in its thread."
    )
    draft: str = Field(default="", max_length=64, description="A waiting draft's id, to write it again.")

    @model_validator(mode="after")
    def _sendable(self) -> Self:
        problems: list[str] = draft_problems("mail", self.to, self.cc, self.subject, self.body)
        if problems:
            raise ValueError(f"this draft could not be sent: {'; '.join(problems)}")
        if self.draft and self.reply_to:
            raise ValueError("a draft written again keeps the message it answers: give draft or reply_to, not both")
        return self


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def _mail(args: MailDraftArgs) -> OutgoingMail:
    return OutgoingMail(
        to=tuple(address.strip() for address in args.to),
        cc=tuple(address.strip() for address in args.cc),
        subject=args.subject.strip(),
        body=args.body,
    )


async def _chats_draft(ctx: ToolContext, draft_id: str) -> DraftRecord:
    """The chat's draft ``draft_id`` that still takes an answer: a run writes again only a draft of its own chat."""
    found: DraftRecord | None = next(
        (draft for draft in await ctx.runtime.store.thread_drafts(ctx.thread_id) if draft.id == draft_id), None
    )
    if found is None:
        raise ToolInputError(
            f"{MAIL_DRAFT_TOOL}: no draft {draft_id} of this chat waits for the owner (sent, discarded or another "
            "chat's): make a new one without draft",
            {"draft": draft_id},
        )
    return found


def build_mail_draft_tool() -> RegisteredTool:
    def classify(args: MailDraftArgs, ctx: ToolContext) -> ActionInfo:
        # A draft in the owner's own mailbox reaches no one: the owner's press of Send, on its card or in the mailbox,
        # is what sends it. A key in it, in an address too, is refused as anywhere else.
        words: str = "\n".join([*args.to, *args.cc, args.subject, args.body])
        flags: tuple[Flag, ...] = ("exfiltration",) if looks_like_key(words) else ()
        what: str = "Rewrite the draft" if args.draft else "Draft an email"
        return ActionInfo(
            tool=MAIL_DRAFT_TOOL,
            kind="notes_write",
            risk="safe",
            summary=_clip(f"{what} in {args.account} to {', '.join(args.to)}: {args.subject}", _SUMMARY_CHARS),
            target=f"account:{args.account.strip().casefold()}",
            flags=flags,
        )

    async def run(args: MailDraftArgs, ctx: ToolContext) -> ToolResult:
        mail: OutgoingMail = _mail(args)
        draft: DraftRecord
        if args.draft:
            found: DraftRecord = await _chats_draft(ctx, args.draft)
            if found.mailbox is None or found.mailbox.account.casefold() != args.account.strip().casefold():
                raise ToolInputError(
                    f"{MAIL_DRAFT_TOOL}: the draft {args.draft} is not in the mailbox of {args.account}",
                    {"draft": args.draft, "account": args.account},
                )
            draft = await rewrite_mailbox_draft(
                ctx.runtime.store, ctx.runtime.bus, ctx.runtime.accounts, found.id, mail
            )
        else:
            draft = await create_mailbox_draft(
                ctx.runtime.store,
                ctx.runtime.bus,
                ctx.runtime.accounts,
                ctx.run_id,
                args.account,
                mail,
                args.reply_to.strip() or None,
            )
        place: str = (
            "nowhere"
            if draft.mailbox is None
            else f"{PROVIDER_NAMES[draft.mailbox.provider]} of {draft.mailbox.account}"
        )
        return ok(
            f"Draft {draft.id} is in the Drafts of {place} and on a card in the conversation. The owner sends it from "
            "either, after changing it if they wish, or discards it. Nothing has been sent, and nothing goes out "
            "until they press Send: do not send this email another way. Say in your answer that it waits for them; "
            f"to change it, call {MAIL_DRAFT_TOOL} again with draft {draft.id}."
        )

    return make_tool(
        name=MAIL_DRAFT_TOOL,
        description=(
            "Draft an email in the owner's own Gmail or Outlook, a reply in its thread or a new mail: a draft they "
            "open, edit and send there or from a card here. Prefer it to draft_message for a signed-in account."
        ),
        args_model=MailDraftArgs,
        modes=ACTING_MODES,
        parallel_safe=True,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )
