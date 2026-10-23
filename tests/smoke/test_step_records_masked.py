"""The records of a step hold no secret Dot holds (item 551), on a real store and bus with no model: its policy event,
its approval and the card of it, the decision kept with its result and the context of the error a refusal raises, in
every approval mode. The policy and the review model still weigh the step as it is."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio

from dotagent.agent.approvals import ApprovalBroker
from dotagent.agent.authorization import Authorization
from dotagent.agent.gate import RunGate, SiteLocks
from dotagent.agent.review import Reviewer, ReviewRequest, ReviewVerdict
from dotagent.config import PaceConfig
from dotagent.core.errors import HandedOff, PolicyDenied
from dotagent.events import EventBus
from dotagent.policy.types import ActionInfo, ApprovalMode
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, EventRecord, RunRecord, ThreadRecord

WAIT_S: float = 10.0
INSTRUCTION: str = "Fetch the report"
NO_PACE: PaceConfig = PaceConfig(
    public_per_hour=0, public_per_day=0, social_per_hour=0, social_per_day=0, public_spacing_s=0.0
)
# Made-up values in the forms of a key and of a secret Dot holds; none of them is a real one.
KEY_FORM: str = "sk-test-0123456789abcdefghij"
HELD: str = "correct-horse-battery-staple"
COMMAND: str = f'curl -s -H "Authorization: Bearer {KEY_FORM}" "https://api.example/report?k={HELD}"'
# A command that carries both, as each flag of the policy takes it: a change that asks, a credential that is handed to
# the owner, and a secret that every mode but yolo refuses; the last two quote the summary in their reason.
ASKS: ActionInfo = ActionInfo(
    tool="write_file", kind="write", risk="medium", summary=f"Write {COMMAND}", target=COMMAND, flags=()
)
CREDENTIAL: ActionInfo = ActionInfo(
    tool="shell", kind="shell", risk="high", summary=f"Run: {COMMAND}", target=COMMAND, flags=("credential",)
)
SECRET: ActionInfo = ActionInfo(
    tool="shell", kind="shell", risk="high", summary=f"Run: {COMMAND}", target=COMMAND, flags=("secret_access",)
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
        thread: ThreadRecord = await store.create_thread("masked records", "test")
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


@pytest.fixture(autouse=True)
def held_in_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A secret Dot holds: a value of the ``.env`` of a Dot home of the test's own."""
    home: Path = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text(f"DEMO_SERVICE_KEY={HELD}\n", encoding="utf-8")
    monkeypatch.setenv("DOT_HOME", str(home))


@dataclass
class _Reviewer:
    """A review model that gives ``decision`` and keeps what it was asked."""

    decision: Literal["approve", "deny", "escalate"]
    asked: list[ReviewRequest]

    async def __call__(self, request: ReviewRequest) -> ReviewVerdict:
        self.asked.append(request)
        return ReviewVerdict(decision=self.decision, reason=f"the review model says {self.decision}")


async def _nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked about this: {request.action.summary}")


def _gate(room: Room, approvals: ApprovalMode, reviewer: Reviewer) -> RunGate:
    return RunGate(
        room.store,
        room.bus,
        room.broker,
        room.run.id,
        "task",
        "cautious",
        NO_PACE,
        {},
        approvals,
        reviewer,
        (INSTRUCTION,),
        SiteLocks(),
    )


def _leaks(*records: object) -> list[str]:
    """The secrets, as made up here, that stand in any of ``records``."""
    return [secret for secret in (HELD, KEY_FORM) if any(secret in str(record) for record in records)]


async def _records_of(room: Room) -> list[object]:
    """Everything the store keeps of the run's steps: its events and its approvals."""
    events: list[EventRecord] = [
        event
        for kind in ("policy", "approval_requested", "approval_auto", "approval_decided")
        for event in await room.store.events_of_kind(room.run.id, kind, 20)
    ]
    approvals: list[ApprovalRecord] = [
        record for record in await room.store.list_approvals(None, 20) if record.run_id == room.run.id
    ]
    return [*events, *approvals]


async def _asked(room: Room, gate: RunGate, action: ActionInfo, approve: bool) -> list[EventRecord]:
    """Authorise ``action``, answer the question it puts to the owner (yes or no), and return the events the question
    published: the card as it was shown. The error of a refusal is raised on to the caller."""
    shown: list[EventRecord] = []
    async with room.bus.subscribe(room.run.id) as events, asyncio.timeout(WAIT_S):
        authorizing: asyncio.Task[Authorization] = asyncio.create_task(gate.authorize_recorded(action))
        event: EventRecord = await events.queue.get()
        shown.append(event)
        while event.kind != "approval_requested":
            event = await events.queue.get()
            shown.append(event)
        approval_id: object = event.data["approval_id"]
        assert isinstance(approval_id, str)
        await room.broker.resolve(approval_id, approve, "user", "go ahead")
        await authorizing
    return shown


async def test_a_yolo_step_keeps_no_secret_in_its_records(room: Room) -> None:
    done: Authorization = await _gate(room, "yolo", _nobody).authorize_recorded(CREDENTIAL)
    # The reason the step would have asked for quotes its summary, and the decision kept with the result is masked.
    assert "credential actions are handed to the user" in done.decision.reason and "***" in done.decision.reason
    assert _leaks(done.decision, *await _records_of(room)) == []
    (waived,) = await room.store.events_of_kind(room.run.id, "approval_auto", 10)
    assert "***" in str(waived.data["summary"]) and "***" in str(waived.data["args"]), waived.data


async def test_a_question_to_the_owner_shows_the_step_as_its_records_do(room: Room) -> None:
    shown: list[EventRecord] = await _asked(room, _gate(room, "ask", _nobody), ASKS, True)
    assert [event.kind for event in shown][-1] == "approval_requested"
    assert _leaks(*shown, *await _records_of(room)) == []
    (card,) = [event for event in shown if event.kind == "approval_requested"]
    assert (card.data["args"], card.data["summary"]) != ({}, ""), card.data
    assert COMMAND not in str(card.data) and "***" in str(card.data["args"]), card.data
    # A step the owner refuses raises with the same masked step in its error.
    with pytest.raises(Exception) as refused:
        await _asked(room, _gate(room, "ask", _nobody), ASKS, False)
    assert _leaks(refused.value, getattr(refused.value, "context", {})) == []
    assert _leaks(*await _records_of(room)) == []


async def test_a_step_handed_to_the_owner_raises_with_no_secret_in_its_context(room: Room) -> None:
    with pytest.raises(HandedOff) as handed:
        await _asked(room, _gate(room, "ask", _nobody), CREDENTIAL, True)
    assert _leaks(handed.value.context) == [] and "***" in str(handed.value.context)
    assert _leaks(*await _records_of(room)) == []


async def test_the_review_model_weighs_the_step_as_it_is_and_its_record_masks_it(room: Room) -> None:
    reviewer: _Reviewer = _Reviewer(decision="approve", asked=[])
    done: Authorization = await _gate(room, "auto", reviewer).authorize_recorded(ASKS)
    assert done.decision.answered_by == "reviewer"
    (asked,) = reviewer.asked
    assert asked.action == ASKS
    assert _leaks(done.decision, *await _records_of(room)) == []
    assert any(record.summary.startswith("Write curl") for record in await room.store.list_approvals(None, 10))


async def test_a_refusal_keeps_no_secret_in_its_policy_event_nor_in_its_error(room: Room) -> None:
    with pytest.raises(PolicyDenied) as refused:
        await _gate(room, "ask", _nobody).authorize_recorded(SECRET)
    assert "auto-review refused this action" in refused.value.message
    assert _leaks(refused.value.context) == [] and "***" in str(refused.value.context)
    (policy,) = await room.store.events_of_kind(room.run.id, "policy", 10)
    assert (policy.data["verdict"], policy.data["source"]) == ("deny", "auto_review")
    assert "auto-review refused this action" in str(policy.data["reason"]) and "***" in str(policy.data["reason"])
    assert _leaks(*await _records_of(room)) == []
