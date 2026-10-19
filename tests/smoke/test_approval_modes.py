"""The approval modes against the real store, bus and broker: who is asked, what is recorded and what no mode waives.

The review model is replaced by plain functions that answer as told, so that what the gate does with each answer is
exact; the review model itself is exercised against real models in ``test_run_modes_live.py``.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio

from dotagent.agent.approvals import ApprovalBroker
from dotagent.agent.gate import REVIEWER_DECIDER, YOLO_DECIDER, RunGate, SiteLocks
from dotagent.agent.review import Reviewer, ReviewRequest, ReviewVerdict
from dotagent.config import PaceConfig, SpendCap
from dotagent.core.errors import ApprovalRejected, HandedOff, PolicyDenied
from dotagent.events import EventBus
from dotagent.policy.paths import CHANGES_DOT
from dotagent.policy.types import ActionInfo, Amount, ApprovalMode, Preset
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, EventRecord, RunMode, RunRecord, ThreadRecord

WAIT_S: float = 10.0
INSTRUCTION: str = "Save my notes in notes.md"
# No pace: these tests are about who answers the policy's questions.
NO_PACE: PaceConfig = PaceConfig(
    public_per_hour=0, public_per_day=0, social_per_hour=0, social_per_day=0, public_spacing_s=0.0
)

# What the cautious preset asks about, a payment (a hard stop that is the user's in every mode), a read of a key
# (refused in every mode) and a schedule (allowed by the aggressive preset until the run has read content from a
# stranger).
WRITE: ActionInfo = ActionInfo(
    tool="write_file", kind="write", risk="medium", summary="Write notes.md", target="notes.md", flags=()
)
PAYMENT: ActionInfo = ActionInfo(
    tool="browser_act",
    kind="browser_click",
    risk="high",
    summary="Pay the invoice",
    target="shop.example",
    flags=("financial",),
)
# A payment with its amount on a site with pre-approved caps (item 33).
CAPS: dict[str, SpendCap] = {"shop.example": SpendCap(per_purchase=20.0, per_day=30.0)}
PAID: ActionInfo = PAYMENT.model_copy(
    update={"target": "https://shop.example/checkout", "amount": Amount(value=12.0, currency="EUR")}
)
# A public post on a site whose pace allows one a day.
ONE_POST_A_DAY: PaceConfig = PaceConfig(
    public_per_hour=0, public_per_day=1, social_per_hour=0, social_per_day=0, public_spacing_s=0.0
)
POST: ActionInfo = ActionInfo(
    tool="browser_act",
    kind="browser_click",
    risk="medium",
    summary="Post the reply",
    target="https://social.example/thread",
    flags=("public_post",),
)
SECRET: ActionInfo = ActionInfo(
    tool="read_file",
    kind="read",
    risk="high",
    summary="Read the SSH key",
    target="~/.ssh/id_ed25519",
    flags=("secret_access",),
)
SCHEDULE: ActionInfo = ActionInfo(
    tool="schedule_create",
    kind="schedule",
    risk="medium",
    summary="Save a schedule",
    target="schedules",
    flags=("persistence",),
)
# A change of Dot's own files as the file tools describe it (item 398), a command that changes the workspace, one that
# only reads it, and a read of a file.
OWN_FILE: ActionInfo = ActionInfo(
    tool="edit_file",
    kind="write",
    risk="high",
    summary=f"Edit /Dot/src/dotagent/server/static/css/app.css ({CHANGES_DOT})",
    target="/Dot/src/dotagent/server/static/css/app.css",
    flags=("self_change",),
)
COMMAND: ActionInfo = ActionInfo(
    tool="shell", kind="shell", risk="low", summary="npm install", target="npm install", flags=()
)
LISTING: ActionInfo = ActionInfo(tool="shell", kind="shell", risk="safe", summary="ls", target="ls", flags=())
READ: ActionInfo = ActionInfo(
    tool="read_file", kind="read", risk="safe", summary="Read notes.md", target="notes.md", flags=()
)


@dataclass(frozen=True)
class Room:
    store: Store
    bus: EventBus
    broker: ApprovalBroker
    run: RunRecord


@dataclass(frozen=True)
class Question:
    """An authorisation that waits for the user, with the record of the question it put to them."""

    answer: asyncio.Task[str]
    record: ApprovalRecord


@pytest_asyncio.fixture
async def room(tmp_path: Path) -> AsyncIterator[Room]:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        bus: EventBus = EventBus(store)
        thread: ThreadRecord = await store.create_thread("approval modes", "test")
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


async def _nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked about this: {request.action.summary}")


def _answering(decision: Literal["approve", "deny", "escalate"], asked: list[ReviewRequest]) -> Reviewer:
    async def answer(request: ReviewRequest) -> ReviewVerdict:
        asked.append(request)
        return ReviewVerdict(decision=decision, reason=f"the review model says {decision}")

    return answer


def _gate(room: Room, run_mode: RunMode, preset: Preset, approvals: ApprovalMode, reviewer: Reviewer) -> RunGate:
    return RunGate(
        room.store,
        room.bus,
        room.broker,
        room.run.id,
        run_mode,
        preset,
        NO_PACE,
        {},
        approvals,
        reviewer,
        (INSTRUCTION,),
        SiteLocks(),
    )


async def _ask(room: Room, gate: RunGate, action: ActionInfo) -> Question:
    """Start an authorisation and return it once its question to the user is on the bus."""
    async with room.bus.subscribe(room.run.id) as events, asyncio.timeout(WAIT_S):
        answer: asyncio.Task[str] = asyncio.create_task(gate.authorize(action))
        event: EventRecord = await events.queue.get()
        while event.kind != "approval_requested":
            event = await events.queue.get()
    approval_id: object = event.data["approval_id"]
    assert isinstance(approval_id, str)
    return Question(answer=answer, record=await room.store.get_approval(approval_id))


async def _kinds(room: Room) -> list[str]:
    events: list[EventRecord] = await room.store.list_events(room.run.id, 0, 200)
    return [event.kind for event in events]


async def _decided(room: Room) -> list[ApprovalRecord]:
    return [record for record in await room.store.list_approvals(None, 50) if record.status != "pending"]


# ------------------------------------------------------------------------------------------------------------ ask


async def test_ask_mode_waits_for_the_user_and_hands_back_the_note(room: Room) -> None:
    question: Question = await _ask(room, _gate(room, "task", "cautious", "ask", _nobody), WRITE)
    assert question.record.tool == "write_file"
    assert not question.answer.done()
    await room.broker.resolve(question.record.id, True, "user", "go ahead")
    assert await asyncio.wait_for(question.answer, WAIT_S) == "go ahead"
    assert "approval_auto" not in await _kinds(room)


# ------------------------------------------------------------------------------------------------------------ yolo


async def test_yolo_lets_the_action_run_without_asking_and_keeps_the_record(room: Room) -> None:
    gate: RunGate = _gate(room, "task", "cautious", "yolo", _nobody)
    assert await asyncio.wait_for(gate.authorize(WRITE), WAIT_S) == ""
    assert await room.store.list_approvals("pending", 10) == []
    (record,) = await _decided(room)
    assert (record.status, record.decided_by, record.tool) == ("approved", YOLO_DECIDER, "write_file")
    # The record carries the action and the policy's reason once each (item 253): the timeline line joins them.
    assert record.summary == WRITE.summary
    assert record.note != "" and record.note not in record.summary
    kinds: list[str] = await _kinds(room)
    assert "approval_auto" in kinds
    assert "approval_requested" not in kinds
    assert (await room.store.get_run(room.run.id)).status != "blocked"


async def test_yolo_also_waives_the_question_that_untrusted_content_raises(room: Room) -> None:
    asking: RunGate = _gate(room, "task", "aggressive", "ask", _nobody)
    assert await asyncio.wait_for(asking.authorize(SCHEDULE), WAIT_S) == ""
    asking.taint()
    question: Question = await _ask(room, asking, SCHEDULE)
    await room.broker.resolve(question.record.id, False, "user", "")
    with pytest.raises(ApprovalRejected):
        await asyncio.wait_for(question.answer, WAIT_S)
    yolo: RunGate = _gate(room, "task", "aggressive", "yolo", _nobody)
    yolo.taint()
    assert await asyncio.wait_for(yolo.authorize(SCHEDULE), WAIT_S) == ""
    assert {record.decided_by: record.status for record in await _decided(room)} == {
        "user": "denied",
        YOLO_DECIDER: "approved",
    }


async def test_yolo_lets_secrets_and_money_through_and_keeps_a_record(room: Room) -> None:
    gate: RunGate = _gate(room, "task", "aggressive", "yolo", _nobody)
    assert await asyncio.wait_for(gate.authorize(SECRET), WAIT_S) == ""
    assert await asyncio.wait_for(gate.authorize(PAYMENT), WAIT_S) == ""
    assert sorted((record.tool, record.status, record.decided_by) for record in await _decided(room)) == sorted(
        [(SECRET.tool, "approved", YOLO_DECIDER), (PAYMENT.tool, "approved", YOLO_DECIDER)]
    )


async def test_a_hand_off_rule_of_the_user_still_stops_a_yolo_run(room: Room) -> None:
    await room.store.add_rule("handoff", "browser_act", "invoice", "Payments are mine")
    gate: RunGate = _gate(room, "task", "aggressive", "yolo", _nobody)
    question: Question = await _ask(room, gate, PAYMENT)
    assert question.record.summary == f"{PAYMENT.summary} — Payments are mine"
    await room.broker.resolve(question.record.id, True, "user", "paid it myself")
    with pytest.raises(HandedOff):
        await asyncio.wait_for(question.answer, WAIT_S)
    # What no rule hands off still goes through.
    assert await asyncio.wait_for(gate.authorize(SECRET), WAIT_S) == ""


async def test_a_payment_under_its_pre_approved_caps_is_a_question_and_counts_against_the_day(room: Room) -> None:
    gate: RunGate = RunGate(
        room.store,
        room.bus,
        room.broker,
        room.run.id,
        "task",
        "aggressive",
        NO_PACE,
        CAPS,
        "ask",
        _nobody,
        (INSTRUCTION,),
        SiteLocks(),
    )
    question: Question = await _ask(room, gate, PAID)
    assert question.record.severity == "high"
    assert "12.00 EUR on shop.example is within its pre-approved caps" in question.record.summary
    await room.broker.resolve(question.record.id, True, "user", "fine")
    assert await asyncio.wait_for(question.answer, WAIT_S) == "fine"
    assert await room.store.spent_since("shop.example", 0.0) == 12.0
    # The second purchase would take the day over 30: the money hand-off holds again.
    over: Question = await _ask(room, gate, PAID.model_copy(update={"amount": Amount(value=19.0, currency="EUR")}))
    assert over.record.severity == "handoff"
    await room.broker.resolve(over.record.id, True, "user", "paid it myself")
    with pytest.raises(HandedOff):
        await asyncio.wait_for(over.answer, WAIT_S)
    # A yolo run spends within the caps without a question; an unlisted site or no amount keeps the hand-off's waiver.
    yolo: RunGate = RunGate(
        room.store,
        room.bus,
        room.broker,
        room.run.id,
        "task",
        "aggressive",
        NO_PACE,
        CAPS,
        "yolo",
        _nobody,
        (),
        SiteLocks(),
    )
    assert (
        await asyncio.wait_for(
            yolo.authorize(PAID.model_copy(update={"amount": Amount(value=5.0, currency="EUR")})), WAIT_S
        )
        == ""
    )
    assert await room.store.spent_since("shop.example", 0.0) == 17.0
    assert await asyncio.wait_for(yolo.authorize(PAYMENT), WAIT_S) == ""
    assert await room.store.spent_since("shop.example", 0.0) == 17.0


async def test_a_second_payment_on_the_site_waits_for_the_first_and_counts_what_it_spent(room: Room) -> None:
    """Two runs pay on one site at once (item 136): the second is decided once the first is, from what it spent, so the
    two cannot both take what is left of the day."""
    locks: SiteLocks = SiteLocks()
    first, second = (
        RunGate(
            room.store,
            room.bus,
            room.broker,
            room.run.id,
            "task",
            "aggressive",
            NO_PACE,
            CAPS,
            "ask",
            _nobody,
            (INSTRUCTION,),
            locks,
        )
        for _ in range(2)
    )
    asked: Question = await _ask(room, first, PAID)
    later: asyncio.Task[str] = asyncio.create_task(
        second.authorize(PAID.model_copy(update={"amount": Amount(value=19.0, currency="EUR")}))
    )
    async with room.bus.subscribe(room.run.id) as events, asyncio.timeout(WAIT_S):
        await room.broker.resolve(asked.record.id, True, "user", "fine")
        event: EventRecord = await events.queue.get()
        while event.kind != "approval_requested":
            event = await events.queue.get()
    assert await asyncio.wait_for(asked.answer, WAIT_S) == "fine"
    approval_id: object = event.data["approval_id"]
    assert isinstance(approval_id, str)
    # 12 of the day's 30 are spent: 19 more would go over, so the money hand-off holds for the second.
    assert (await room.store.get_approval(approval_id)).severity == "handoff"
    await room.broker.resolve(approval_id, False, "user", "")
    with pytest.raises(ApprovalRejected):
        await asyncio.wait_for(later, WAIT_S)
    assert await room.store.spent_since("shop.example", 0.0) == 12.0


async def test_two_runs_cannot_both_take_the_last_public_action_of_the_day_on_a_site(room: Room) -> None:
    """The pace of a site counts a public action of another run that is being decided at the same time (item 136)."""
    locks: SiteLocks = SiteLocks()
    gates: list[RunGate] = [
        RunGate(
            room.store,
            room.bus,
            room.broker,
            room.run.id,
            "task",
            "aggressive",
            ONE_POST_A_DAY,
            {},
            "yolo",
            _nobody,
            (INSTRUCTION,),
            locks,
        )
        for _ in range(2)
    ]
    outcomes: list[str | BaseException] = await asyncio.wait_for(
        asyncio.gather(*(gate.authorize(POST) for gate in gates), return_exceptions=True), WAIT_S
    )
    assert outcomes[0] == ""
    assert isinstance(outcomes[1], PolicyDenied)
    assert len(await room.store.paced_times("social.example", "public", 0.0)) == 1


async def test_yolo_still_keeps_a_research_run_read_only(room: Room) -> None:
    gate: RunGate = _gate(room, "research", "aggressive", "yolo", _nobody)
    with pytest.raises(PolicyDenied):
        await asyncio.wait_for(gate.authorize(WRITE), WAIT_S)
    assert await room.store.list_approvals(None, 10) == []


# ------------------------------------------------------------------------------------------------------------ auto


async def test_auto_mode_lets_the_review_model_approve_from_the_instructions(room: Room) -> None:
    asked: list[ReviewRequest] = []
    gate: RunGate = _gate(room, "task", "cautious", "auto", _answering("approve", asked))
    gate.add_instruction("Use the title Notes")
    assert await asyncio.wait_for(gate.authorize(WRITE), WAIT_S) == ""
    (request,) = asked
    assert request.instructions == (INSTRUCTION, "Use the title Notes")
    assert (request.action, request.tainted) == (WRITE, False)
    (record,) = await _decided(room)
    assert (record.status, record.decided_by) == ("approved", REVIEWER_DECIDER)
    assert "the review model says approve" in record.note
    assert await room.store.list_approvals("pending", 10) == []


async def test_auto_mode_stops_the_action_the_review_model_refuses(room: Room) -> None:
    asked: list[ReviewRequest] = []
    gate: RunGate = _gate(room, "task", "cautious", "auto", _answering("deny", asked))
    with pytest.raises(ApprovalRejected) as refused:
        await asyncio.wait_for(gate.authorize(WRITE), WAIT_S)
    assert "the review model says deny" in refused.value.message
    (record,) = await _decided(room)
    assert (record.status, record.decided_by) == ("denied", REVIEWER_DECIDER)
    assert len(asked) == 1


async def test_auto_mode_passes_what_the_review_model_cannot_tell_to_the_user(room: Room) -> None:
    asked: list[ReviewRequest] = []
    question: Question = await _ask(room, _gate(room, "task", "cautious", "auto", _answering("escalate", asked)), WRITE)
    assert "passed this on to you" in question.record.summary
    assert "the review model says escalate" in question.record.summary
    await room.broker.resolve(question.record.id, True, "user", "yes, that one")
    assert await asyncio.wait_for(question.answer, WAIT_S) == "yes, that one"
    assert len(asked) == 1


async def test_auto_mode_never_asks_the_review_model_about_money(room: Room) -> None:
    question: Question = await _ask(room, _gate(room, "task", "aggressive", "auto", _nobody), PAYMENT)
    assert question.record.severity == "handoff"
    await room.broker.resolve(question.record.id, False, "user", "not now")
    with pytest.raises(ApprovalRejected):
        await asyncio.wait_for(question.answer, WAIT_S)


async def test_auto_mode_tells_the_review_model_when_the_run_read_untrusted_content(room: Room) -> None:
    asked: list[ReviewRequest] = []
    gate: RunGate = _gate(room, "task", "aggressive", "auto", _answering("approve", asked))
    gate.taint()
    assert await asyncio.wait_for(gate.authorize(SCHEDULE), WAIT_S) == ""
    (request,) = asked
    assert request.tainted


# ----------------------------------------------------------------------------------------------- Dot's own files


async def test_a_change_of_dots_own_files_asks_the_user_and_goes_ahead_when_allowed(room: Room) -> None:
    """Item 398: in every mode but yolo, the user answers it, never the review model, and the run makes the change once
    the user allows it; before, the change was refused outright in ask and auto."""
    for approvals in ("supervised", "ask", "auto"):
        question: Question = await _ask(room, _gate(room, "task", "aggressive", approvals, _nobody), OWN_FILE)
        assert question.record.tool == "edit_file"
        assert CHANGES_DOT in question.record.summary
        assert "only the user lets a run change Dot's own files" in question.record.summary
        assert question.record.severity == "high"
        await room.broker.resolve(question.record.id, True, "user", f"go ahead in {approvals}")
        assert await asyncio.wait_for(question.answer, WAIT_S) == f"go ahead in {approvals}"
    declined: Question = await _ask(room, _gate(room, "task", "aggressive", "auto", _nobody), OWN_FILE)
    await room.broker.resolve(declined.record.id, False, "user", "not that file")
    with pytest.raises(ApprovalRejected):
        await asyncio.wait_for(declined.answer, WAIT_S)


async def test_an_allow_rule_of_the_user_does_not_answer_for_dots_own_files(room: Room) -> None:
    await room.store.add_rule("allow", "edit_file", "", "Edits are fine")
    question: Question = await _ask(room, _gate(room, "task", "aggressive", "ask", _nobody), OWN_FILE)
    await room.broker.resolve(question.record.id, True, "user", "")
    assert await asyncio.wait_for(question.answer, WAIT_S) == ""


async def test_yolo_changes_dots_own_files_without_asking_and_keeps_the_record(room: Room) -> None:
    gate: RunGate = _gate(room, "task", "aggressive", "yolo", _nobody)
    assert await asyncio.wait_for(gate.authorize(OWN_FILE), WAIT_S) == ""
    (record,) = await _decided(room)
    assert (record.status, record.decided_by, record.tool) == ("approved", YOLO_DECIDER, "edit_file")


# ---------------------------------------------------------------------------------------------------- supervised


async def test_supervised_waits_for_the_user_before_a_change_the_preset_lets_through(room: Room) -> None:
    question: Question = await _ask(room, _gate(room, "task", "aggressive", "supervised", _nobody), WRITE)
    assert question.record.tool == "write_file"
    assert "Supervised asks before every change" in question.record.summary
    await room.broker.resolve(question.record.id, True, "user", "fine")
    assert await asyncio.wait_for(question.answer, WAIT_S) == "fine"
    command: Question = await _ask(room, _gate(room, "task", "aggressive", "supervised", _nobody), COMMAND)
    await room.broker.resolve(command.record.id, False, "user", "")
    with pytest.raises(ApprovalRejected):
        await asyncio.wait_for(command.answer, WAIT_S)


async def test_supervised_lets_reads_and_the_users_own_allow_rules_run(room: Room) -> None:
    gate: RunGate = _gate(room, "task", "aggressive", "supervised", _nobody)
    assert await asyncio.wait_for(gate.authorize(READ), WAIT_S) == ""
    assert await asyncio.wait_for(gate.authorize(LISTING), WAIT_S) == ""
    await room.store.add_rule("allow", "write_file", "", "Notes are fine")
    assert await asyncio.wait_for(gate.authorize(WRITE), WAIT_S) == ""
    assert await room.store.list_approvals(None, 10) == []
