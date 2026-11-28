"""The manager review of the team (item 2068): what it finds with no model, the proposals it keeps, and the review as
the daemon runs it over a store, with a stand-in for the manager's model, and the owner's Accept and Skip."""

import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import pytest_asyncio

from dotagent.agent import team_review_service
from dotagent.agent.orders import WorkDesk
from dotagent.agent.review import TeamReviewAnswer, TeamReviewer, TeamReviewRequest
from dotagent.agent.team_load import MOST_OPEN_ORDERS
from dotagent.agent.team_review import (
    IDLE_S,
    STUCK_S,
    ProposalDraft,
    SnapshotBot,
    SnapshotObjective,
    SnapshotOrder,
    SnapshotRoutine,
    SplitDraft,
    TeamFinding,
    TeamReviewState,
    TeamSnapshot,
    checked_proposals,
    review_due,
    team_findings,
)
from dotagent.core.errors import NotFoundError, ProviderProtocolError, RunConflict
from dotagent.runtime import Runtime
from dotagent.store.db import Store
from dotagent.store.models import DOT_AGENT_ID, AgentRecord, BotLoadRow, ScheduleOptions

_NOW: float = 1_800_000_000.0
_DAY: float = 86400.0


def _row(agent_id: str, open_orders: int, routines: int, runs: int, failed: int, last: float | None) -> BotLoadRow:
    return BotLoadRow(
        agent_id=agent_id,
        open_orders=open_orders,
        active_routines=routines,
        routines=routines,
        objectives=0,
        runs=runs,
        done=runs - failed,
        failed=failed,
        steps=runs * 10,
        prior_runs=0,
        prior_done=0,
        last_run_at=last,
    )


def _bot(bot_id: str, name: str, row: BotLoadRow, age_days: float, paused: bool) -> SnapshotBot:
    return SnapshotBot(id=bot_id, name=name, role="role", created_at=_NOW - age_days * _DAY, paused=paused, row=row)


def _team() -> TeamSnapshot:
    return TeamSnapshot(
        now=_NOW,
        bots=(
            _bot(DOT_AGENT_ID, "Dot", _row(DOT_AGENT_ID, 0, 0, 0, 0, None), 30, False),
            _bot("seller", "Seller", _row("seller", MOST_OPEN_ORDERS + 1, 2, 10, 1, _NOW - _DAY), 30, False),
            _bot("hunter", "Hunter", _row("hunter", 0, 1, 4, 3, _NOW - _DAY), 30, False),
            _bot("sleepy", "Sleepy", _row("sleepy", 0, 0, 0, 0, _NOW - 10 * _DAY), 30, False),
            _bot("resting", "Resting", _row("resting", 0, 0, 0, 0, None), 30, True),
            _bot("newbie", "Newbie", _row("newbie", 0, 0, 0, 0, None), 2, False),
            _bot("writer", "Writer", _row("writer", 0, 0, 2, 0, _NOW - _DAY), 30, False),
        ),
        orders=(
            SnapshotOrder(
                id="wo_old",
                agent_id="seller",
                title="Call the leads",
                status="in_review",
                attempts=1,
                updated_at=_NOW - STUCK_S - 1,
                due_at=None,
            ),
            SnapshotOrder(
                id="wo_new",
                agent_id="seller",
                title="",
                status="working",
                attempts=1,
                updated_at=_NOW - 60,
                due_at=None,
            ),
        ),
        objectives=(
            SnapshotObjective(id="obj_old", title="Clients", updated_at=_NOW - 8 * _DAY),
            SnapshotObjective(id="obj_new", title="Fresh", updated_at=_NOW - _DAY),
        ),
        routines=(
            SnapshotRoutine(id="sch_a", name="Morning calls", agent_id="seller", enabled=True, cron="0 9 * * *"),
            SnapshotRoutine(id="sch_b", name="Invoices", agent_id="seller", enabled=True, cron="0 17 * * *"),
            SnapshotRoutine(id="sch_h", name="Hunt", agent_id="hunter", enabled=True, cron="0 8 * * *"),
            SnapshotRoutine(id="sch_off", name="Old", agent_id="hunter", enabled=False, cron="0 7 * * *"),
        ),
    )


def test_the_review_finds_stuck_orders_overloaded_and_failing_bots_objectives_behind_and_idle_bots() -> None:
    found: tuple[TeamFinding, ...] = team_findings(_team())
    assert [(finding.kind, finding.agent_id or finding.subject_id) for finding in found] == [
        ("stuck_order", "seller"),
        ("overloaded", "seller"),
        ("failing", "hunter"),
        ("objective_behind", "obj_old"),
        # Dot, a paused bot and a bot younger than a week are never idle; one that ran this week is not.
        ("idle_bot", "sleepy"),
    ]
    assert found[0].subject_id == "wo_old" and found[0].count == 2
    assert (found[2].count, found[2].limit) == (3, 4)
    assert found[4].count == 10
    assert IDLE_S == 7 * _DAY


def _draft(finding: int, kind: str) -> ProposalDraft:
    return ProposalDraft.model_validate(
        {
            "finding": finding,
            "kind": kind,
            "why": "Because.",
            "orders": [],
            "to_bot": "",
            "bot": "",
            "parts": [],
            "routine": "",
        }
    )


def test_proposals_that_fit_the_team_are_kept_with_their_names() -> None:
    team: TeamSnapshot = _team()
    found: tuple[TeamFinding, ...] = team_findings(team)
    move: ProposalDraft = _draft(1, "move_orders").model_copy(update={"orders": ["wo_old"], "to_bot": "writer"})
    split: ProposalDraft = _draft(2, "split_bot").model_copy(
        update={
            "bot": "seller",
            "parts": [
                SplitDraft(name="Seller-sales", role="Sells", routines=["sch_a"]),
                SplitDraft(name="Seller-delivery", role="Delivers", routines=["sch_b"]),
            ],
        }
    )
    retire: ProposalDraft = _draft(3, "retire_routine").model_copy(update={"routine": "sch_h"})
    kept = checked_proposals([move, split, retire], team, found, ["tp_1", "tp_2", "tp_3"])
    assert [(p.id, p.kind, p.finding, p.agent_name) for p in kept] == [
        ("tp_1", "move_orders", 0, "Seller"),
        ("tp_2", "split_bot", 1, "Seller"),
        ("tp_3", "retire_routine", 2, "Hunter"),
    ]
    assert (kept[0].order_titles, kept[0].to_name) == (("Call the leads",), "Writer")
    assert [part.routine_names for part in kept[1].parts] == [("Morning calls",), ("Invoices",)]
    assert kept[2].routine_name == "Hunt" and all(p.status == "waiting" for p in kept)


@pytest.mark.parametrize(
    "update",
    [
        {"kind": "move_orders", "orders": ["wo_gone"], "to_bot": "writer"},
        {"kind": "move_orders", "orders": ["wo_old"], "to_bot": DOT_AGENT_ID},
        {"kind": "move_orders", "orders": ["wo_old"], "to_bot": "seller"},
        {"kind": "split_bot", "bot": DOT_AGENT_ID, "parts": [SplitDraft(name="A", role="a", routines=[])] * 2},
        {"kind": "split_bot", "bot": "seller", "parts": [SplitDraft(name="A", role="a", routines=[])]},
        {
            "kind": "split_bot",
            "bot": "seller",
            "parts": [SplitDraft(name="Writer", role="a", routines=[]), SplitDraft(name="B", role="b", routines=[])],
        },
        {
            "kind": "split_bot",
            "bot": "seller",
            "parts": [SplitDraft(name="A", role="a", routines=["sch_h"]), SplitDraft(name="B", role="b", routines=[])],
        },
        {"kind": "retire_routine", "routine": "sch_off"},
        {"finding": 99, "kind": "retire_routine", "routine": "sch_h"},
    ],
)
def test_a_proposal_that_does_not_fit_the_team_is_asked_again(update: dict[str, object]) -> None:
    team: TeamSnapshot = _team()
    draft: ProposalDraft = _draft(1, "move_orders").model_copy(update=update)
    with pytest.raises(ProviderProtocolError):
        checked_proposals([draft], team, team_findings(team), ["tp_1"])


def test_a_review_is_due_once_its_period_passed_and_never_while_off() -> None:
    assert not review_due(TeamReviewState(), _NOW)
    assert review_due(TeamReviewState(every="weekly"), _NOW)
    assert not review_due(TeamReviewState(every="weekly", last_at=_NOW - 6 * _DAY), _NOW)
    assert review_due(TeamReviewState(every="weekly", last_at=_NOW - 7 * _DAY), _NOW)
    assert review_due(TeamReviewState(every="daily", last_at=_NOW - _DAY), _NOW)


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    opened: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        yield opened
    finally:
        await opened.close()


def _options(agent_id: str) -> ScheduleOptions:
    return ScheduleOptions(
        model=None,
        effort=None,
        speed=None,
        approvals=None,
        goal_mode=None,
        timezone=None,
        history_runs=None,
        same_chat=None,
        stop_after_failures=None,
        notify=None,
        agent_id=agent_id,
    )


class _Manager:
    """The manager's model: says which bot it answers as, and proposes what the test hands it, by the ids it got."""

    def __init__(self, propose: Callable[[TeamReviewRequest], list[ProposalDraft]]) -> None:
        self.asked: list[TeamReviewRequest] = []
        self.managers: list[str] = []
        self._propose: Callable[[TeamReviewRequest], list[ProposalDraft]] = propose

    def reviewer_for(self, manager: AgentRecord) -> TeamReviewer:
        self.managers.append(manager.id)

        async def review(request: TeamReviewRequest) -> TeamReviewAnswer:
            self.asked.append(request)
            drafts: list[ProposalDraft] = self._propose(request)
            return TeamReviewAnswer(
                summary="The team is stretched.",
                proposals=checked_proposals(drafts, request.snapshot, request.findings, request.ids[: len(drafts)]),
            )

        return review


def _runtime(store: Store) -> Runtime:
    return cast(Runtime, SimpleNamespace(store=store))


@pytest.mark.asyncio
async def test_a_team_with_nothing_found_asks_no_model(store: Store) -> None:
    manager: _Manager = _Manager(lambda request: [])
    state: TeamReviewState = await team_review_service.review(_runtime(store), time.time(), manager.reviewer_for)
    assert manager.asked == []
    assert state.review is not None and state.review.findings == () and state.review.summary == ""
    assert state.last_at is not None


@pytest.mark.asyncio
async def test_a_review_proposes_and_the_owner_accepts_or_skips_each_card(store: Store) -> None:
    hunter: AgentRecord = await store.create_agent(
        "Hunter", "Finds jobs", "Look widely.", "", None, None, ("web",), None, None
    )
    routines = [
        await store.add_schedule(name, "0 9 * * *", None, "goal", "task", None, _options(hunter.id), None, True)
        for name in ("Apply", "Follow up", "Archive")
    ]
    # Three of its four runs failed this week: a failing bot.
    for status in ("failed", "failed", "failed", "done"):
        thread = await store.create_thread("t", "ui")
        run = await store.create_run(
            thread.id,
            "g",
            "task",
            "scripted:test",
            None,
            "schedule",
            "low",
            "standard",
            None,
            "ask",
            False,
            None,
            hunter.id,
            None,
        )
        await store._db.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run.id))
    await store._db.commit()
    lead: AgentRecord = await store.create_agent("Lead", "Leads", "", "", None, None, (), None, None)
    await team_review_service.configure(store, lead.id, "weekly")

    def propose(request: TeamReviewRequest) -> list[ProposalDraft]:
        return [
            _draft(1, "split_bot").model_copy(
                update={
                    "bot": hunter.id,
                    "parts": [
                        SplitDraft(name="Hunter-apply", role="Applies", routines=[routines[0].id]),
                        SplitDraft(name="Hunter-follow", role="Follows up", routines=[routines[1].id]),
                    ],
                }
            ),
            _draft(1, "retire_routine").model_copy(update={"routine": routines[2].id}),
        ]

    manager: _Manager = _Manager(propose)
    state: TeamReviewState = await team_review_service.review(_runtime(store), time.time(), manager.reviewer_for)
    # The bot marked as manager answers.
    assert manager.managers == [lead.id]
    assert state.review is not None and state.review.manager_name == "Lead"
    assert [finding.kind for finding in state.review.findings] == ["failing"]
    split, retire = state.review.proposals
    # Nothing changed before the owner's yes.
    assert len(await store.list_agents()) == 2
    assert all(schedule.enabled for schedule in await store.list_schedules())

    no_desk: WorkDesk = cast(WorkDesk, None)
    state = await team_review_service.accept(store, no_desk, split.id, time.time())
    made: dict[str, AgentRecord] = {agent.name: agent for agent in await store.list_agents()}
    assert set(made) == {"Hunter", "Lead", "Hunter-apply", "Hunter-follow"}
    assert made["Hunter-apply"].instructions == "Look widely." and made["Hunter-apply"].tools == ("web",)
    owners: dict[str, str | None] = {s.id: s.options.agent_id for s in await store.list_schedules()}
    assert owners[routines[0].id] == made["Hunter-apply"].id
    assert owners[routines[1].id] == made["Hunter-follow"].id
    assert owners[routines[2].id] == hunter.id
    with pytest.raises(RunConflict):
        await team_review_service.accept(store, no_desk, split.id, time.time())

    state = await team_review_service.skip(store, retire.id, time.time())
    assert state.review is not None
    assert [proposal.status for proposal in state.review.proposals] == ["accepted", "skipped"]
    assert (await store.get_schedule(routines[2].id)).enabled
    with pytest.raises(NotFoundError):
        await team_review_service.skip(store, "tp_nothing", time.time())


@pytest.mark.asyncio
async def test_accepting_a_retirement_switches_the_routine_off_and_a_stale_one_is_refused(store: Store) -> None:
    bot: AgentRecord = await store.create_agent("Idle", "Waits", "", "", None, None, (), None, None)
    routine = await store.add_schedule("Ping", "0 9 * * *", None, "goal", "task", None, _options(bot.id), None, True)
    thread = await store.create_thread("t", "ui")
    for _ in range(3):
        run = await store.create_run(
            thread.id,
            "g",
            "task",
            "scripted:test",
            None,
            "schedule",
            "low",
            "standard",
            None,
            "ask",
            False,
            None,
            bot.id,
            None,
        )
        await store._db.execute("UPDATE runs SET status = 'failed' WHERE id = ?", (run.id,))
    await store._db.commit()
    manager: _Manager = _Manager(
        lambda request: [_draft(1, "retire_routine").model_copy(update={"routine": routine.id})]
    )
    state: TeamReviewState = await team_review_service.review(_runtime(store), time.time(), manager.reviewer_for)
    assert state.review is not None
    proposal_id: str = state.review.proposals[0].id
    # Dot reviews when no bot was chosen.
    assert manager.managers == [DOT_AGENT_ID]
    await store.switch_off_schedule(routine.id, "switched off: by hand")
    with pytest.raises(RunConflict):
        await team_review_service.accept(store, cast(WorkDesk, None), proposal_id, time.time())
    await store.set_schedule_enabled(routine.id, True, None)
    await team_review_service.accept(store, cast(WorkDesk, None), proposal_id, time.time())
    switched = await store.get_schedule(routine.id)
    assert not switched.enabled and switched.last_status == team_review_service.RETIRED_STATUS


@pytest.mark.asyncio
async def test_a_manager_that_is_not_a_bot_is_refused(store: Store) -> None:
    with pytest.raises(NotFoundError):
        await team_review_service.configure(store, "agt_nobody", "daily")
