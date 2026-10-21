"""What decided a step the gate let through (item 468), on a real store and bus with no model: the preset or the owner's
rule when nobody was asked, and who answered a question: the owner, the review model or yolo, which asks nothing."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest_asyncio

from dotagent.agent.approvals import ApprovalBroker
from dotagent.agent.authorization import Authorization, StepDecision
from dotagent.agent.gate import RunGate, SiteLocks
from dotagent.agent.review import Reviewer, ReviewRequest, ReviewVerdict
from dotagent.config import PaceConfig
from dotagent.events import EventBus
from dotagent.policy.types import ActionInfo, ApprovalMode, Preset
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, RuleRecord, RunRecord, ThreadRecord

WAIT_S: float = 10.0
INSTRUCTION: str = "Save my notes in notes.md"
NO_PACE: PaceConfig = PaceConfig(
    public_per_hour=0, public_per_day=0, social_per_hour=0, social_per_day=0, public_spacing_s=0.0
)
READ: ActionInfo = ActionInfo(
    tool="read_file", kind="read", risk="safe", summary="Read notes.md", target="notes.md", flags=()
)
WRITE: ActionInfo = ActionInfo(
    tool="write_file", kind="write", risk="medium", summary="Write notes.md", target="notes.md", flags=()
)


@dataclass(frozen=True)
class Room:
    store: Store
    bus: EventBus
    broker: ApprovalBroker
    run: RunRecord


@pytest_asyncio.fixture
async def room(tmp_path: Path) -> AsyncIterator[Room]:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        bus: EventBus = EventBus(store)
        thread: ThreadRecord = await store.create_thread("step decisions", "test")
        run: RunRecord = await store.create_run(
            thread.id,
            INSTRUCTION,
            "task",
            "ollama:none",
            None,
            "test",
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            None,
            None,
        )
        yield Room(store=store, bus=bus, broker=ApprovalBroker(store, bus, WAIT_S), run=run)
    finally:
        await store.close()


def _answering(decision: Literal["approve", "deny", "escalate"]) -> Reviewer:
    async def answer(request: ReviewRequest) -> ReviewVerdict:
        return ReviewVerdict(decision=decision, reason=f"the review model says {decision}")

    return answer


async def _nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked about this: {request.action.summary}")


def _gate(room: Room, preset: Preset, approvals: ApprovalMode, reviewer: Reviewer) -> RunGate:
    return RunGate(
        room.store,
        room.bus,
        room.broker,
        room.run.id,
        "task",
        preset,
        NO_PACE,
        {},
        approvals,
        reviewer,
        (INSTRUCTION,),
        SiteLocks(),
    )


async def _answered_by_the_owner(room: Room, gate: RunGate, action: ActionInfo) -> Authorization:
    """Authorise ``action`` and approve, as the owner, the question it puts to them."""
    async with room.bus.subscribe(room.run.id) as events, asyncio.timeout(WAIT_S):
        authorizing: asyncio.Task[Authorization] = asyncio.create_task(gate.authorize_recorded(action))
        event: EventRecord = await events.queue.get()
        while event.kind != "approval_requested":
            event = await events.queue.get()
        approval_id: object = event.data["approval_id"]
        assert isinstance(approval_id, str)
        await room.broker.resolve(approval_id, True, "user", "go ahead")
        return await authorizing


async def test_a_step_nobody_was_asked_about_names_the_preset_or_the_owners_rule(room: Room) -> None:
    read: StepDecision = (await _gate(room, "balanced", "ask", _nobody).authorize_recorded(READ)).decision
    assert (read.verdict, read.source, read.answered_by, read.preset, read.rule_id) == (
        "allow",
        "preset",
        "policy",
        "balanced",
        None,
    )
    rule: RuleRecord = await room.store.add_rule("allow", "write_file", "", "Notes are fine")
    written: StepDecision = (await _gate(room, "cautious", "ask", _nobody).authorize_recorded(WRITE)).decision
    assert (written.source, written.rule_id, written.reason, written.answered_by) == (
        "rule",
        rule.id,
        "Notes are fine",
        "policy",
    )


async def test_who_answered_a_question_is_kept_with_the_step(room: Room) -> None:
    owner: Authorization = await _answered_by_the_owner(room, _gate(room, "cautious", "ask", _nobody), WRITE)
    assert (owner.note, owner.decision.verdict, owner.decision.answered_by) == ("go ahead", "ask", "user")
    reviewed: StepDecision = (
        await _gate(room, "cautious", "auto", _answering("approve")).authorize_recorded(WRITE)
    ).decision
    assert (reviewed.verdict, reviewed.answered_by) == ("ask", "reviewer")
    passed_on: Authorization = await _answered_by_the_owner(
        room, _gate(room, "cautious", "auto", _answering("escalate")), WRITE
    )
    assert passed_on.decision.answered_by == "user"
    waived: StepDecision = (await _gate(room, "cautious", "yolo", _nobody).authorize_recorded(WRITE)).decision
    assert (waived.verdict, waived.answered_by) == ("ask", "yolo") and waived.reason != ""
    supervised: Authorization = await _answered_by_the_owner(
        room, _gate(room, "balanced", "supervised", _nobody), WRITE
    )
    assert (supervised.decision.source, supervised.decision.answered_by) == ("supervised", "user")
    # authorize gives the owner's note as it did.
    assert await _gate(room, "balanced", "ask", _nobody).authorize(READ) == ""


async def test_the_taint_the_gate_weighed_is_kept_with_the_decision_and_the_question(room: Room) -> None:
    """Item 474: a step's decision, and the policy event of a question, say whether the run was tainted when the gate
    decided, so that what a rule would have done there can be told later (item 469)."""
    clean: StepDecision = (await _gate(room, "balanced", "ask", _nobody).authorize_recorded(READ)).decision
    assert clean.tainted is False
    tainted: RunGate = _gate(room, "cautious", "ask", _nobody)
    tainted.taint()
    read: StepDecision = (await tainted.authorize_recorded(READ)).decision
    assert read.tainted is True
    asked: Authorization = await _answered_by_the_owner(room, tainted, WRITE)
    assert asked.decision.tainted is True
    events: list[EventRecord] = await room.store.list_events(room.run.id, 0, 500)
    policy: EventRecord = [event for event in events if event.kind == "policy"][-1]
    assert (policy.data["summary"], policy.data["tainted"]) == ("Write notes.md", True)
