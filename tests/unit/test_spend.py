"""Pre-approved spending (item 33): amounts read from labels and pages, the caps, and the policy's decision."""

import pytest

from dotagent.config import SpendCap
from dotagent.policy.engine import decide
from dotagent.policy.spend import (
    SpendState,
    amount_in_label,
    find_amounts,
    spend_allowance,
    spend_host,
    spend_state,
    total_on_page,
)
from dotagent.policy.types import ActionInfo, Amount, Decision

CAPS: dict[str, SpendCap] = {"shop.example": SpendCap(per_purchase=20.0, per_day=30.0)}
URL: str = "https://www.shop.example/checkout"


def _payment(amount: Amount | None, flags: tuple[str, ...] = ("financial",)) -> ActionInfo:
    return ActionInfo(
        tool="browser_act",
        kind="browser_click",
        risk="high",
        summary="click Pay now",
        target=URL,
        flags=flags,  # type: ignore[arg-type]
        amount=amount,
    )


@pytest.mark.parametrize(
    ("label", "amount"),
    [
        ("Pay €19,90", Amount(value=19.9, currency="€")),
        ("Place order · $1,234.50", Amount(value=1234.5, currency="$")),
        ("Buy now for 12 TL", Amount(value=12.0, currency="TL")),
        ("USD 5", Amount(value=5.0, currency="USD")),
        ("Pay now", None),
        ("Order 20261010", None),
        # Item 350: a number is read whole, and of several amounts the largest counts.
        ("Pay $1500.00", Amount(value=1500.0, currency="$")),
        ("Pay $150000", Amount(value=150000.0, currency="$")),
        ("Pay $1000.50", Amount(value=1000.5, currency="$")),
        ("Pay 2500 TL", Amount(value=2500.0, currency="TL")),
        ("Save $5 — Pay $500", Amount(value=500.0, currency="$")),
    ],
)
def test_the_amount_of_a_label_is_the_number_next_to_a_currency(label: str, amount: Amount | None) -> None:
    assert amount_in_label(label) == amount


def test_the_total_of_a_page_is_the_largest_amount_after_a_total_cue() -> None:
    page: str = "Lamp €9.90\nShipping €4.00\nSubtotal €9.90\nOrder total: €13.90\nPay now"
    assert total_on_page(page) == Amount(value=13.9, currency="€")
    assert total_on_page("Nothing to pay here") is None
    # A total of savings after the order's own total does not stand for it (item 350).
    assert total_on_page("Order total: $999.00\nTotal savings: $2.00") == Amount(value=999.0, currency="$")
    assert find_amounts("from $5 to $10") == (Amount(value=5.0, currency="$"), Amount(value=10.0, currency="$"))


def test_the_caps_apply_to_a_listed_site_with_a_readable_amount() -> None:
    assert spend_host(URL) == "shop.example"
    assert spend_host("shop.example") is None
    state: SpendState | None = spend_state(CAPS, URL, Amount(value=12.0, currency="€"), 0.0)
    assert state is not None and state.host == "shop.example"
    assert spend_state(CAPS, "https://other.example/pay", Amount(value=1.0, currency="€"), 0.0) is None
    assert spend_state(CAPS, URL, None, 0.0) is None


@pytest.mark.parametrize(
    ("value", "spent", "allowed"),
    [(12.0, 0.0, True), (20.0, 10.0, True), (20.01, 0.0, False), (12.0, 18.5, False), (1.0, 29.0, True)],
)
def test_a_spend_is_pre_approved_under_both_caps(value: float, spent: float, allowed: bool) -> None:
    state: SpendState = SpendState(
        host="shop.example", amount=Amount(value=value, currency="€"), cap=CAPS["shop.example"], spent_today=spent
    )
    decision: Decision | None = spend_allowance(state)
    assert (decision is not None) == allowed
    if decision is not None:
        assert (decision.verdict, decision.source) == ("ask", "spend")
        assert "within its pre-approved caps" in decision.reason


def test_the_allowance_replaces_the_money_hand_off_but_not_a_credential_one() -> None:
    amount: Amount = Amount(value=12.0, currency="€")
    state: SpendState = SpendState(host="shop.example", amount=amount, cap=CAPS["shop.example"], spent_today=0.0)
    allowance: Decision | None = spend_allowance(state)
    assert allowance is not None
    assert decide(_payment(amount), "task", False, "aggressive", [], allowance) is allowance
    assert decide(_payment(amount), "task", False, "aggressive", [], None).source == "hard_stop"
    with_secret: ActionInfo = _payment(amount, ("financial", "credential"))
    assert decide(with_secret, "task", False, "aggressive", [], allowance).verdict == "handoff"
