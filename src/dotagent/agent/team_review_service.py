"""The manager review as the daemon runs it (item 2068): its state in the store, the team read from the store, one
review at a time, and the owner's Accept and Skip of a proposal, the one way a review changes anything.

The scheduler starts a review when one is due (:func:`dotagent.agent.team_review.review_due`); the owner can ask for
one at any time. A review that found nothing asks no model. Accepting a proposal checks it again against the team as
it is then and refuses one that no longer fits (an order that moved on, a name taken, a routine gone) with a
:class:`RunConflict`, leaving the proposal waiting for the owner to skip.
"""

import asyncio
import weakref
from collections.abc import Callable
from typing import TYPE_CHECKING

from dotagent.agent.review import TeamReviewAnswer, TeamReviewer, TeamReviewRequest, team_reviewer_for
from dotagent.agent.team_load import LOAD_WINDOW_S
from dotagent.agent.team_review import (
    MOST_PROPOSALS,
    WITHHELD,
    ReviewEvery,
    SnapshotBot,
    SnapshotObjective,
    SnapshotOrder,
    SnapshotRoutine,
    TeamFinding,
    TeamProposal,
    TeamReview,
    TeamReviewState,
    TeamSnapshot,
    team_findings,
)
from dotagent.core.errors import InvalidValueError, NotFoundError, RunConflict
from dotagent.core.ids import new_id
from dotagent.core.logs import get_logger
from dotagent.core.paths import failsafe_path
from dotagent.store.db import Store
from dotagent.store.models import (
    BOT_HELD_WORK_ORDERS,
    DOT_AGENT,
    DOT_AGENT_ID,
    AgentRecord,
    BotLoadRow,
    RunRecord,
    ScheduleRecord,
    WorkOrderRecord,
)

if TYPE_CHECKING:
    from dotagent.agent.orders import WorkDesk
    from dotagent.runtime import Runtime

log = get_logger(__name__)

TEAM_REVIEW_KEY: str = "team_review"
# What a routine a review's proposal switched off says, on its row (item 458).
RETIRED_STATUS: str = "switched off: the owner accepted the manager review's proposal"
# The note a work order moved on a proposal carries.
MOVED_NOTE: str = "moved by the owner on the manager review's proposal"
_ORDERS_READ: int = 200
_OBJECTIVES_READ: int = 100
# The sources whose runs are not a bot's work: the pre-push code reviews (``dotagent.server.REVIEW_SOURCE``).
_NOT_WORK: tuple[str, ...] = ("review",)
# One review at a time, and each change of the stored state at a time, by the runtime's store.
_REVIEWS: "weakref.WeakKeyDictionary[Store, asyncio.Lock]" = weakref.WeakKeyDictionary()
_STATES: "weakref.WeakKeyDictionary[Store, asyncio.Lock]" = weakref.WeakKeyDictionary()


def _lock(locks: "weakref.WeakKeyDictionary[Store, asyncio.Lock]", store: Store) -> asyncio.Lock:
    found: asyncio.Lock | None = locks.get(store)
    if found is None:
        found = asyncio.Lock()
        locks[store] = found
    return found


def running(store: Store) -> bool:
    """Whether a review is under way."""
    return _lock(_REVIEWS, store).locked()


async def load(store: Store) -> TeamReviewState:
    raw: str | None = await store.kv_get(TEAM_REVIEW_KEY)
    return TeamReviewState() if raw is None else TeamReviewState.model_validate_json(raw)


async def _save(store: Store, state: TeamReviewState) -> None:
    await store.kv_set(TEAM_REVIEW_KEY, state.model_dump_json())


async def _bots(store: Store) -> dict[str, AgentRecord]:
    """Dot and the owner's agents, by id."""
    return {DOT_AGENT_ID: DOT_AGENT, **{agent.id: agent for agent in await store.list_agents()}}


async def configure(store: Store, manager: str, every: ReviewEvery) -> TeamReviewState:
    """Set who reviews the team and how often; one manager at a time, so choosing a bot makes it the manager. A bot
    that is not there is refused."""
    if manager not in await _bots(store):
        raise NotFoundError("no bot has this id", {"manager": manager})
    async with _lock(_STATES, store):
        state: TeamReviewState = await load(store)
        changed: TeamReviewState = state.model_copy(update={"manager": manager, "every": every})
        await _save(store, changed)
        return changed


async def snapshot(store: Store, now: float) -> TeamSnapshot:
    """The team as the store has it at ``now``: what each bot carries, the open orders the bots hold, the active
    objectives and the routines. A title or name a run wrote after reading outside content is withheld."""
    rows: dict[str, BotLoadRow] = {
        row.agent_id: row for row in await store.bot_load_rows(now - LOAD_WINDOW_S, now - 2 * LOAD_WINDOW_S, _NOT_WORK)
    }
    agents: dict[str, AgentRecord] = await _bots(store)
    bots: tuple[SnapshotBot, ...] = tuple(
        SnapshotBot(
            id=agent.id,
            name=agent.name,
            role=agent.role,
            created_at=agent.created_at,
            paused=agent.paused_at is not None,
            row=rows[agent.id],
        )
        for agent in agents.values()
        if agent.id in rows
    )
    orders: list[WorkOrderRecord] = await store.list_work_orders(BOT_HELD_WORK_ORDERS, _ORDERS_READ)
    schedules: list[ScheduleRecord] = await store.list_schedules()
    return TeamSnapshot(
        now=now,
        bots=bots,
        orders=tuple(
            SnapshotOrder(
                id=order.id,
                agent_id=order.agent_id,
                title=WITHHELD if order.tainted else order.title,
                status=order.status,
                attempts=order.attempts,
                updated_at=order.updated_at,
                due_at=order.due_at,
            )
            for order in orders
        ),
        objectives=tuple(
            SnapshotObjective(
                id=objective.id,
                title=WITHHELD if objective.tainted else objective.title,
                updated_at=objective.updated_at,
            )
            for objective in await store.list_objectives(("active",), _OBJECTIVES_READ)
        ),
        routines=tuple(
            SnapshotRoutine(
                id=schedule.id,
                name=WITHHELD if schedule.options.from_outside else schedule.name,
                agent_id=schedule.options.agent_id or DOT_AGENT_ID,
                enabled=schedule.enabled,
                cron=schedule.cron,
            )
            for schedule in schedules
        ),
    )


def reviewer_of(runtime: "Runtime", manager: AgentRecord, now: float) -> TeamReviewer:
    """The manager's model (its own, or the review model for one that has none, Dot's included), and the models after
    the review model when it declines."""
    return team_reviewer_for(
        runtime.router,
        manager.model or runtime.roles.review(),
        runtime.roles.after_review(),
        runtime.config.agent.model_call_timeout_s,
        f"team_review:{int(now)}",
    )


async def review(
    runtime: "Runtime", now: float, reviewer_for: Callable[[AgentRecord], TeamReviewer]
) -> TeamReviewState:
    """One review, stored once it ends: the findings, and the manager's summary and proposals when it found anything
    (``reviewer_for`` gives the manager's model, :func:`reviewer_of` in the daemon). Its try is recorded first, so a
    review that fails is not tried again before its period; the failure is stored as the state's ``error`` and raised.
    One review at a time; refused while the emergency stop is engaged."""
    store: Store = runtime.store
    if await asyncio.to_thread(failsafe_path().exists):
        raise RunConflict("the emergency stop is engaged: the team is not reviewed until it is released", {})
    lock: asyncio.Lock = _lock(_REVIEWS, store)
    if lock.locked():
        raise RunConflict("a review of the team is under way", {})
    async with lock:
        async with _lock(_STATES, store):
            state: TeamReviewState = (await load(store)).model_copy(update={"last_at": now})
            await _save(store, state)
        bots: dict[str, AgentRecord] = await _bots(store)
        manager: AgentRecord = bots.get(state.manager, DOT_AGENT)
        team: TeamSnapshot = await snapshot(store, now)
        findings: tuple[TeamFinding, ...] = team_findings(team)
        try:
            answer: TeamReviewAnswer = (
                TeamReviewAnswer(summary="", proposals=())
                if not findings
                else await reviewer_for(manager)(
                    TeamReviewRequest(
                        manager=f"{manager.name}, {manager.role}" if manager.role else manager.name,
                        snapshot=team,
                        findings=findings,
                        ids=tuple(new_id("tp") for _ in range(MOST_PROPOSALS)),
                    )
                )
            )
        except Exception as error:
            async with _lock(_STATES, store):
                await _save(store, (await load(store)).model_copy(update={"error": str(error)[:500]}))
            raise
        done: TeamReview = TeamReview(
            at=now,
            manager_id=manager.id,
            manager_name=manager.name,
            findings=findings,
            summary=answer.summary,
            proposals=answer.proposals,
        )
        async with _lock(_STATES, store):
            stored: TeamReviewState = (await load(store)).model_copy(update={"review": done, "error": ""})
            await _save(store, stored)
        log.info("team_reviewed", findings=len(findings), proposals=len(answer.proposals), manager=manager.id)
        return stored


def _waiting(state: TeamReviewState, proposal_id: str) -> TeamProposal:
    proposals: tuple[TeamProposal, ...] = () if state.review is None else state.review.proposals
    for proposal in proposals:
        if proposal.id == proposal_id:
            if proposal.status != "waiting":
                raise RunConflict(f"the proposal was {proposal.status} already", {"proposal": proposal_id})
            return proposal
    raise NotFoundError("no proposal of the newest review has this id", {"proposal": proposal_id})


async def _decided(store: Store, proposal_id: str, accepted: bool, now: float) -> TeamReviewState:
    state: TeamReviewState = await load(store)
    assert state.review is not None
    proposals: tuple[TeamProposal, ...] = tuple(
        proposal.model_copy(update={"status": "accepted" if accepted else "skipped", "decided_at": now})
        if proposal.id == proposal_id
        else proposal
        for proposal in state.review.proposals
    )
    changed: TeamReviewState = state.model_copy(
        update={"review": state.review.model_copy(update={"proposals": proposals})}
    )
    await _save(store, changed)
    return changed


async def skip(store: Store, proposal_id: str, now: float) -> TeamReviewState:
    """The owner skips a proposal: nothing changes but its card."""
    async with _lock(_STATES, store):
        _waiting(await load(store), proposal_id)
        return await _decided(store, proposal_id, False, now)


async def accept(store: Store, desk: "WorkDesk", proposal_id: str, now: float) -> TeamReviewState:
    """The owner accepts a proposal: its change is made, as the owner's own, and its card says so. A proposal that no
    longer fits the team is refused (:class:`RunConflict`) and left waiting."""
    async with _lock(_STATES, store):
        proposal: TeamProposal = _waiting(await load(store), proposal_id)
        if proposal.kind == "move_orders":
            await _move_orders(store, desk, proposal)
        elif proposal.kind == "split_bot":
            await _split_bot(store, proposal)
        else:
            schedule: ScheduleRecord = await store.get_schedule(proposal.routine_id)
            if not schedule.enabled:
                raise RunConflict("the routine is off already", {"routine": proposal.routine_id})
            await store.switch_off_schedule(proposal.routine_id, RETIRED_STATUS)
        log.info("team_proposal_accepted", proposal=proposal_id, kind=proposal.kind)
        return await _decided(store, proposal_id, True, now)


async def _move_orders(store: Store, desk: "WorkDesk", proposal: TeamProposal) -> None:
    """Each order goes to the proposal's bot as the lead's ``reassign`` sends it, as an attempt of the run that handed
    it out. Every order must still be the first bot's and wait for review, or none moves."""
    target: AgentRecord = await store.get_agent(proposal.to_agent)
    orders: list[tuple[WorkOrderRecord, RunRecord]] = []
    for order_id in proposal.order_ids:
        order: WorkOrderRecord = await store.get_work_order(order_id)
        if order.agent_id != proposal.agent_id or order.status != "in_review" or order.lead_run_id is None:
            raise RunConflict(
                "only an order that is still the bot's and waits for review moves: this one moved on",
                {"order_id": order.id, "status": order.status},
            )
        orders.append((order, await store.get_run(order.lead_run_id)))
    for order, lead in orders:
        await desk.review(lead, "task", order, "reassign", MOVED_NOTE, "", target, order.tainted)


async def _split_bot(store: Store, proposal: TeamProposal) -> None:
    """The new bots, each with the split bot's instructions, bounds and edges, its own name and role, and the routines
    the proposal gave it; the split bot stays, with what is left."""
    source: AgentRecord = await store.get_agent(proposal.agent_id)
    taken: set[str] = {agent.name.strip().casefold() for agent in await store.list_agents()} | {"dot"}
    schedules: dict[str, ScheduleRecord] = {schedule.id: schedule for schedule in await store.list_schedules()}
    for part in proposal.parts:
        if part.name.casefold() in taken:
            raise InvalidValueError("a bot has this name now", {"name": part.name})
        for routine_id in part.routine_ids:
            schedule: ScheduleRecord | None = schedules.get(routine_id)
            if schedule is None or schedule.options.agent_id != source.id:
                raise RunConflict("the routine is not the bot's any more", {"routine": routine_id})
    for part in proposal.parts:
        made: AgentRecord = await store.create_agent(
            part.name,
            part.role,
            source.instructions,
            source.model,
            source.effort,
            source.approvals,
            source.tools,
            None,
            None,
            source.refuses,
        )
        for routine_id in part.routine_ids:
            moved: ScheduleRecord = schedules[routine_id]
            await store.update_schedule(
                moved.id,
                moved.name,
                moved.cron,
                moved.run_at,
                moved.goal,
                moved.mode,
                moved.options.model_copy(update={"agent_id": made.id}),
                moved.enabled,
                moved.next_run_at,
            )
