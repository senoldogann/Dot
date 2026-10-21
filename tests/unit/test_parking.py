"""Steps kept for the owner (item 482), the pure part: which runs keep a hand-off nobody answers, the fingerprint that
keeps the same step once in a chat, and what the run is told of it."""

from typing import Literal

import pytest

from dotagent.agent.parking import Parking, handed_back_text, kept_digest, kept_text, parking_for
from dotagent.config import PolicyConfig, load_default_config
from dotagent.policy.types import ActionInfo, Amount, ApprovalMode

_PAY: ActionInfo = ActionInfo(
    tool="shell",
    kind="shell",
    risk="high",
    summary="pay the invoice of October",
    target="./pay.sh --invoice 1042",
    flags=("financial", "external_send"),
)


def _policy(unanswered: Literal["refuse", "park"]) -> PolicyConfig:
    return load_default_config().policy.model_copy(update={"unanswered": unanswered, "parked_hours": 2.0})


@pytest.mark.parametrize("approvals", ["supervised", "ask", "auto"])
def test_a_run_that_asks_keeps_its_hand_offs_where_park_is_set(approvals: ApprovalMode) -> None:
    assert parking_for(_policy("park"), approvals, False, False, False, "thr-1", "sch-1") == Parking(
        thread_id="thr-1", schedule_id="sch-1", keep_s=7200.0
    )
    assert parking_for(_policy("refuse"), approvals, False, False, False, "thr-1", "sch-1") is None


@pytest.mark.parametrize(
    ("approvals", "delegated", "session", "evaluated"),
    [
        ("yolo", False, False, False),
        ("ask", True, False, False),
        ("ask", False, True, False),
        ("ask", False, False, True),
    ],
)
def test_yolo_a_delegated_run_a_session_and_an_eval_keep_nothing(
    approvals: ApprovalMode, delegated: bool, session: bool, evaluated: bool
) -> None:
    assert parking_for(_policy("park"), approvals, delegated, session, evaluated, "thr-1", None) is None


def test_the_fingerprint_is_the_step_as_its_card_showed_it() -> None:
    assert kept_digest(_PAY) == kept_digest(_PAY.model_copy(update={"flags": ("external_send", "financial")}))
    for changed in (
        {"tool": "terminal"},
        {"summary": "pay the invoice of November"},
        {"target": "./pay.sh --invoice 1043"},
        {"flags": ("financial",)},
        {"amount": Amount(value=120.0, currency="EUR")},
    ):
        assert kept_digest(_PAY.model_copy(update=changed)) != kept_digest(_PAY), changed


def test_what_the_run_is_told_of_a_kept_step_and_of_the_owner_doing_it() -> None:
    assert "(apr-1)" in kept_text("apr-1", True)
    assert "waits for the user already (apr-1)" in kept_text("apr-1", False)
    said: str = "The user did the step that waited for them (apr-1) themselves; go on from there."
    assert handed_back_text("apr-1", "  ") == said
    assert handed_back_text("apr-1", " Paid by card. ") == f"{said} They wrote: Paid by card."
