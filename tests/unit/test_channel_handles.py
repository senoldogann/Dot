"""The numbers and addresses paired with the iMessage channel (item 572): how a typed one is written as Messages names
a chat, and how a recipient of a draft is told to be one of them."""

import pytest

from dotagent.channels.handles import names_a_handle, normalize_handle
from dotagent.core.errors import ChannelError

PHONE: str = "+358401234567"
ADDRESS: str = "me@example.com"
PAIRED: tuple[str, ...] = (PHONE, ADDRESS)


@pytest.mark.parametrize(
    ("typed", "handle"),
    [
        ("+358 40 123 4567", PHONE),
        ("+358-40-123-4567", PHONE),
        ("+1 (415) 555-0100", "+14155550100"),
        ("  Me@Example.COM ", ADDRESS),
    ],
)
def test_a_handle_is_named_as_messages_names_a_chat(typed: str, handle: str) -> None:
    assert normalize_handle(typed) == handle


@pytest.mark.parametrize("typed", ["040 123 4567", "0401234567", "12345", "me@example", "not a handle", "+0123456789"])
def test_a_number_without_its_country_code_or_a_word_is_no_handle(typed: str) -> None:
    with pytest.raises(ChannelError):
        normalize_handle(typed)


@pytest.mark.parametrize(
    "recipient",
    [
        PHONE,
        " +358401234567 ",
        "+358 40 123 4567",
        "+358-40-123-4567",
        "040 123 4567",
        "0401234567",
        "(040) 123-4567",
        "00358401234567",
        "358401234567",
        ADDRESS,
        "ME@Example.com",
        " me@example.com ",
    ],
)
def test_a_recipient_that_names_a_paired_handle_is_told_whatever_way_it_is_written(recipient: str) -> None:
    assert names_a_handle(recipient, PAIRED)


@pytest.mark.parametrize(
    "recipient",
    [
        "+358401234568",
        "+358 40 123 4560",
        "+49 40 123 4567",
        "+4915112345678",
        "040 123 4568",
        "123456",
        "4567",
        "0",
        "",
        "other@example.com",
        "me@example.com.tr",
        "xme@example.com",
        "me@example.org",
    ],
)
def test_a_recipient_that_names_someone_else_is_not(recipient: str) -> None:
    assert not names_a_handle(recipient, PAIRED)


def test_nothing_is_paired_nobody_is_named() -> None:
    assert not names_a_handle(PHONE, ())
    assert not names_a_handle(ADDRESS, ())


def test_an_address_never_names_a_number_and_a_number_never_names_an_address() -> None:
    assert not names_a_handle(PHONE, (ADDRESS,))
    assert not names_a_handle(ADDRESS, (PHONE,))
