"""Pre-approved spending (item 33): amounts read from labels and pages, the caps, and the policy's decision."""

from types import SimpleNamespace

import pytest

from dotagent.browser.safety import assess_step, dialog_is_financial, payment_steps
from dotagent.browser.types import Step
from dotagent.config import SpendCap
from dotagent.policy.elements import Element, StepAssessment, assessment_for, naming_reasons
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
from dotagent.store.models import RuleRecord
from dotagent.tools.browser import _payment_amount

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
        ("Pay CHF 1'234.50", Amount(value=1234.5, currency="CHF")),
        ("Payer 1\u202f234,56 €", Amount(value=1234.56, currency="€")),
        ("Betala 1 299,00 SEK", Amount(value=1299.0, currency="SEK")),
    ],
)
def test_the_amount_of_a_label_is_the_number_next_to_a_currency(label: str, amount: Amount | None) -> None:
    assert amount_in_label(label) == amount


@pytest.mark.parametrize(
    "label",
    [
        "Pay",
        "Pay $19.90",
        "Pay €19,90",
        "Total 9,90 €. Pay",
        "Total: €9.90\nPay",
        "Lamp — $9.90 — Buy",
        "Lamp - Buy",
        "Buy",
        "Buy $14.99",
        "Order now",
        "Order",
        "Pre-order",
        "Place your order",
        "Confirm and pay",
        "Agree & pay",
        "Complete checkout",
        "Proceed to pay",
        "Place bid",
        "Add funds",
        "Rent HD $3.99",
        "Order · €23.50",
        "Book for $199",
        "Book now · $199",
        "Book this room · $199",
        "Book flight $320",
        "Reserve now — €50",
        "Reserve a table — €10",
        "Book 2 tickets · $40",
        "Reserve for 2 guests — €50",
        "Book 1 room · $199",
        "Order (3 items) · $45.00",
        "Book (€199)",
        "Book at $199",
        "Reserve · from €120",
        "Book 1500 TL",
        "Book: $199",
        "Reserve: €50",
        "Order: $45.00",
        # Labels nobody foresaw err towards the hand-off: a book's place in its series reads as a booking, and a row of
        # past orders that shows its number without "#" as an order.
        "Dune · Book 1 · $9.99",
        "Order 4521 · $45.00 · Delivered",
        "Subscribe for €4.99/month",
        "Upgrade · 99 TL",
        "Satın al",
        "Şimdi öde",
        "Öde",
        "Onayla ve öde",
        "Ödeme yap",
        "Maksa",
        "Osta nyt",
        "Vahvista tilaus",
        "Tilaa ja maksa",
    ],
)
def test_a_label_that_asks_for_money_is_a_payment(label: str) -> None:
    found: StepAssessment = assessment_for(naming_reasons(Element(role="button", name=label)))
    assert found.risk == "high" and "financial" in found.flags, label


@pytest.mark.parametrize(
    "label",
    [
        "Payment methods",
        "PayPal",
        "Where to buy",
        "Buyer protection",
        "Order history",
        "Order 20261010",
        "Order #4521 · $45.00 · Delivered",
        "Order summary · $45.00",
        "Order total: $45.00",
        "Order details · $45.00",
        "Reserve price $500",
        "Orders over $35 ship free",
        "Checkout",
        "Proceed to checkout",
        "Continue to payment",
        "Subscribe",
        "Ode to Joy",
        "Maksaa 5 €",
    ],
)
def test_a_label_that_names_money_or_leads_to_the_payment_page_is_no_payment(label: str) -> None:
    """The control that takes the money is the payment: the way to the payment page is a plain click, so that the run
    fills the order before the button that pays is handed to the owner."""
    assert "financial" not in assessment_for(naming_reasons(Element(role="button", name=label))).flags, label


@pytest.mark.parametrize(
    "message",
    [
        "Order #4521 will be charged $45.00 to your card. Continue?",
        "Order 20261010: $45.00 will be charged",
        "Reserve: €50 will be taken from your card",
    ],
)
def test_a_dialog_that_names_an_order_and_its_amount_asks_to_pay(message: str) -> None:
    """In a dialog's sentences an order's number is the order about to be paid: the shapes that make a label a record
    do not make a dialog one."""
    assert dialog_is_financial(message), message


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


def test_a_rule_of_the_owners_that_hands_a_payment_off_holds_over_the_allowance() -> None:
    """The allowance stands in for the money hand-off, not for the owner's rules (item 385): a rule that hands the
    step off still does; one that only asks leaves the allowance its place."""
    amount: Amount = Amount(value=12.0, currency="€")
    state: SpendState = SpendState(host="shop.example", amount=amount, cap=CAPS["shop.example"], spent_today=0.0)
    allowance: Decision | None = spend_allowance(state)
    assert allowance is not None
    handing: RuleRecord = RuleRecord(
        id="rule_handoff",
        position=0,
        enabled=True,
        behavior="handoff",
        tool_glob="browser_act",
        match_regex="",
        description="Payments are mine.",
        created_at=0.0,
    )
    decision: Decision = decide(_payment(amount), "task", False, "aggressive", [handing], allowance)
    assert (decision.verdict, decision.source, decision.rule_id) == ("handoff", "rule", handing.id)
    asking: RuleRecord = RuleRecord(
        id="rule_ask",
        position=0,
        enabled=True,
        behavior="ask",
        tool_glob="browser_act",
        match_regex="",
        description="",
        created_at=0.0,
    )
    assert decide(_payment(amount), "task", False, "aggressive", [asking], allowance) is allowance


async def test_a_payment_steps_amount_is_the_largest_its_labels_and_its_page_name() -> None:
    """A coupon or a basket line in the same batch cannot stand for the payment (item 385): the largest amount of the
    labels and of the page's total is the one the caps read."""
    page: SimpleNamespace = SimpleNamespace(text="Basket\nOrder total: €499.00")

    async def read_text(thread_id: str, ref: None, offset: int, limit: int) -> SimpleNamespace:
        return page

    ctx: SimpleNamespace = SimpleNamespace(thread_id="thread_1", browser=SimpleNamespace(read_text=read_text))
    labels: list[Element | None] = [
        Element(role="button", name="Apply −€5 coupon"),
        None,
        Element(role="button", name="Pay €480.00"),
    ]
    assert await _payment_amount(ctx, labels) == Amount(value=499.0, currency="€")  # type: ignore[arg-type]
    page.text = "Nothing to pay here"
    assert await _payment_amount(ctx, labels) == Amount(value=480.0, currency="€")  # type: ignore[arg-type]
    assert await _payment_amount(ctx, [None]) is None  # type: ignore[arg-type]


def test_the_payment_steps_of_a_batch_are_the_clicks_that_pay_not_the_typing_into_a_card_field() -> None:
    """One amount stands for one payment (item 561): a batch with two payment steps would have passed for the larger
    of its amounts, so ``browser_act`` counts them and refuses a batch with more than one."""
    paying: StepAssessment = assessment_for(["payment"])
    plain: StepAssessment = assessment_for([])
    card: Element = Element(role="textbox", name="Card number")
    donate: Element = Element(role="button", name="Donate $10")
    support: Element = Element(role="button", name="Support the shelter")
    steps: list[tuple[Step, Element | None, StepAssessment]] = [
        # Typing into a card field is flagged as money, and moves none by itself.
        (Step(action="fill", ref="e8", text="4242 4242 4242 4242"), card, paying),
        (Step(action="click", ref="e5"), donate, paying),
        (Step(action="click", ref="e6"), support, plain),
        (Step(action="click_at", x=10, y=10), None, plain),
        (Step(action="click", ref="e1"), Element(role="button", name="Next"), plain),
    ]
    assert payment_steps(steps, frozenset()) == 1
    # A control no pattern knows that the typed judge found a payment is one too, each time it is clicked.
    assert payment_steps(steps, frozenset({support})) == 2
    assert payment_steps([steps[1], steps[1]], frozenset()) == 2
    assert payment_steps([], frozenset()) == 0


def test_enter_or_space_on_a_button_that_pays_is_a_payment_as_a_click_on_it_is() -> None:
    """Enter or Space on the control that has the focus presses it (the review of item 561): on a button that pays,
    the step is judged by the button's name as a click is, so it is money for the owner, and a batch counts it among
    its payments. Enter in a search box presses nothing that pays."""
    pay: Element = Element(role="button", name="Pay $45.00")
    search: Element = Element(role="textbox", name="Search")
    for key in ("Enter", "Space", " ", "Control+Enter"):
        judged: StepAssessment = assess_step(Step(action="press", ref="e5", key=key), pay, None)
        assert "financial" in judged.flags, (key, judged)
    assert "financial" not in assess_step(Step(action="press", ref="e2", key="Enter"), search, None).flags
    assert "financial" not in assess_step(Step(action="press", ref="e5", key="Tab"), pay, None).flags
    pressed: StepAssessment = assess_step(Step(action="press", ref="e5", key="Enter"), pay, None)
    clicked: StepAssessment = assess_step(
        Step(action="click", ref="e9"), Element(role="button", name="Place order"), None
    )
    steps: list[tuple[Step, Element | None, StepAssessment]] = [
        (Step(action="click", ref="e9"), Element(role="button", name="Place order"), clicked),
        (Step(action="press", ref="e5", key="Enter"), pay, pressed),
    ]
    assert payment_steps(steps, frozenset()) == 2
