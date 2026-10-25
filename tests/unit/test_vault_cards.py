"""A card's number, brand, expiry and security code as Dot's vault checks them (item 621): the check digit, the brand
the first digits name, the expiry on a given day, and the words a card is named with."""

from datetime import date

import pytest

from dotagent.vault.cards import (
    BRAND_NAMES,
    Brand,
    brand_of,
    card_label,
    code_problem,
    digits_of,
    expiry_problem,
    grouped,
    last_four,
    luhn_ok,
    number_problem,
    parse_expiry,
    render_expiry,
)


def _with_check_digit(body: str) -> str:
    """``body`` followed by the digit that makes it a number Luhn's sum accepts."""
    for check in "0123456789":
        if luhn_ok(body + check):
            return body + check
    raise AssertionError(body)


@pytest.mark.parametrize(
    ("number", "brand"),
    [
        ("4242424242424242", "visa"),
        ("4012888888881881", "visa"),
        ("5555555555554444", "mastercard"),
        ("2223003122003222", "mastercard"),
        ("378282246310005", "amex"),
        ("371449635398431", "amex"),
        ("6011111111111117", "discover"),
        ("6500000000000002", "discover"),
        ("30569309025904", "diners"),
        ("36227206271667", "diners"),
        ("3530111333300000", "jcb"),
        ("6200000000000005", "unionpay"),
        ("6759649826438453", "maestro"),
        ("2200123456789019", "mir"),
        ("9792030394440796", "troy"),
        ("1234567812345670", "other"),
    ],
)
def test_the_first_digits_name_the_brand(number: str, brand: Brand) -> None:
    prefixed: str = _with_check_digit(number[:-1])
    assert brand_of(prefixed) == brand


@pytest.mark.parametrize(
    ("number", "fine"),
    [
        ("4242 4242 4242 4242", True),
        ("4242-4242-4242-4242", True),
        ("4242.4242.4242.4242", True),
        ("4242\N{NO-BREAK SPACE}4242\N{NO-BREAK SPACE}4242\N{NO-BREAK SPACE}4242", True),
        ("378282246310005", True),
        ("4242 4242 4242 4241", False),
        ("4242 4242 424", False),
        ("4242 4242 4242 42424242", False),
        ("4242 4242 4242 424a", False),
        ("", False),
        ("      ", False),
        ("4242 4242 4242 4242 4242 4242", False),
        ("0000000000000", True),
        ("424242424242", True),
        ("42424242424", False),
    ],
)
def test_a_number_is_good_when_it_has_digits_enough_and_a_check_digit_that_fits(number: str, fine: bool) -> None:
    assert (number_problem(number) is None) == fine


def test_a_number_that_does_not_fit_says_what_to_do_and_never_quotes_the_number() -> None:
    assert number_problem("4242 4242 4242 4241") == "This number does not look right: check its digits."
    assert number_problem("4242 4242 424") == "A card number has 12 to 19 digits."
    assert number_problem("4242 4242 4242 42a2") == "A card number is made of digits."
    for said in (number_problem("4242 4242 4242 4241"), number_problem("4242 4242")):
        assert said is not None and "4242" not in said


def test_the_digits_are_taken_from_the_ways_a_number_is_written() -> None:
    assert digits_of("4242 4242-4242.4242") == "4242424242424242"
    assert digits_of("4242x") is None
    assert digits_of("\N{ARABIC-INDIC DIGIT FOUR}\N{ARABIC-INDIC DIGIT TWO}") is None
    assert last_four("4242424242424242") == "4242"


def test_a_number_is_grouped_as_it_is_printed() -> None:
    assert grouped("4242424242424242", "visa") == "4242 4242 4242 4242"
    assert grouped("378282246310005", "amex") == "3782 822463 10005"
    assert grouped("30569309025904", "diners") == "3056 9309 0259 04"


@pytest.mark.parametrize(
    ("written", "month", "year"),
    [
        ("08/29", 8, 2029),
        ("8/29", 8, 2029),
        ("08 / 29", 8, 2029),
        ("08-2029", 8, 2029),
        ("0829", 8, 2029),
        ("12.31", 12, 2031),
        ("01/2030", 1, 2030),
    ],
)
def test_an_expiry_is_read_as_a_month_and_a_year(written: str, month: int, year: int) -> None:
    expiry = parse_expiry(written)
    assert expiry is not None and (expiry.month, expiry.year) == (month, year)
    assert render_expiry(expiry) == f"{month:02d}/{year % 100:02d}"


@pytest.mark.parametrize("written", ["", "13/29", "00/29", "8", "08/2", "ab/cd", "08/29/30", "2029/08"])
def test_an_expiry_that_is_no_month_and_year_is_not_read(written: str) -> None:
    assert parse_expiry(written) is None


def test_a_card_is_good_to_the_end_of_its_month_and_not_beyond_a_card_s_years() -> None:
    today: date = date(2026, 10, 24)
    assert expiry_problem("10/26", today) is None
    assert expiry_problem("09/26", today) == "This card has expired."
    assert expiry_problem("12/25", today) == "This card has expired."
    assert expiry_problem("10/52", today) == "This expiry is too far ahead to be a card's."
    assert expiry_problem("10/51", today) is None
    assert expiry_problem("soon", today) == "Write the expiry as month and year, like 08/29."


@pytest.mark.parametrize(
    ("code", "fine"), [("123", True), ("1234", True), ("12", False), ("12345", False), ("12a", False), ("", False)]
)
def test_a_security_code_has_three_or_four_digits(code: str, fine: bool) -> None:
    assert (code_problem(code) is None) == fine


def test_a_card_is_named_by_its_nickname_brand_and_last_four() -> None:
    assert card_label("Travel", "visa", "4242") == "Travel · Visa •••• 4242"
    assert card_label("", "amex", "0005") == "American Express •••• 0005"
    assert set(BRAND_NAMES) == set(Brand.__value__.__args__)
