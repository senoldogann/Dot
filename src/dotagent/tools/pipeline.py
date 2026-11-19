"""The pipeline tools (item 1227): the ledger of a continuing piece of work, kept in its record file
(:mod:`dotagent.pipeline.ledger`, :mod:`dotagent.pipeline.record`). ``pipeline_view`` reads it; ``pipeline_client``
adds or changes a client; ``pipeline_begin`` writes an external action down before it goes out and ``pipeline_end``
says what came of it, so that no action is sent twice after a run broke off or a send whose result was not seen;
``pipeline_outcome`` keeps what the client did, dated and sourced, apart from what Dot did.

Each writes only the block of the record the ledger lives in, as a file write in the workspace, noted for ``dotx
undo``. A run that read content third parties can author (a reply's) may keep the ledger, but may not move a client
the owner held back (``paused``, ``do-not-contact``) to any other status: that is the owner's word."""

import asyncio
import secrets
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import Field

from dotagent.agent.orders import fenced
from dotagent.pipeline import record as records
from dotagent.pipeline.ledger import (
    ActionResult,
    ClientChange,
    Ledger,
    LedgerRefusal,
    action_key,
    begin,
    end,
    minute,
    note_outcome,
    summary,
    upsert_client,
)
from dotagent.pipeline.record import RecordRefusal
from dotagent.policy.types import ActionInfo
from dotagent.proactive.text import one_line
from dotagent.tools.base import (
    ACTING_MODES,
    ALL_MODES,
    RegisteredTool,
    ToolArgs,
    ToolResult,
    fail,
    make_path_tool,
    mark_untrusted,
    ok,
)
from dotagent.tools.context import ToolContext
from dotagent.tools.files import journal_for, read_action, resolve_path, write_action

PIPELINE_VIEW_TOOL: str = "pipeline_view"
PIPELINE_CLIENT_TOOL: str = "pipeline_client"
PIPELINE_BEGIN_TOOL: str = "pipeline_begin"
PIPELINE_END_TOOL: str = "pipeline_end"
PIPELINE_OUTCOME_TOOL: str = "pipeline_outcome"
PIPELINE_TOOLS: tuple[str, ...] = (
    PIPELINE_VIEW_TOOL,
    PIPELINE_CLIENT_TOOL,
    PIPELINE_BEGIN_TOOL,
    PIPELINE_END_TOOL,
    PIPELINE_OUTCOME_TOOL,
)
_TIMEOUT_S: float = 20.0
_TEXT_CHARS: int = 400
_NONCE_BYTES: int = 8
_RECORD: str = "The record file in the workspace."
_HELD: frozenset[str] = frozenset({"paused", "do-not-contact"})


class RecordArgs(ToolArgs):
    record: str = Field(min_length=1, max_length=300, description=_RECORD)


class ViewArgs(RecordArgs):
    client: str | None = Field(default=None, max_length=48, description="One client's id, for its history.")


class ClientArgs(RecordArgs):
    client: str = Field(min_length=1, max_length=48, description="Id: a-z, 0-9, hyphens.")
    name: str | None = Field(default=None, max_length=120)
    contact: str | None = Field(default=None, max_length=200)
    status: str | None = Field(
        default=None,
        max_length=20,
        description="new contacted waiting replied meeting proposal won lost paused do-not-contact",
    )
    expecting: str | None = Field(default=None, max_length=_TEXT_CHARS)
    next_step: str | None = Field(default=None, max_length=_TEXT_CHARS)
    next_at: str | None = Field(default=None, max_length=32, description="2026-11-21 or 2026-11-21 09:00")


class BeginArgs(RecordArgs):
    client: str = Field(min_length=1, max_length=48)
    action: str = Field(min_length=1, max_length=48, description="Same on every try: first-mail, reminder-1.")
    variant: str = Field(default="", max_length=60, description="The way of doing it, to compare ways.")


class EndArgs(RecordArgs):
    key: str = Field(min_length=3, max_length=100, description="<client>/<action>")
    result: ActionResult = Field(description="What you saw; uncertain when you could not tell, never failed then.")
    evidence: str = Field(min_length=3, max_length=_TEXT_CHARS, description="What showed it, with its time.")


class OutcomeArgs(RecordArgs):
    client: str = Field(min_length=1, max_length=48)
    outcome: str = Field(
        min_length=1, max_length=20, description="reply meeting proposal won lost declined bounced unsubscribed"
    )
    at: str = Field(min_length=10, max_length=32, description="As the source dates it.")
    source: str = Field(min_length=3, max_length=_TEXT_CHARS, description="Where you saw it.")


def _refused(refusal: RecordRefusal) -> ToolResult:
    """A refusal as the run reads it: fenced and marking the run when the block holds outside words, which it may
    quote."""
    if not refusal.outside:
        return fail(refusal.message)
    return mark_untrusted(fail(fenced("pipeline refusal", refusal.message, secrets.token_hex(_NONCE_BYTES))))


def _now() -> str:
    return minute(datetime.now())


def _record(raw: str, ctx: ToolContext) -> Path:
    return resolve_path(raw, ctx)


async def _change[T](
    ctx: ToolContext, authorized: ActionInfo, tool: str, apply_: Callable[[Ledger], tuple[Ledger, T]]
) -> T:
    """Change the record the call was allowed to write (its resolved path, item 681), marked as written by a run that
    read outside content when this one did."""
    return await asyncio.to_thread(
        records.change, Path(authorized.target), journal_for(ctx, tool), ctx.ever_tainted(), apply_
    )


def _write(tool: str, verb: str) -> Callable[[RecordArgs, ToolContext], ActionInfo]:
    """The classify of a write tool: a write of the record, weighed by where it is, as ``write_file``'s."""

    def classify(args: RecordArgs, ctx: ToolContext) -> ActionInfo:
        info: ActionInfo = write_action(
            tool, _record(args.record, ctx), ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths, verb
        )
        return info.model_copy(update={"summary": one_line(f"{verb} in {args.record}", 120)})

    return classify


def _view_tool() -> RegisteredTool:
    def classify(args: ViewArgs, ctx: ToolContext) -> ActionInfo:
        return read_action(
            PIPELINE_VIEW_TOOL,
            _record(args.record, ctx),
            ctx.workspace,
            ctx.runtime.protected,
            ctx.runtime.dot_paths,
            "Read the pipeline of",
        )

    async def run(args: ViewArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        ledger: Ledger = await asyncio.to_thread(records.read, Path(authorized.target))
        try:
            text: str = summary(ledger, args.client)
        except LedgerRefusal as refusal:
            return fail(str(refusal))
        if not ledger.outside:
            return ok(text)
        # A run that had read a third party's words (a reply's) wrote here: its cells are fenced as theirs, and the run
        # that reads them is marked as having read outside content.
        return mark_untrusted(ok(fenced("pipeline ledger", text, secrets.token_hex(_NONCE_BYTES))))

    return make_path_tool(
        name=PIPELINE_VIEW_TOOL,
        description=(
            "Read a continuing work's ledger in its record: unsettled actions first, then each client, then how each "
            "way did. Read it before acting for a client."
        ),
        args_model=ViewArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def _client_tool() -> RegisteredTool:
    async def run(args: ClientArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        change: ClientChange = ClientChange(
            name=args.name,
            contact=args.contact,
            status=args.status,
            expecting=args.expecting,
            next_step=args.next_step,
            next_at=args.next_at,
        )
        tainted: bool = ctx.ever_tainted()

        def apply_(ledger: Ledger) -> tuple[Ledger, str]:
            present = next((each for each in ledger.clients if each.id == args.client), None)
            if (
                tainted
                and present is not None
                and present.status in _HELD
                and args.status is not None
                and args.status != present.status
            ):
                raise LedgerRefusal(
                    f"{present.name} is {present.status} on the owner's word: a run that read outside content does "
                    "not change that"
                )
            return upsert_client(ledger, args.client, change), "added" if present is None else "changed"

        try:
            said: str = await _change(ctx, authorized, PIPELINE_CLIENT_TOOL, apply_)
        except RecordRefusal as refusal:
            return _refused(refusal)
        return ok(f"The client {args.client} is {said} in {args.record}.")

    return make_path_tool(
        name=PIPELINE_CLIENT_TOOL,
        description="Add or change a client in the ledger; do-not-contact and paused only on the owner's word.",
        args_model=ClientArgs,
        modes=ACTING_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=_write(PIPELINE_CLIENT_TOOL, "Keep a client"),
        run=run,
    )


def _begin_tool() -> RegisteredTool:
    async def run(args: BeginArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        now: str = _now()
        try:
            await _change(
                ctx,
                authorized,
                PIPELINE_BEGIN_TOOL,
                lambda ledger: (begin(ledger, args.client, args.action, args.variant, now), None),
            )
        except RecordRefusal as refusal:
            return _refused(refusal)
        key: str = action_key(args.client, args.action)
        return ok(
            f"{key} is begun at {now}. Send it now, then end it with pipeline_end: sent or failed with what showed it, "
            "uncertain when you could not see."
        )

    return make_path_tool(
        name=PIPELINE_BEGIN_TOOL,
        description=(
            "Write down a mail or message to a client right before you send it; never send one without it. Refused "
            "if it was sent, or its last try's result is unseen."
        ),
        args_model=BeginArgs,
        modes=ACTING_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=_write(PIPELINE_BEGIN_TOOL, "Begin an action"),
        run=run,
    )


def _end_tool() -> RegisteredTool:
    async def run(args: EndArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        now: str = _now()
        try:
            await _change(
                ctx,
                authorized,
                PIPELINE_END_TOOL,
                lambda ledger: (end(ledger, args.key, args.result, args.evidence, now), None),
            )
        except RecordRefusal as refusal:
            return _refused(refusal)
        follow: str = {
            "sent": "Set what answer you expect and the next step with pipeline_client.",
            "failed": "It may be begun again once what failed is mended.",
            "uncertain": "It stays in the way of a new try until a run looks again and ends it sent or failed.",
        }[args.result]
        return ok(f"{args.key} ended {args.result} at {now}. {follow}")

    return make_path_tool(
        name=PIPELINE_END_TOOL,
        description="Say what came of an action you began, with what showed it.",
        args_model=EndArgs,
        modes=ACTING_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=_write(PIPELINE_END_TOOL, "End an action"),
        run=run,
    )


def _outcome_tool() -> RegisteredTool:
    async def run(args: OutcomeArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        try:
            added: bool = await _change(
                ctx,
                authorized,
                PIPELINE_OUTCOME_TOOL,
                lambda ledger: note_outcome(ledger, args.client, args.outcome, args.at, args.source),
            )
        except RecordRefusal as refusal:
            return _refused(refusal)
        if not added:
            return ok(f"That {args.outcome} of {args.client} at {args.at} is kept already.")
        return ok(
            f"The {args.outcome} of {args.client} at {args.at} is kept. Update the client's status and next step."
        )

    return make_path_tool(
        name=PIPELINE_OUTCOME_TOOL,
        description="Keep what a client did (a reply, a deal, a no), dated and sourced; never what you did not see.",
        args_model=OutcomeArgs,
        modes=ACTING_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=_write(PIPELINE_OUTCOME_TOOL, "Keep an outcome"),
        run=run,
    )


def build_pipeline_tools() -> tuple[RegisteredTool, ...]:
    return (_view_tool(), _client_tool(), _begin_tool(), _end_tool(), _outcome_tool())
