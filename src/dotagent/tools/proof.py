"""``add_proof`` (item 1853): a run adds a check that has to exit 0 before it counts as done.

A task run adds it to its own checks; a session of a campaign adds it to the campaign's, which every later session's
claim is held to, so that write carries the ``persistence`` flag as ``campaign_targets`` does. Adding runs nothing:
Dot runs the checks at the run's finish (and at a campaign's claim) as ``shell`` calls of the run, through the gate.
Checks are only added: no tool changes or removes one, so a run cannot soften what it is held to.
"""

from pydantic import Field

from dotagent.agent.proof import (
    PROOF_EVENT,
    added,
    limit_refused,
    proof_event_data,
    proof_refused,
    run_checks,
)
from dotagent.core.text import truncate_middle
from dotagent.llm.router import parse_model_ref
from dotagent.policy.types import ActionInfo, Flag
from dotagent.store.models import (
    DEFAULT_PROOF_TIMEOUT_S,
    MAX_PROOF_CHECKS,
    MAX_PROOF_TIMEOUT_S,
    CampaignRecord,
    ProofCheck,
    ProofCommand,
    RunRecord,
)
from dotagent.tools.base import RegisteredTool, ToolArgs, ToolResult, fail, make_tool, ok
from dotagent.tools.context import ToolContext

ADD_PROOF_TOOL: str = "add_proof"
_TIMEOUT_S: float = 30.0


class AddProofArgs(ToolArgs):
    command: ProofCommand = Field(
        description=(
            "A command line that exits 0 only when the work is really done, for example 'uv run pytest tests/x -q' or "
            "'test -s ~/report.pdf'. Use absolute paths or the workspace."
        ),
    )
    timeout_s: int = Field(
        default=DEFAULT_PROOF_TIMEOUT_S,
        ge=1,
        le=MAX_PROOF_TIMEOUT_S,
        description="Seconds the check may run before it is killed, which counts as failing.",
    )


# What a check holds the run or, in a campaign's session, every later session to: written as a lasting note is, so once
# the run has read untrusted content the policy asks first (the context does not say whether the run is a session).
_HOLDS_LATER_WORK: tuple[Flag, ...] = ("persistence",)


async def _add_to_campaign(campaign: CampaignRecord, check: ProofCheck, ctx: ToolContext) -> ToolResult:
    try:
        fresh: tuple[ProofCheck, ...] = added(campaign.proofs, (check,))
    except ValueError as exc:
        return fail(f"{exc}. A check cannot be removed; claim with the ones set.")
    if not fresh:
        return ok(f"The campaign already has this check: {check.command}")
    await ctx.runtime.store.add_campaign_proof(campaign.id, check)
    await ctx.runtime.store.add_campaign_entry(
        campaign.id, ctx.run_id, "note", f"A proof check was added: {check.command} (limit {check.timeout_s} s)", {}
    )
    return ok(
        f"Added to the campaign's proof: `{check.command}`. Every claim of its outcome runs it first, and is refused "
        "unless it exits 0."
    )


async def _add_to_run(check: ProofCheck, ctx: ToolContext) -> ToolResult:
    held: tuple[ProofCheck, ...] = await run_checks(ctx.runtime.store, ctx.run_id)
    try:
        fresh: tuple[ProofCheck, ...] = added(held, (check,))
    except ValueError as exc:
        return fail(f"{exc}. A check cannot be removed; finish with the ones set.")
    if not fresh:
        return ok(f"The run already has this check: {check.command}")
    await ctx.runtime.bus.emit(ctx.run_id, PROOF_EVENT, proof_event_data(check), True)
    return ok(
        f"Added to the run's proof: `{check.command}`. When you finish with success, Dot runs every check first; "
        "the run is done only when each exits 0, and a failing one sends you back with its output."
    )


def build_proof_tool() -> RegisteredTool:
    async def run(args: AddProofArgs, ctx: ToolContext) -> ToolResult:
        record: RunRecord = await ctx.runtime.store.get_run(ctx.run_id)
        refused: str | None = proof_refused(record.mode, parse_model_ref(record.model).provider)
        if refused is not None:
            return fail(f"Not added: {refused}.")
        check: ProofCheck = ProofCheck(command=args.command, timeout_s=args.timeout_s, by="run")
        too_long: str | None = limit_refused((check,), ctx.runtime.config.shell.max_timeout_s)
        if too_long is not None:
            return fail(f"Not added: {too_long}.")
        if record.campaign_id is not None:
            return await _add_to_campaign(await ctx.runtime.store.get_campaign(record.campaign_id), check, ctx)
        return await _add_to_run(check, ctx)

    def classify(args: AddProofArgs, ctx: ToolContext) -> ActionInfo:
        return ActionInfo(
            tool=ADD_PROOF_TOOL,
            kind="meta",
            risk="safe",
            summary=f"Add a proof check: {truncate_middle(' '.join(args.command.split()), 200)}",
            target="proof",
            flags=_HOLDS_LATER_WORK,
        )

    return make_tool(
        name=ADD_PROOF_TOOL,
        description=(
            "Add a proof check: a command that must exit 0 before the run (or, in a campaign's session, the campaign) "
            "counts as done, such as the tests of what you changed. Dot runs every check when you finish with success "
            f"and sends you back with the output of one that fails. At most {MAX_PROOF_CHECKS}; a check cannot be "
            "changed or removed. Add one when the owner names a check that must pass, or when the goal has a "
            "command that proves it."
        ),
        args_model=AddProofArgs,
        modes=frozenset({"task"}),
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )
