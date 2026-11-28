"""The manager review of the team (item 2068): what it finds with no model, and the proposals it may make.

The owner's ask of 2026-11-28, after Grok Bot's note that a manager bot should oversee the specialised ones. A review
looks at the team as the store has it (:class:`TeamSnapshot`) and finds, with no model (:func:`team_findings`): work
orders that are stuck, bots that sit idle, objectives with no progress, bots that are overloaded (item 2067) and bots
whose runs fail half the time. The manager (Dot, or the bot the owner chose) then proposes a change for the findings a
change helps, through one call of its model with no tool but its answer's (:mod:`dotagent.agent.review`); this module
checks each proposal against the snapshot (:func:`checked_proposals`), so that one naming an order, a bot or a routine
that is not there is asked again rather than shown. A proposal is a card the owner accepts or skips: nothing is changed
until they say yes (:mod:`dotagent.agent.team_review_service`).

Three kinds of change: move work orders to another bot (as the lead's ``reassign`` does), split a bot into two or three
new ones that take its routines, and switch a routine off.
"""

from collections.abc import Sequence
from typing import Literal

from pydantic import Field

from dotagent.agent.team_load import BotLoad, OverloadReason, bot_load
from dotagent.core.errors import ProviderProtocolError
from dotagent.core.types import FrozenModel
from dotagent.store.models import DOT_AGENT_ID, BotLoadRow

ReviewEvery = Literal["off", "daily", "weekly"]
FindingKind = Literal["stuck_order", "idle_bot", "objective_behind", "overloaded", "failing"]
ProposalKind = Literal["move_orders", "split_bot", "retire_routine"]
ProposalStatus = Literal["waiting", "accepted", "skipped"]

# How long between two reviews.
REVIEW_PERIODS: dict[ReviewEvery, float] = {"daily": 86400.0, "weekly": 7 * 86400.0}
# An open order whose due time passed, or that has not moved for this long, is stuck.
STUCK_S: float = 2 * 86400.0
# A bot older than this that ran nothing in it, holds no order and has no routine on is idle.
IDLE_S: float = 7 * 86400.0
# An active objective with no progress for this long is behind.
BEHIND_S: float = 7 * 86400.0
# A bot whose runs of the week failed half the time or more, over at least this many runs, is failing.
FAILING_RUNS: int = 3
# The most findings one review shows and the manager is asked about, and the most proposals it may make.
MOST_FINDINGS: int = 20
MOST_PROPOSALS: int = 8
MOST_ORDERS_MOVED: int = 10
# A split makes two or three bots.
FEWEST_PARTS: int = 2
MOST_PARTS: int = 3
NAME_CHARS: int = 40
# What a title read from outside content is shown as: counted, not quoted (as the brief does, item 907).
WITHHELD: str = ""


class SnapshotBot(FrozenModel):
    """A bot as the review reads it: Dot (``id`` Dot's) or one of the owner's agents, with what it carries."""

    id: str
    name: str
    role: str
    created_at: float
    paused: bool
    row: BotLoadRow


class SnapshotOrder(FrozenModel):
    """An open work order a bot holds; ``title`` is :data:`WITHHELD` for one that read outside content."""

    id: str
    agent_id: str
    title: str
    status: str
    attempts: int
    updated_at: float
    due_at: float | None


class SnapshotObjective(FrozenModel):
    """An active objective; ``title`` is :data:`WITHHELD` for one a run wrote after reading outside content."""

    id: str
    title: str
    updated_at: float


class SnapshotRoutine(FrozenModel):
    """A routine (a schedule) and the bot it runs as (Dot's id for Dot); ``name`` is :data:`WITHHELD` for one a run
    made after reading outside content."""

    id: str
    name: str
    agent_id: str
    enabled: bool
    cron: str | None


class TeamSnapshot(FrozenModel):
    """The team at ``now``: every bot, Dot first, the open orders the bots hold, the active objectives, the routines."""

    now: float
    bots: tuple[SnapshotBot, ...]
    orders: tuple[SnapshotOrder, ...]
    objectives: tuple[SnapshotObjective, ...]
    routines: tuple[SnapshotRoutine, ...]


class TeamFinding(FrozenModel):
    """One thing the review found. ``agent_id`` and ``agent_name`` are the bot it is about (the order's for a stuck
    order; "" for an objective), ``subject_id`` and ``subject`` the order or objective (its title, "" when withheld).
    ``count`` and ``limit`` by kind: a stuck order's, an idle bot's and an objective's days; a failing bot's failed
    runs and its runs; 0 otherwise. ``reasons`` are an overloaded bot's."""

    kind: FindingKind
    agent_id: str
    agent_name: str
    subject_id: str
    subject: str
    count: int
    limit: int
    reasons: tuple[OverloadReason, ...]


class SplitPart(FrozenModel):
    """A bot a split makes: its name, its role, and the routines of the split bot it takes over (with their names)."""

    name: str
    role: str
    routine_ids: tuple[str, ...]
    routine_names: tuple[str, ...]


class TeamProposal(FrozenModel):
    """A change the manager proposes for one finding (``finding``, its place in the review's findings), and why.

    ``agent_id`` is the bot it changes: the one whose orders move, the one split, the one whose routine goes off. A move
    names its ``order_ids`` (and their titles) and ``to_agent``; a split its ``parts``; a retirement its ``routine_id``.
    ``status`` is ``waiting`` until the owner accepts or skips it, when ``decided_at`` is set."""

    id: str
    kind: ProposalKind
    finding: int
    why: str
    agent_id: str
    agent_name: str
    order_ids: tuple[str, ...]
    order_titles: tuple[str, ...]
    to_agent: str
    to_name: str
    parts: tuple[SplitPart, ...]
    routine_id: str
    routine_name: str
    status: ProposalStatus
    decided_at: float | None


class TeamReview(FrozenModel):
    """One review: when, by which manager, what it found, the manager's summary ("" with nothing found: no model was
    asked) and its proposals."""

    at: float
    manager_id: str
    manager_name: str
    findings: tuple[TeamFinding, ...]
    summary: str
    proposals: tuple[TeamProposal, ...]


class TeamReviewState(FrozenModel):
    """The manager review as the store keeps it: the manager (a bot's id, Dot's by default), how often it reviews (off
    until the owner turns it on), when the last review was tried, the newest review and the error of the last try that
    failed ("" for none)."""

    manager: str = DOT_AGENT_ID
    every: ReviewEvery = "off"
    last_at: float | None = None
    review: TeamReview | None = None
    error: str = ""


class SplitDraft(FrozenModel):
    """A bot a split makes, as the manager's model writes it."""

    name: str = Field(description=f"The new bot's name, {NAME_CHARS} characters at most, not one any bot has.")
    role: str = Field(description="Its role in one line: the one kind of work it does.")
    routines: list[str] = Field(
        max_length=20, description="The ids of the split bot's routines this bot takes over, each in one part only."
    )


class ProposalDraft(FrozenModel):
    """A proposal as the manager's model writes it, before :func:`checked_proposals` checks it against the snapshot."""

    finding: int = Field(ge=1, le=MOST_FINDINGS, description="The number of the finding this proposal answers.")
    kind: ProposalKind = Field(
        description="move_orders: hand open work orders to another bot. split_bot: make two or three new bots that "
        "take over the bot's routines. retire_routine: switch a routine off."
    )
    why: str = Field(description="Why, in one or two sentences the owner reads on the card, in English.")
    orders: list[str] = Field(
        max_length=MOST_ORDERS_MOVED, description="move_orders: the ids of the orders to move, all of one bot. Else []."
    )
    to_bot: str = Field(description='move_orders: the id of the bot that takes them (not Dot). Else "".')
    bot: str = Field(description='split_bot: the id of the bot to split (not Dot). Else "".')
    parts: list[SplitDraft] = Field(
        max_length=MOST_PARTS, description=f"split_bot: the {FEWEST_PARTS} or {MOST_PARTS} new bots. Else []."
    )
    routine: str = Field(description='retire_routine: the id of the routine to switch off. Else "".')


class TeamReviewDraft(FrozenModel):
    """The manager's answer: a summary of the team for the owner and its proposals."""

    summary: str = Field(description="How the team stands, in two or three sentences for the owner, in English.")
    proposals: list[ProposalDraft] = Field(
        max_length=MOST_PROPOSALS,
        description="At most one proposal per finding, for the findings a change helps; [] when none does.",
    )


def review_due(state: TeamReviewState, now: float) -> bool:
    """Whether a review is due: it is on, and none was tried in its period."""
    if state.every == "off":
        return False
    return state.last_at is None or now - state.last_at >= REVIEW_PERIODS[state.every]


def _days(seconds: float) -> int:
    return max(0, int(seconds // 86400.0))


def loads(snapshot: TeamSnapshot) -> dict[str, BotLoad]:
    return {bot.id: bot_load(bot.row) for bot in snapshot.bots}


def team_findings(snapshot: TeamSnapshot) -> tuple[TeamFinding, ...]:
    """What the review finds in ``snapshot``, with no model: stuck orders first, then overloaded and failing bots,
    objectives behind and idle bots, at most :data:`MOST_FINDINGS`."""
    now: float = snapshot.now
    names: dict[str, str] = {bot.id: bot.name for bot in snapshot.bots}
    found: list[TeamFinding] = []
    for order in snapshot.orders:
        late: bool = order.due_at is not None and order.due_at < now
        if late or now - order.updated_at >= STUCK_S:
            found.append(
                TeamFinding(
                    kind="stuck_order",
                    agent_id=order.agent_id,
                    agent_name=names.get(order.agent_id, ""),
                    subject_id=order.id,
                    subject=order.title,
                    count=_days(now - order.updated_at),
                    limit=0,
                    reasons=(),
                )
            )
    by_load: dict[str, BotLoad] = loads(snapshot)
    for bot in snapshot.bots:
        reasons: tuple[OverloadReason, ...] = by_load[bot.id].reasons
        if reasons:
            found.append(_about(bot, "overloaded", 0, 0, reasons))
    for bot in snapshot.bots:
        row: BotLoadRow = bot.row
        if row.runs >= FAILING_RUNS and 2 * row.failed >= row.runs:
            found.append(_about(bot, "failing", row.failed, row.runs, ()))
    for objective in snapshot.objectives:
        if now - objective.updated_at >= BEHIND_S:
            found.append(
                TeamFinding(
                    kind="objective_behind",
                    agent_id="",
                    agent_name="",
                    subject_id=objective.id,
                    subject=objective.title,
                    count=_days(now - objective.updated_at),
                    limit=0,
                    reasons=(),
                )
            )
    for bot in snapshot.bots:
        if _idle(bot, now):
            since: float = bot.row.last_run_at if bot.row.last_run_at is not None else bot.created_at
            found.append(_about(bot, "idle_bot", _days(now - since), 0, ()))
    return tuple(found[:MOST_FINDINGS])


def _about(
    bot: SnapshotBot, kind: FindingKind, count: int, limit: int, reasons: tuple[OverloadReason, ...]
) -> TeamFinding:
    return TeamFinding(
        kind=kind,
        agent_id=bot.id,
        agent_name=bot.name,
        subject_id="",
        subject="",
        count=count,
        limit=limit,
        reasons=reasons,
    )


def _idle(bot: SnapshotBot, now: float) -> bool:
    """A bot of the owner's, not paused, older than :data:`IDLE_S`, that ran nothing in it, holds no open order and has
    no routine on: Dot, the lead, is never idle."""
    row: BotLoadRow = bot.row
    if bot.id == DOT_AGENT_ID or bot.paused or now - bot.created_at < IDLE_S:
        return False
    ran: bool = row.last_run_at is not None and now - row.last_run_at < IDLE_S
    return not ran and row.open_orders == 0 and row.active_routines == 0


def _refused(why: str, number: int, value: str) -> ProviderProtocolError:
    return ProviderProtocolError(f"proposal {number}: {why}", {"proposal": number, "value": value})


def _proposal(
    proposal_id: str,
    kind: ProposalKind,
    finding: int,
    why: str,
    agent: tuple[str, str],
    orders: tuple[tuple[str, ...], tuple[str, ...]],
    target: tuple[str, str],
    parts: tuple[SplitPart, ...],
    routine: tuple[str, str],
) -> TeamProposal:
    return TeamProposal(
        id=proposal_id,
        kind=kind,
        finding=finding,
        why=why,
        agent_id=agent[0],
        agent_name=agent[1],
        order_ids=orders[0],
        order_titles=orders[1],
        to_agent=target[0],
        to_name=target[1],
        parts=parts,
        routine_id=routine[0],
        routine_name=routine[1],
        status="waiting",
        decided_at=None,
    )


def checked_proposals(
    drafts: Sequence[ProposalDraft], snapshot: TeamSnapshot, findings: Sequence[TeamFinding], ids: Sequence[str]
) -> tuple[TeamProposal, ...]:
    """The proposals of ``drafts``, each checked against ``snapshot`` and given its id from ``ids`` (in order). Raises a
    :class:`ProviderProtocolError` naming the proposal and what is wrong with it, so that the model is asked again:
    a finding that is not one, an order that is not open or not one bot's, a bot or routine that is not there, a split
    of Dot or into names taken, a routine named twice or not the bot's, a routine already off."""
    if len(drafts) > MOST_PROPOSALS:
        raise ProviderProtocolError(f"at most {MOST_PROPOSALS} proposals", {"proposals": len(drafts)})
    bots: dict[str, SnapshotBot] = {bot.id: bot for bot in snapshot.bots}
    orders: dict[str, SnapshotOrder] = {order.id: order for order in snapshot.orders}
    routines: dict[str, SnapshotRoutine] = {routine.id: routine for routine in snapshot.routines}
    taken: set[str] = {bot.name.strip().casefold() for bot in snapshot.bots}
    kept: list[TeamProposal] = []
    for number, (draft, proposal_id) in enumerate(zip(drafts, ids, strict=True), start=1):
        if not 1 <= draft.finding <= len(findings):
            raise _refused(f"finding must be a number from 1 to {len(findings)}", number, str(draft.finding))
        why: str = " ".join(draft.why.split())
        if not why:
            raise _refused("say why in a sentence", number, "")
        if draft.kind == "move_orders":
            moved: tuple[SnapshotBot, SnapshotBot, tuple[SnapshotOrder, ...]] = _move(draft, number, bots, orders)
            kept.append(
                _proposal(
                    proposal_id,
                    "move_orders",
                    draft.finding - 1,
                    why,
                    (moved[0].id, moved[0].name),
                    (tuple(order.id for order in moved[2]), tuple(order.title for order in moved[2])),
                    (moved[1].id, moved[1].name),
                    (),
                    ("", ""),
                )
            )
        elif draft.kind == "split_bot":
            split: tuple[SnapshotBot, tuple[SplitPart, ...]] = _split(draft, number, bots, routines, taken)
            taken.update(part.name.casefold() for part in split[1])
            kept.append(
                _proposal(
                    proposal_id,
                    "split_bot",
                    draft.finding - 1,
                    why,
                    (split[0].id, split[0].name),
                    ((), ()),
                    ("", ""),
                    split[1],
                    ("", ""),
                )
            )
        else:
            routine: SnapshotRoutine | None = routines.get(draft.routine)
            if routine is None or not routine.enabled:
                raise _refused("routine must be the id of a routine that is on", number, draft.routine)
            owner: SnapshotBot | None = bots.get(routine.agent_id)
            kept.append(
                _proposal(
                    proposal_id,
                    "retire_routine",
                    draft.finding - 1,
                    why,
                    (routine.agent_id, "" if owner is None else owner.name),
                    ((), ()),
                    ("", ""),
                    (),
                    (routine.id, routine.name),
                )
            )
    return tuple(kept)


def _move(
    draft: ProposalDraft, number: int, bots: dict[str, SnapshotBot], orders: dict[str, SnapshotOrder]
) -> tuple[SnapshotBot, SnapshotBot, tuple[SnapshotOrder, ...]]:
    """The bot that holds the orders of a move, the bot that takes them and the orders."""
    picked: list[SnapshotOrder] = []
    for order_id in dict.fromkeys(draft.orders):
        order: SnapshotOrder | None = orders.get(order_id)
        if order is None:
            raise _refused("orders must be ids of open work orders", number, order_id)
        picked.append(order)
    if not picked or len(picked) > MOST_ORDERS_MOVED:
        raise _refused(f"name 1 to {MOST_ORDERS_MOVED} orders", number, str(len(picked)))
    holders: set[str] = {order.agent_id for order in picked}
    if len(holders) != 1:
        raise _refused("the orders of one move must all be one bot's", number, ", ".join(sorted(holders)))
    holder: SnapshotBot | None = bots.get(holders.pop())
    if holder is None:
        raise _refused("the orders' bot is gone", number, picked[0].agent_id)
    target: SnapshotBot | None = bots.get(draft.to_bot)
    if target is None or target.id == DOT_AGENT_ID or target.id == holder.id:
        raise _refused("to_bot must be the id of another bot of the owner's (not Dot)", number, draft.to_bot)
    return holder, target, tuple(picked)


def _split(
    draft: ProposalDraft,
    number: int,
    bots: dict[str, SnapshotBot],
    routines: dict[str, SnapshotRoutine],
    taken: set[str],
) -> tuple[SnapshotBot, tuple[SplitPart, ...]]:
    """The bot a split splits and the bots it makes."""
    bot: SnapshotBot | None = bots.get(draft.bot)
    if bot is None or bot.id == DOT_AGENT_ID:
        raise _refused("bot must be the id of a bot of the owner's (Dot is not split)", number, draft.bot)
    if not FEWEST_PARTS <= len(draft.parts) <= MOST_PARTS:
        raise _refused(f"a split makes {FEWEST_PARTS} or {MOST_PARTS} bots", number, str(len(draft.parts)))
    seen_names: set[str] = set()
    seen_routines: set[str] = set()
    parts: list[SplitPart] = []
    for part in draft.parts:
        clean: str = " ".join(part.name.split())
        key: str = clean.casefold()
        if not clean or len(clean) > NAME_CHARS or key in taken or key in seen_names or key == "dot":
            raise _refused(f"each new bot needs a name of its own, {NAME_CHARS} characters at most", number, clean)
        role: str = " ".join(part.role.split())
        if not role:
            raise _refused("each new bot needs a role", number, clean)
        seen_names.add(key)
        names: list[str] = []
        for routine_id in part.routines:
            routine: SnapshotRoutine | None = routines.get(routine_id)
            if routine is None or routine.agent_id != bot.id or routine_id in seen_routines:
                raise _refused("routines must be ids of the split bot's routines, each once", number, routine_id)
            seen_routines.add(routine_id)
            names.append(routine.name)
        parts.append(SplitPart(name=clean, role=role, routine_ids=tuple(part.routines), routine_names=tuple(names)))
    return bot, tuple(parts)


def _percent(share: float | None) -> str:
    return "-" if share is None else f"{round(share * 100)}%"


def finding_line(finding: TeamFinding) -> str:
    """A finding in English, as the manager's model reads it (ids in brackets, a withheld title said so)."""
    subject: str = finding.subject if finding.subject != WITHHELD else "(its title read outside content; not shown)"
    if finding.kind == "stuck_order":
        return (
            f"Work order [{finding.subject_id}] {subject!r} of {finding.agent_name} [{finding.agent_id}] has not moved "
            f"for {finding.count} days, or is past its due time."
        )
    if finding.kind == "objective_behind":
        return f"Objective [{finding.subject_id}] {subject!r} has had no progress for {finding.count} days."
    bot: str = f"{finding.agent_name} [{finding.agent_id}]"
    if finding.kind == "overloaded":
        reasons: list[str] = []
        for reason in finding.reasons:
            if reason.kind == "orders":
                reasons.append(f"{reason.count} open work orders, more than {reason.limit}")
            elif reason.kind == "routines":
                reasons.append(f"{reason.count} routines on, more than {reason.limit}")
            else:
                reasons.append(f"{reason.count}% of its runs ended done this week against {reason.limit}% before")
        return f"{bot} is overloaded: {'; '.join(reasons)}."
    if finding.kind == "failing":
        return f"{bot}: {finding.count} of its {finding.limit} runs this week failed."
    return f"{bot} ran nothing for {finding.count} days, holds no work order and has no routine on."


def team_lines(snapshot: TeamSnapshot) -> str:
    """The team as the manager's model reads it: each bot with what it carries, its routines and its open orders."""
    by_load: dict[str, BotLoad] = loads(snapshot)
    blocks: list[str] = []
    for bot in snapshot.bots:
        load: BotLoad = by_load[bot.id]
        head: str = (
            f"Bot [{bot.id}] {bot.name}{' (paused)' if bot.paused else ''}: {bot.role or 'no role'}. "
            f"Open orders {load.open_orders}, routines on {load.active_routines}, runs this week {load.runs}, "
            f"done {_percent(load.done_share)}, average steps {'-' if load.steps is None else round(load.steps)}."
        )
        lines: list[str] = [head]
        for routine in snapshot.routines:
            if routine.agent_id == bot.id:
                name: str = routine.name if routine.name != WITHHELD else "(name read outside content)"
                lines.append(
                    f"  routine [{routine.id}] {name!r}, {routine.cron or 'once'}, {'on' if routine.enabled else 'off'}"
                )
        for order in snapshot.orders:
            if order.agent_id == bot.id:
                title: str = order.title if order.title != WITHHELD else "(title read outside content)"
                lines.append(f"  order [{order.id}] {title!r}, {order.status}, {order.attempts} attempts")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)
