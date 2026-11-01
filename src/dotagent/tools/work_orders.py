"""The work-order tools (item 907): ``assign_work`` records a piece of work and starts an agent on it without waiting,
``work_orders`` lists what was handed out and where it stands, ``review_work`` judges what came back. They are offered
as ``message_agent`` is, to the run that leads (Dot's own, or an agent's own chat), and ask the policy for what it
asks: a delegation. The orders and their rules are :mod:`dotagent.agent.orders`."""

import asyncio
import contextlib
import secrets
import time
from typing import Annotated, Literal

from pydantic import Field

from dotagent.agent.orders import (
    ACCEPTANCE_CHARS,
    BRIEF_CHARS,
    NOTES_CHARS,
    TITLE_CHARS,
    Verdict,
    fenced,
)
from dotagent.core.errors import NotFoundError, ToolExecutionError, ToolInputError
from dotagent.core.text import redact_known, truncate_tail
from dotagent.policy.types import ActionInfo
from dotagent.proactive.text import one_line
from dotagent.secrets import known_secrets
from dotagent.store.db import Store
from dotagent.store.models import (
    OPEN_WORK_ORDERS,
    AgentRecord,
    ObjectiveRecord,
    RunMode,
    RunRecord,
    WorkOrderRecord,
    WorkOrderStatus,
)
from dotagent.tools.base import (
    ALL_MODES,
    RegisteredTool,
    ToolArgs,
    ToolResult,
    at_most,
    make_tool,
    mark_untrusted,
    ok,
)
from dotagent.tools.context import Spawner, ToolContext
from dotagent.tools.delegate import default_child_mode
from dotagent.tools.objectives import find_objective
from dotagent.tools.team import find_agent

ASSIGN_WORK_TOOL: str = "assign_work"
WORK_ORDERS_TOOL: str = "work_orders"
REVIEW_WORK_TOOL: str = "review_work"
# The three, which only the run that leads has, and which a delegated run never does.
WORK_ORDER_TOOLS: tuple[str, ...] = (ASSIGN_WORK_TOOL, WORK_ORDERS_TOOL, REVIEW_WORK_TOOL)
_START_TIMEOUT_S: float = 120.0
_READ_TIMEOUT_S: float = 30.0
_LISTED: int = 40
_MOST_DUE_HOURS: float = 24.0 * 30
_SUMMARY_CHARS: int = 100
_LINE_CHARS: int = 120
_ANSWER_CHARS: int = 4000
_NONCE_BYTES: int = 8


class AssignWorkArgs(ToolArgs):
    agent: str = Field(min_length=1, description="The agent's name.")
    title: str = Field(min_length=3, max_length=TITLE_CHARS)
    brief: str = Field(
        min_length=3,
        max_length=BRIEF_CHARS,
        description="The whole work with every input it needs: the agent cannot see this chat.",
    )
    acceptance: str = Field(min_length=3, max_length=ACCEPTANCE_CHARS, description="What done means: its checks.")
    due_hours: Annotated[float, at_most(_MOST_DUE_HOURS)] | None = Field(default=None, gt=0)
    objective: str | None = Field(default=None, description="The id or title of the objective it serves.")


class WorkOrdersArgs(ToolArgs):
    status: WorkOrderStatus | Literal["open", "all"] = Field(default="open")
    order: str | None = Field(default=None, description="An order's id, to read it in full with its answers.")


class ReviewWorkArgs(ToolArgs):
    order: str = Field(min_length=1, description="The work order's id.")
    verdict: Literal["accept", "return", "reassign", "escalate", "cancel"]
    notes: str = Field(
        default="",
        max_length=NOTES_CHARS,
        description="Why; for a return, what to fix: the agent cannot see this chat.",
    )
    to_agent: str | None = Field(default=None, description="For reassign: who takes over.")


async def _leads_for(ctx: ToolContext) -> tuple[RunRecord, Spawner]:
    """The run that leads the work, as the store has it, and what starts runs for it; ``ToolExecutionError`` for a run
    that cannot hand out work (a ``dotx`` command, whose runs end with the process and could not follow an attempt)."""
    spawner: Spawner | None = ctx.spawner
    if spawner is None or not spawner.answers_reach_runs:
        raise ToolExecutionError(
            "work orders need Dot's daemon, which follows the agents' runs; this run cannot hand any out",
            {"run_id": ctx.run_id},
        )
    return await ctx.runtime.store.get_run(ctx.run_id), spawner


def _wanted(status: WorkOrderStatus | Literal["open", "all"]) -> tuple[WorkOrderStatus, ...] | None:
    """The statuses ``work_orders`` lists for what it was asked: the open ones, every one (``None``), or one."""
    if status == "open":
        return OPEN_WORK_ORDERS
    if status == "all":
        return None
    return (status,)


def _age(seconds: float) -> str:
    """A span of time in the biggest unit that says it: minutes, hours or days."""
    if seconds < 3600:
        return f"{max(int(seconds // 60), 0)} min"
    if seconds < 86_400:
        return f"{int(seconds // 3600)} h"
    return f"{int(seconds // 86_400)} d"


async def _assigner_agent(store: Store, order: WorkOrderRecord) -> str | None:
    """The agent whose run handed the order out, ``None`` for Dot's."""
    if order.run_id is None:
        return None
    try:
        return (await store.get_run(order.run_id)).agent_id
    except NotFoundError:
        return None


async def _own_order(ctx: ToolContext, text: str) -> WorkOrderRecord:
    """The order named ``text`` that this lead may work on: Dot's runs see them all, an agent's own chat those its own
    runs handed out. ``ToolInputError`` with the open ones when there is none."""
    store: Store = ctx.runtime.store
    found: WorkOrderRecord | None = None
    with contextlib.suppress(NotFoundError):
        found = await store.get_work_order(text.strip())
    if found is None or (ctx.agent_id is not None and await _assigner_agent(store, found) != ctx.agent_id):
        known: str = ", ".join(each.id for each in await _visible(ctx, OPEN_WORK_ORDERS)) or "none open"
        raise ToolInputError("no work order of yours has that id", {"order": text.strip(), "open": known})
    return found


async def _visible(ctx: ToolContext, statuses: tuple[WorkOrderStatus, ...] | None) -> list[WorkOrderRecord]:
    store: Store = ctx.runtime.store
    orders: list[WorkOrderRecord] = await store.list_work_orders(statuses, _LISTED)
    if ctx.agent_id is None:
        return orders
    return [order for order in orders if await _assigner_agent(store, order) == ctx.agent_id]


def _assign_tool() -> RegisteredTool:
    def classify(args: AssignWorkArgs, ctx: ToolContext) -> ActionInfo:
        shown: str = truncate_tail(" ".join(args.title.split()), _SUMMARY_CHARS)
        return ActionInfo(
            tool=ASSIGN_WORK_TOOL,
            kind="delegate",
            risk="safe",
            summary=f"Hand {args.agent.strip()} a work order: {shown}",
            target="",
            flags=(),
        )

    async def run(args: AssignWorkArgs, ctx: ToolContext) -> ToolResult:
        lead, spawner = await _leads_for(ctx)
        store: Store = ctx.runtime.store
        agents: list[AgentRecord] = await store.list_agents()
        agent: AgentRecord | None = find_agent(agents, args.agent)
        if agent is None:
            raise ToolInputError(
                "no agent has that name", {"agent": args.agent, "team": ", ".join(found.name for found in agents)}
            )
        if lead.agent_id == agent.id:
            raise ToolInputError("an agent does not hand work to itself", {"agent": agent.name})
        objective_id: str | None = None
        if args.objective is not None and args.objective.strip():
            objective: ObjectiveRecord | None = find_objective(
                await store.list_objectives(("active",), _LISTED), args.objective
            )
            if objective is None:
                raise ToolInputError(
                    "no active objective has that id or title; see objectives", {"objective": args.objective}
                )
            objective_id = objective.id
        held: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
        mode: RunMode = default_child_mode(ctx.mode)
        order: WorkOrderRecord = await spawner.desk.assign(
            lead,
            mode,
            agent,
            redact_known(" ".join(args.title.split()), held),
            redact_known(args.brief.strip(), held),
            redact_known(args.acceptance.strip(), held),
            args.due_hours,
            objective_id,
            ctx.ever_tainted(),
        )
        due: str = "" if order.due_at is None else f", due in {_age(order.due_at - time.time())}"
        return ok(
            f"Work order {order.id} handed to {agent.name}{due}; its run is at work now and nobody waits for it. You "
            "are told in a note when it ends: then judge it against what you said done means with review_work. "
            "work_orders shows where each stands."
        )

    return make_tool(
        name=ASSIGN_WORK_TOOL,
        description=(
            "Hand a piece of work to an agent as a recorded work order and go on: its run starts at once, you do not "
            "wait, and you are told when it ends. Then judge it with review_work."
        ),
        args_model=AssignWorkArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_START_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def _order_line(order: WorkOrderRecord, names: dict[str, str], now: float) -> str:
    agent: str = names.get(order.agent_id, "an agent that is gone")
    due: str = ""
    if order.due_at is not None and order.status in OPEN_WORK_ORDERS:
        due = f", due in {_age(order.due_at - now)}" if order.due_at > now else f", {_age(now - order.due_at)} overdue"
    waiting: str = ", waits for your review" if order.status == "in_review" else ""
    note: str = f" - {one_line(order.note, _LINE_CHARS)}" if order.note else ""
    return (
        f"- {order.id} [{order.status}] {one_line(order.title, _LINE_CHARS)} - {agent}, attempt {order.attempts}, "
        f"handed out {_age(now - order.created_at)} ago{due}{waiting}{note}"
    )


async def _attempts(store: Store, order: WorkOrderRecord) -> str:
    lines: list[str] = []
    for number, run_id in enumerate(order.run_ids, start=1):
        try:
            run: RunRecord = await store.get_run(run_id)
        except NotFoundError:
            lines.append(f"Attempt {number} (run {run_id}): the run is gone.")
            continue
        said: str = truncate_tail(run.result.strip() or "(it replied nothing)", _ANSWER_CHARS)
        lines.append(f"Attempt {number} (run {run.id}) {run.status}, {run.steps} steps:\n{said}")
    return "\n\n".join(lines) or "No attempt has started."


def _orders_tool() -> RegisteredTool:
    def classify(args: WorkOrdersArgs, ctx: ToolContext) -> ActionInfo:
        return ActionInfo(
            tool=WORK_ORDERS_TOOL, kind="read", risk="safe", summary="See the work orders", target="", flags=()
        )

    async def run(args: WorkOrdersArgs, ctx: ToolContext) -> ToolResult:
        store: Store = ctx.runtime.store
        names: dict[str, str] = {agent.id: agent.name for agent in await store.list_agents()}
        now: float = time.time()
        if args.order is not None and args.order.strip():
            order: WorkOrderRecord = await _own_order(ctx, args.order)
            text: str = "\n\n".join(
                [
                    _order_line(order, names, now),
                    f"The work:\n{order.brief}",
                    f"Done means:\n{order.acceptance}",
                    await _attempts(store, order),
                ]
            )
            shown: list[WorkOrderRecord] = [order]
        else:
            shown = await _visible(ctx, _wanted(args.status))
            text = "\n".join(_order_line(order, names, now) for order in shown) or "No work orders."
        if not any(order.tainted for order in shown):
            return ok(text)
        # The words of an order, and the answers of its attempts, may carry what a page or a mail said to a run that
        # read it: fenced, and the reader is marked as having read outside content.
        return mark_untrusted(ok(fenced("work_orders", text, secrets.token_hex(_NONCE_BYTES))))

    return make_tool(
        name=WORK_ORDERS_TOOL,
        description="The work orders you handed out: status, agent, tries, age and due time.",
        args_model=WorkOrdersArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_READ_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def _review_tool() -> RegisteredTool:
    def classify(args: ReviewWorkArgs, ctx: ToolContext) -> ActionInfo:
        return ActionInfo(
            tool=REVIEW_WORK_TOOL,
            kind="delegate",
            risk="safe",
            summary=f"Judge work order {args.order.strip()}: {args.verdict}",
            target="",
            flags=(),
        )

    async def run(args: ReviewWorkArgs, ctx: ToolContext) -> ToolResult:
        lead, spawner = await _leads_for(ctx)
        order: WorkOrderRecord = await _own_order(ctx, args.order)
        if args.verdict in ("return", "reassign") and not args.notes.strip():
            raise ToolInputError(
                f"a {args.verdict} says what to fix in notes: the agent cannot see this chat", {"order": order.id}
            )
        to_agent: AgentRecord | None = None
        if args.to_agent is not None and args.to_agent.strip():
            agents: list[AgentRecord] = await ctx.runtime.store.list_agents()
            to_agent = find_agent(agents, args.to_agent)
            if to_agent is None:
                raise ToolInputError(
                    "no agent has that name",
                    {"agent": args.to_agent, "team": ", ".join(found.name for found in agents)},
                )
            if to_agent.id == lead.agent_id:
                raise ToolInputError("an agent does not take work from itself", {"agent": to_agent.name})
        held: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
        verdict: Verdict = args.verdict
        said: str = await spawner.desk.review(
            lead,
            default_child_mode(ctx.mode),
            order,
            verdict,
            redact_known(args.notes.strip(), held),
            to_agent,
            ctx.ever_tainted(),
        )
        # A verdict on an order whose words came of outside content makes the lead's run one that read such content, as
        # reading the order in work_orders does: the new attempt starts so, and the run that sent it back goes on so.
        after: WorkOrderRecord = await ctx.runtime.store.get_work_order(order.id)
        result: ToolResult = ok(said)
        return mark_untrusted(result) if order.tainted or after.tainted else result

    return make_tool(
        name=REVIEW_WORK_TOOL,
        description=(
            "Judge a work order that came back: accept it, return it to its agent with notes (the third return goes "
            "to the owner), reassign it, escalate it to the owner, or cancel it."
        ),
        args_model=ReviewWorkArgs,
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=_START_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def build_work_order_tools() -> tuple[RegisteredTool, ...]:
    return (_assign_tool(), _orders_tool(), _review_tool())
