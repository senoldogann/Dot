"""Pre-approved spending: a payment step on a listed site under its caps is a question instead of a hand-off.

``[policy.spend]`` lists sites with a cap per purchase and per day. The amount of a payment step is read from the
control's label ("Pay €19.90") or, failing that, from the total on the page ("Order total: $12.00"); a step whose
amount cannot be read keeps the hand-off. Under both caps the policy asks instead of handing the step to the user, so
a ``yolo`` run goes on, an ``auto`` run lets the review model answer, and an ``ask`` run asks once; the spend is then
recorded and counts against the site's day. The caps are plain numbers compared with the number on the page, whatever
its currency: set caps for sites that bill in one currency.
"""

import re
from collections.abc import Mapping
from urllib.parse import SplitResult, urlsplit

from dotagent.config import SpendCap
from dotagent.core.types import FrozenModel
from dotagent.policy.types import Amount, Decision

DAY_S: float = 86400.0
# What a payment control or a total line says: a currency sign or code before or after a number with thousands and
# decimals in either style.
_NUMBER: str = r"\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
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
    """The number ``digits`` holds, in either style: the last separator followed by one or two digits is the decimal
    mark, every other separator groups thousands."""
    match: re.Match[str] | None = re.search(r"[.,](\d{1,2})$", digits)
    if match is None:
        return float(re.sub(r"[.,]", "", digits))
    whole: str = re.sub(r"[.,]", "", digits[: match.start()])
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


def amount_in_label(label: str) -> Amount | None:
    """The amount a control's label names ("Pay €19.90"), or ``None``."""
    amounts: tuple[Amount, ...] = find_amounts(label)
    return amounts[0] if amounts else None


def total_on_page(text: str) -> Amount | None:
    """The amount after the last total cue of the page's text ("Order total: $12.00"), or ``None``."""
    cues: list[re.Match[str]] = list(_TOTAL_CUE.finditer(text))
    for cue in reversed(cues):
        amounts: tuple[Amount, ...] = find_amounts(text[cue.end() : cue.end() + _TOTAL_WINDOW_CHARS])
        if amounts:
            return amounts[0]
    return None


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
            f"{render_amount(state.amount)} on {state.host} is within its pre-approved caps ({state.cap.per_purchase:g} "
            f"per purchase, {state.cap.per_day:g} per day; {left:.2f} left today after it)"
        ),
        rule_id=None,
    )
