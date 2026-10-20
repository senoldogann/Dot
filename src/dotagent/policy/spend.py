"""Pre-approved spending: a payment step on a listed site under its caps is a question instead of a hand-off.

``[policy.spend]`` lists sites with a cap per purchase and per day. The amount of a payment step is read from the labels
of its controls ("Pay €19.90") and from the totals on the page ("Order total: $12.00"); where several amounts stand, the
largest is taken, so a misread errs towards the hand-off. A step whose amount cannot be read keeps the hand-off. Under
both caps the policy asks instead of handing the step to the user, so an ``auto`` run lets the review model answer, and
an ``ask`` or a ``yolo`` run asks the user once (item 429); the spend is recorded once the step goes ahead, whichever
question let it through, and counts against the site's day. The caps are plain numbers compared with the number on the
page, whatever its currency: set caps for sites that bill in one currency.
"""

import re
from collections.abc import Mapping
from urllib.parse import SplitResult, urlsplit

from dotagent.config import SpendCap
from dotagent.core.types import FrozenModel
from dotagent.policy.types import Amount, Decision

DAY_S: float = 86400.0
# What a payment control or a total line says: a currency sign or code before or after a number with thousands and
# decimals in either style, its thousands grouped by a point, a comma, an apostrophe ("CHF 1'234.50") or a space, a
# no-break one too ("1 234,56 €"). A number is taken whole, never a part of its digits (item 350: "$1500.00" was read
# as 150, "CHF 1'234.50" as 1).
_GROUPING: str = "[.,'\u2019\u00a0\u202f ]"
_NUMBER: str = rf"(?<![\d.,'\u2019])(?:\d{{1,3}}(?:{_GROUPING}\d{{3}})+(?:[.,]\d{{1,2}})?|\d+(?:[.,]\d{{1,2}})?)(?!\d)"
_CODES: str = r"USD|EUR|GBP|TRY|TL|CHF|JPY|CAD|AUD|SEK|NOK|DKK|PLN"
_SIGNS: str = r"[$€£₺¥]"
_AMOUNT: re.Pattern[str] = re.compile(
    rf"(?i)(?:(?P<sign>{_SIGNS})\s?(?P<a>{_NUMBER})|(?P<code>\b(?:{_CODES})\b)\s?(?P<b>{_NUMBER})"
    rf"|(?P<c>{_NUMBER})\s?(?:(?P<sign2>{_SIGNS})|(?P<code2>\b(?:{_CODES})\b)))"
)
_TOTAL_CUE: re.Pattern[str] = re.compile(r"(?i)\b(?:total|amount due|you pay|to pay|toplam|tutar|yhteens[äa])\b")
_TOTAL_WINDOW_CHARS: int = 80


class SpendState(FrozenModel):
    """A payment step as the caps see it: the site, the amount, the site's caps and what the day already holds."""

    host: str
    amount: Amount
    cap: SpendCap
    spent_today: float


def _value(digits: str) -> float:
    """The number ``digits`` holds, in either style: the last point or comma followed by one or two digits is the
    decimal mark, every other separator groups thousands."""
    match: re.Match[str] | None = re.search(r"[.,](\d{1,2})$", digits)
    if match is None:
        return float(re.sub(_GROUPING, "", digits))
    whole: str = re.sub(_GROUPING, "", digits[: match.start()])
    return float(f"{whole or '0'}.{match.group(1)}")


def find_amounts(text: str) -> tuple[Amount, ...]:
    """Every amount of money in ``text``, in order."""
    found: list[Amount] = []
    for match in _AMOUNT.finditer(text):
        digits: str = match.group("a") or match.group("b") or match.group("c")
        currency: str = (
            match.group("sign") or match.group("code") or match.group("sign2") or match.group("code2")
        ).upper()
        found.append(Amount(value=_value(digits), currency=currency))
    return tuple(found)


def _largest(amounts: tuple[Amount, ...]) -> Amount | None:
    return max(amounts, key=lambda amount: amount.value) if amounts else None


def amount_in_label(label: str) -> Amount | None:
    """The amount a control's label names ("Pay €19.90"), the largest of several ("Save $5 · Pay $500"), or ``None``."""
    return _largest(find_amounts(label))


def total_on_page(text: str) -> Amount | None:
    """The largest amount after a total cue of the page's text ("Order total: $12.00"), or ``None``: a total of
    savings or of a part of the order may stand after the cue of the order's own."""
    return _largest(
        tuple(
            amount
            for cue in _TOTAL_CUE.finditer(text)
            for amount in find_amounts(text[cue.end() : cue.end() + _TOTAL_WINDOW_CHARS])
        )
    )


def spend_host(target: str) -> str | None:
    """The site of a browser action's target address (its host without ``www.``), or ``None``."""
    parts: SplitResult = urlsplit(target)
    host: str = (parts.hostname or "").removeprefix("www.")
    if parts.scheme not in ("http", "https") or host == "":
        return None
    return host


def spend_state(
    caps: Mapping[str, SpendCap], target: str, amount: Amount | None, spent_today: float
) -> SpendState | None:
    """The state of a payment step on a listed site with a readable amount; ``None`` when the caps do not apply."""
    host: str | None = spend_host(target)
    if host is None or amount is None:
        return None
    cap: SpendCap | None = caps.get(host)
    if cap is None:
        return None
    return SpendState(host=host, amount=amount, cap=cap, spent_today=spent_today)


def render_amount(amount: Amount) -> str:
    return f"{amount.value:.2f} {amount.currency}"


def spend_allowance(state: SpendState) -> Decision | None:
    """The question the policy asks instead of the hand-off when the step is under both caps of its site; ``None`` when
    it is over one of them, so that the hand-off holds."""
    value: float = state.amount.value
    if value > state.cap.per_purchase or state.spent_today + value > state.cap.per_day:
        return None
    left: float = state.cap.per_day - state.spent_today - value
    return Decision(
        verdict="ask",
        source="spend",
        reason=(
            f"{render_amount(state.amount)} on {state.host} is within its pre-approved caps "
            f"({state.cap.per_purchase:g} per purchase, {state.cap.per_day:g} per day; {left:.2f} left today after it)"
        ),
        rule_id=None,
    )
