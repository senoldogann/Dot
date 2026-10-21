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
from dotagent.server import UI_SOURCE
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, EventRecord, RunMode, RunRecord, ThreadRecord

WAIT_S: float = 10.0
INSTRUCTION: str = "Save my notes in notes.md"
# No pace: these tests are about who answers the policy's questions.
NO_PACE: PaceConfig = PaceConfig(
    public_per_hour=0, public_per_day=0, social_per_hour=0, social_per_day=0, public_spacing_s=0.0
)

# What the cautious preset asks about, a payment (a hard stop that is the user's in every mode), a read of a key
# (refused in every mode but yolo, item 433) and a schedule (allowed by the aggressive preset until the run has read
# content from a stranger).
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
# A message sent for the user (item 429), as AppleScript's send is classified.
MESSAGE: ActionInfo = ActionInfo(
    tool="apple_script",
    kind="app_control",
    risk="high",
    summary='Run applescript: tell application "Mail" to send the reply',
    target="Mail",
    flags=("external_send", "message_send"),
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


async def test_a_yolo_run_asks_nothing_at_all_and_the_record_says_what_it_would_have_asked(room: Room) -> None:
    """Item 443, the owner's word of 2026-10-20: a yolo run asks nothing — money, a message or a post in their name
    and a secret store read alike — and each step's record keeps the reason it would have asked for."""
    with pytest.raises(PolicyDenied):
        await asyncio.wait_for(_gate(room, "task", "aggressive", "ask", _nobody).authorize(SECRET), WAIT_S)
    gate: RunGate = _gate(room, "task", "aggressive", "yolo", _nobody)
    mailing: ActionInfo = MESSAGE.model_copy(update={"flags": ("secret_access", "external_send", "message_send")})
    for action in (PAYMENT, mailing, SECRET):
        assert await asyncio.wait_for(gate.authorize(action), WAIT_S) == "", action.summary
    waived: list[ApprovalRecord] = [record for record in await _decided(room) if record.decided_by == YOLO_DECIDER]
    notes: dict[str, str] = {record.summary: record.note for record in waived}
    assert set(notes) == {PAYMENT.summary, mailing.summary, SECRET.summary}, notes
    assert notes[PAYMENT.summary].startswith("financial actions are handed to the user"), notes
    assert "Dot asks before a message or a post in your name" in notes[mailing.summary], notes
    assert notes[SECRET.summary] == "every mode but yolo refuses this (secret_access)", notes
    assert "approval_auto" in await _kinds(room)


async def test_in_yolo_money_is_the_runs_even_in_a_step_that_reads_a_secret(room: Room) -> None:
    gate: RunGate = _gate(room, "task", "aggressive", "yolo", _nobody)
    paying: ActionInfo = SECRET.model_copy(update={"flags": ("secret_access", "financial")})
    assert await asyncio.wait_for(gate.authorize(paying), WAIT_S) == ""
    waived: list[ApprovalRecord] = [record for record in await _decided(room) if record.decided_by == YOLO_DECIDER]
    assert [record.summary for record in waived] == [paying.summary]
    assert "handed to the user" in waived[0].note


# ------------------------------------------------------------------------------------------------------------ a no


async def test_a_no_is_not_asked_again_in_the_run_until_the_owner_writes(room: Room) -> None:
    """Item 456: the owner's no to an action is kept; the same action is refused at once, with no card, no wait and
    no record, until the owner writes again; another action asks as before."""
    gate: RunGate = _gate(room, "task", "cautious", "ask", _nobody)
    first: Question = await _ask(room, gate, COMMAND)
    await room.broker.resolve(first.record.id, False, UI_SOURCE, "not that")
    with pytest.raises(ApprovalRejected):
        await asyncio.wait_for(first.answer, WAIT_S)
    asked: int = len(await room.store.list_approvals(None, 50))
    requested: int = (await _kinds(room)).count("approval_requested")
    with pytest.raises(ApprovalRejected) as again:
        await asyncio.wait_for(gate.authorize(COMMAND), 1.0)
    assert "“not that”" in again.value.message and "until the user writes again" in again.value.message
    assert len(await room.store.list_approvals(None, 50)) == asked
    assert (await _kinds(room)).count("approval_requested") == requested
    assert (await room.store.get_run(room.run.id)).status != "blocked"
    other: Question = await _ask(room, gate, COMMAND.model_copy(update={"summary": "npm ci", "target": "npm ci"}))
    await room.broker.resolve(other.record.id, True, UI_SOURCE, "")
    assert await asyncio.wait_for(other.answer, WAIT_S) == ""
    # The owner writes: the same action is theirs to answer again.
    gate.add_instruction("use the lock file this time")
    reopened: Question = await _ask(room, gate, COMMAND)
    await room.broker.resolve(reopened.record.id, True, UI_SOURCE, "go")
    assert await asyncio.wait_for(reopened.answer, WAIT_S) == "go"


async def test_only_the_owners_no_is_kept(room: Room) -> None:
    """A question nobody answered in time is no answer, and the no of the eval harness or of the review model is not
    the owner's: each is put again."""
    quick: RunGate = RunGate(
        room.store,
        room.bus,
        ApprovalBroker(room.store, room.bus, 0.2),
        room.run.id,
        "task",
        "cautious",
        NO_PACE,
        {},
        "ask",
        _nobody,
        (INSTRUCTION,),
        SiteLocks(),
    )
    for _ in range(2):
        with pytest.raises(ApprovalRejected):
            await asyncio.wait_for(quick.authorize(COMMAND), WAIT_S)
    assert (await _kinds(room)).count("approval_requested") == 2
    harness: RunGate = _gate(room, "task", "cautious", "ask", _nobody)
    for _ in range(2):
        refused: Question = await _ask(room, harness, COMMAND)
        await room.broker.resolve(refused.record.id, False, "eval", "nobody is there")
        with pytest.raises(ApprovalRejected):
            await asyncio.wait_for(refused.answer, WAIT_S)
    reviewed: list[ReviewRequest] = []
    refusing: RunGate = _gate(room, "task", "cautious", "auto", _answering("deny", reviewed))
    for _ in range(2):
        with pytest.raises(ApprovalRejected):
            await asyncio.wait_for(refusing.authorize(COMMAND), WAIT_S)
    assert len(reviewed) == 2


async def test_in_auto_the_review_model_is_not_asked_about_what_the_owner_refused(room: Room) -> None:
    asked: list[ReviewRequest] = []
    answers: list[Literal["approve", "deny", "escalate"]] = ["escalate", "approve"]

    async def passing_then_allowing(request: ReviewRequest) -> ReviewVerdict:
        asked.append(request)
        return ReviewVerdict(decision=answers[len(asked) - 1], reason="the review model's answer")

    gate: RunGate = _gate(room, "task", "cautious", "auto", passing_then_allowing)
    first: Question = await _ask(room, gate, COMMAND)
    await room.broker.resolve(first.record.id, False, UI_SOURCE, "not that")
    with pytest.raises(ApprovalRejected):
        await asyncio.wait_for(first.answer, WAIT_S)
    with pytest.raises(ApprovalRejected, match="declined this exact action"):
        await asyncio.wait_for(gate.authorize(COMMAND), 1.0)
    assert len(asked) == 1


async def test_a_no_given_in_another_mode_does_not_carry_into_yolo(room: Room) -> None:
    """Yolo asks nothing (item 443), and a no given to a run that asked stays with that run's gate."""
    asking: RunGate = _gate(room, "task", "cautious", "ask", _nobody)
    first: Question = await _ask(room, asking, COMMAND)
    await room.broker.resolve(first.record.id, False, UI_SOURCE, "not that")
    with pytest.raises(ApprovalRejected):
        await asyncio.wait_for(first.answer, WAIT_S)
    yolo: RunGate = _gate(room, "task", "cautious", "yolo", _nobody)
    assert await asyncio.wait_for(yolo.authorize(COMMAND), WAIT_S) == ""
    waived: list[ApprovalRecord] = [record for record in await _decided(room) if record.decided_by == YOLO_DECIDER]
    assert [record.summary for record in waived] == [COMMAND.summary]


async def test_no_rule_of_the_user_lets_a_message_go_without_their_yes(room: Room) -> None:
    await room.store.add_rule("allow", "apple_script", "", "Mail is fine")
    allowing: RunGate = _gate(room, "task", "aggressive", "ask", _nobody)
    question: Question = await _ask(room, allowing, MESSAGE)
    assert "whatever your rules say" in question.record.summary
    await room.broker.resolve(question.record.id, True, "user", "send it")
    assert await asyncio.wait_for(question.answer, WAIT_S) == "send it"
    # A rule that hands the message to the user is stricter, and it holds where the modes ask.
    await room.store.add_rule("handoff", "apple_script", "reply", "Replies are mine")
    for rule in await room.store.list_rules():
        if rule.description == "Mail is fine":
            await room.store.delete_rule(rule.id)
    handed: Question = await _ask(room, _gate(room, "task", "aggressive", "ask", _nobody), MESSAGE)
    assert handed.record.severity == "handoff" and handed.record.summary.endswith("Replies are mine")
    await room.broker.resolve(handed.record.id, True, "user", "sent it myself")
    with pytest.raises(HandedOff):
        await asyncio.wait_for(handed.answer, WAIT_S)
    # A yolo run asks nothing, the owner's own hand-off among them (item 443).
    assert await asyncio.wait_for(_gate(room, "task", "aggressive", "yolo", _nobody).authorize(MESSAGE), WAIT_S) == ""


async def test_a_hand_off_rule_of_the_user_stops_a_run_that_asks_and_is_waived_in_yolo(room: Room) -> None:
    await room.store.add_rule("handoff", "schedule_create", "", "Schedules are mine")
    asked: Question = await _ask(room, _gate(room, "task", "aggressive", "ask", _nobody), SCHEDULE)
    assert asked.record.summary == f"{SCHEDULE.summary} — Schedules are mine"
    await room.broker.resolve(asked.record.id, True, "user", "saved it myself")
    with pytest.raises(HandedOff):
        await asyncio.wait_for(asked.answer, WAIT_S)
    # A yolo run asks nothing (item 443): the rule's hand-off is recorded with the step and the step goes on.
    gate: RunGate = _gate(room, "task", "aggressive", "yolo", _nobody)
    assert await asyncio.wait_for(gate.authorize(SCHEDULE), WAIT_S) == ""
    assert await asyncio.wait_for(gate.authorize(WRITE), WAIT_S) == ""
    waived: list[ApprovalRecord] = [record for record in await _decided(room) if record.decided_by == YOLO_DECIDER]
    assert [record.summary for record in waived] == [SCHEDULE.summary]


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
    # A yolo run asks nothing (item 443): a payment within the caps and one no cap covers both go through, and each
    # counts against the day as any other does.
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
    within: ActionInfo = PAID.model_copy(update={"amount": Amount(value=5.0, currency="EUR")})
    assert await asyncio.wait_for(yolo.authorize(within), WAIT_S) == ""
    assert await room.store.spent_since("shop.example", 0.0) == 17.0
    # A payment past the caps is the one the hand-off held: a yolo run makes it, and what it spends counts against the
    # site's day as any other payment's does, so a later run of a mode that asks meets the true total (item 443).
    over: ActionInfo = PAID.model_copy(update={"amount": Amount(value=40.0, currency="EUR")})
    assert await asyncio.wait_for(yolo.authorize(over), WAIT_S) == ""
    assert await room.store.spent_since("shop.example", 0.0) == 57.0
    waived: list[ApprovalRecord] = [record for record in await _decided(room) if record.decided_by == YOLO_DECIDER]
    assert [record.summary for record in waived] == [within.summary, over.summary]
    assert all(record.note for record in waived)


async def test_a_capped_payment_that_also_posts_asks_and_still_counts_against_the_day(room: Room) -> None:
    """A payment under its caps whose step also posts is asked by the guard (item 429), and once it goes ahead its spend
    counts against the day as any other's: a page cannot keep the day's cap from filling by adding a post."""
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
    sharing: ActionInfo = PAID.model_copy(update={"flags": ("financial", "public_post")})
    question: Question = await _ask(room, gate, sharing)
    assert "Dot asks before a message or a post in your name" in question.record.summary
    await room.broker.resolve(question.record.id, True, "user", "fine")
    assert await asyncio.wait_for(question.answer, WAIT_S) == "fine"
    assert await room.store.spent_since("shop.example", 0.0) == 12.0
    # 12 of the day's 30 are spent: 19 more would go over, so the money hand-off holds.
    over: Question = await _ask(room, gate, sharing.model_copy(update={"amount": Amount(value=19.0, currency="EUR")}))
    assert over.record.severity == "handoff"
    await room.broker.resolve(over.record.id, False, "user", "")
    with pytest.raises(ApprovalRejected):
        await asyncio.wait_for(over.answer, WAIT_S)
    assert await room.store.spent_since("shop.example", 0.0) == 12.0


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
    # A yolo run asks nothing (item 443): the two runs race for the last public action of the day, and the site's pace
    # lets one of them take it and refuses the other.
    outcomes: list[str | BaseException] = await asyncio.gather(
        *(gate.authorize(POST) for gate in gates), return_exceptions=True
    )
    assert sorted(outcome if isinstance(outcome, str) else type(outcome).__name__ for outcome in outcomes) == [
        "",
        "PolicyDenied",
    ]
    assert len(await room.store.paced_times("social.example", "public", 0.0)) == 1


async def test_yolo_still_keeps_a_research_run_read_only(room: Room) -> None:
    gate: RunGate = _gate(room, "research", "aggressive", "yolo", _nobody)
    with pytest.raises(PolicyDenied):
        await asyncio.wait_for(gate.authorize(WRITE), WAIT_S)
    # Waiving the always-on review (item 433) does not lift the research run's own limit.
    with pytest.raises(PolicyDenied):
        await asyncio.wait_for(gate.authorize(WRITE.model_copy(update={"flags": ("secret_access",)})), WAIT_S)
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
    the user allows it."""
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
