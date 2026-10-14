"""Which addresses the web tools may connect to: an IPv4 address written as IPv6 is judged by its IPv4 part, in each
of the forms that carry one (item 215)."""

import pytest

from dotagent.web.addresses import is_public_address

_PUBLIC: list[str] = [
    "93.184.215.14",
    "2606:2800:21f:cb07:6820:80da:af6b:8b2c",
    "::ffff:93.184.215.14",
    "64:ff9b::8.8.8.8",
]
_NOT_PUBLIC: list[str] = [
    "127.0.0.1",
    "10.0.0.1",
    "169.254.169.254",
    "::1",
    "::",
    "fe80::1",
    "::ffff:127.0.0.1",
    "::ffff:10.0.0.1",
    "::127.0.0.1",
    "::10.0.0.1",
    "::169.254.169.254",
    "64:ff9b::7f00:1",
    "64:ff9b::a00:1",
    "64:ff9b::169.254.169.254",
]


@pytest.mark.parametrize("address", _PUBLIC)
def test_an_address_of_the_internet_is_public(address: str) -> None:
    assert is_public_address(address)


@pytest.mark.parametrize("address", _NOT_PUBLIC)
def test_an_address_of_this_mac_or_a_private_network_is_not_public_in_any_form(address: str) -> None:
    assert not is_public_address(address)
