"""A card's number, expiry and security code, checked as Dot's vault keeps them (item 621): the number's digits and
check digit (Luhn), the brand its first digits name, the expiry as ``MM/YY`` and whether it is still good. The page
checks the same things as the owner types (``static/js/vaultcard.js``); the daemon is the authority and checks again
when a card is saved. Nothing here reads or writes a Keychain or a file."""

import re
from datetime import date
from typing import Literal

from dotagent.core.types import FrozenModel

type Brand = Literal[
    "visa", "mastercard", "amex", "discover", "diners", "jcb", "unionpay", "maestro", "mir", "troy", "other"
]

BRAND_NAMES: dict[Brand, str] = {
    "visa": "Visa",
    "mastercard": "Mastercard",
    "amex": "American Express",
    "discover": "Discover",
    "diners": "Diners Club",
    "jcb": "JCB",
    "unionpay": "UnionPay",
    "maestro": "Maestro",
    "mir": "Mir",
    "troy": "Troy",
    "other": "Card",
}

# A card number has this many digits at the least and at the most (ISO/IEC 7812).
MIN_DIGITS: int = 12
MAX_DIGITS: int = 19
# The characters a card number is written with between its groups.
_SEPARATORS: str = " \N{NO-BREAK SPACE}\N{NARROW NO-BREAK SPACE}-."
# The digits a security code has.
_CODE_DIGITS: re.Pattern[str] = re.compile(r"[0-9]{3,4}")
# An expiry as it is written: 12/27, 12 / 27, 12-2027, 1227.
_EXPIRY: re.Pattern[str] = re.compile(r"\s*(0?[1-9]|1[0-2])\s*[/\-.]?\s*([0-9]{2}|[0-9]{4})\s*")
# How many years ahead an expiry may be.
_MOST_YEARS_AHEAD: int = 25
_CENTURY: int = 2000
_BRAND_PREFIXES: tuple[tuple[Brand, tuple[tuple[int, int], ...], int], ...] = (
    # (brand, ranges of the first digits, how many first digits the ranges are about): the first that fits names it.
    ("mir", ((2200, 2204),), 4),
    ("mastercard", ((51, 55),), 2),
    ("mastercard", ((2221, 2720),), 4),
    ("amex", ((34, 34), (37, 37)), 2),
    ("diners", ((300, 305), (309, 309)), 3),
    ("diners", ((36, 36), (38, 39)), 2),
    ("jcb", ((3528, 3589),), 4),
    ("discover", ((622126, 622925),), 6),
    ("discover", ((6011, 6011), (644, 649)), 4),
    ("discover", ((644, 649),), 3),
    ("discover", ((65, 65),), 2),
    ("unionpay", ((62, 62),), 2),
    ("troy", ((9792, 9792),), 4),
    ("maestro", ((6304, 6304), (6759, 6759), (6761, 6763)), 4),
    ("maestro", ((50, 50), (56, 58)), 2),
    ("visa", ((4, 4),), 1),
)


class Expiry(FrozenModel):
    """A month and a year (four digits) at the end of which a card stops working."""

    month: int
    year: int


def digits_of(text: str) -> str | None:
    """The digits of a card number written with spaces, hyphens or points between its groups; ``None`` when anything
    else is in it."""
    squeezed: str = "".join(char for char in text if char not in _SEPARATORS)
    return squeezed if squeezed.isascii() and squeezed.isdigit() else None


def luhn_ok(digits: str) -> bool:
    """Whether the last digit of ``digits`` is the check digit of the others (Luhn's sum)."""
    total: int = 0
    for position, char in enumerate(reversed(digits)):
        value: int = int(char)
        if position % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def brand_of(digits: str) -> Brand:
    """The brand the first digits of a card number name; ``other`` when they name none."""
    for brand, ranges, length in _BRAND_PREFIXES:
        if len(digits) < length:
            continue
        head: int = int(digits[:length])
        if any(low <= head <= high for low, high in ranges):
            return brand
    return "other"


def number_problem(text: str) -> str | None:
    """What is wrong with a card number as written, in the words the page shows; ``None`` when it can be a card's."""
    digits: str | None = digits_of(text)
    if digits is None or digits == "":
        return "A card number is made of digits."
    if not MIN_DIGITS <= len(digits) <= MAX_DIGITS:
        return f"A card number has {MIN_DIGITS} to {MAX_DIGITS} digits."
    if not luhn_ok(digits):
        return "This number does not look right: check its digits."
    return None


def last_four(digits: str) -> str:
    return digits[-4:]


def grouped(digits: str, brand: Brand) -> str:
    """The number as it is printed on a card: American Express in 4-6-5, every other in groups of four."""
    if brand == "amex" and len(digits) == 15:
        return f"{digits[:4]} {digits[4:10]} {digits[10:]}"
    return " ".join(digits[start : start + 4] for start in range(0, len(digits), 4))


def parse_expiry(text: str) -> Expiry | None:
    """The month and year of an expiry written ``12/27``, ``12 / 2027``, ``1227`` or ``12-27``; ``None`` for anything
    else."""
    found: re.Match[str] | None = _EXPIRY.fullmatch(text)
    if found is None:
        return None
    year: int = int(found.group(2))
    return Expiry(month=int(found.group(1)), year=year if year >= _CENTURY else _CENTURY + year)


def expiry_problem(text: str, today: date) -> str | None:
    """What is wrong with an expiry as written on ``today``: not a month and a year, one that has passed (a card is good
    to the end of its month) or one too far ahead to be a card's; ``None`` when it is good."""
    expiry: Expiry | None = parse_expiry(text)
    if expiry is None:
        return "Write the expiry as month and year, like 08/29."
    if (expiry.year, expiry.month) < (today.year, today.month):
        return "This card has expired."
    if expiry.year > today.year + _MOST_YEARS_AHEAD:
        return "This expiry is too far ahead to be a card's."
    return None


def render_expiry(expiry: Expiry) -> str:
    """The expiry as ``MM/YY``, which a payment form takes."""
    return f"{expiry.month:02d}/{expiry.year % 100:02d}"


def code_problem(text: str) -> str | None:
    """What is wrong with a security code: it has three or four digits (four on the front of an American Express)."""
    return None if _CODE_DIGITS.fullmatch(text) is not None else "A security code has 3 or 4 digits."


def card_label(nickname: str, brand: Brand, last4: str) -> str:
    """How a card is named for a list and for a run: ``Travel · Visa •••• 4242``, without the nickname when it has
    none."""
    name: str = f"{BRAND_NAMES[brand]} •••• {last4}"
    return f"{nickname} · {name}" if nickname else name
