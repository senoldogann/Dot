"""``unread_or_non_public`` (item 1319): the look the web tools' pages take at an address before a request goes to it.
What is not known to be public is refused, where the look at a page already read lets an unreadable address by."""

import asyncio
import socket
from typing import Any

import pytest

from dotagent.web.addresses import non_public_address, unread_or_non_public


def _answer(*addresses: str) -> Any:
    infos = [
        (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
        for address in addresses
    ]

    async def getaddrinfo(host: str, port: object, **_: object) -> list[tuple[Any, ...]]:
        return infos

    return getaddrinfo


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "[::1]", "10.1.2.3", "192.168.0.7", "169.254.169.254", "::ffff:127.0.0.1", "0.0.0.0", "100.64.0.1"],
)
async def test_an_address_that_is_not_public_is_refused_as_written(host: str) -> None:
    why = await unread_or_non_public(host, 1.0)

    assert why is not None and "private or local network" in why


async def test_a_public_literal_is_let_by() -> None:
    assert await unread_or_non_public("93.184.216.34", 1.0) is None
    assert await unread_or_non_public("[2606:4700:4700::1111]", 1.0) is None


async def test_a_name_with_one_private_address_among_its_answers_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", _answer("93.184.216.34", "127.0.0.1"))

    why = await unread_or_non_public("rebinds.example", 1.0)

    assert why is not None and "resolves to 127.0.0.1" in why


async def test_a_name_whose_answers_are_all_public_is_let_by(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", _answer("93.184.216.34", "2606:2800:220:1::1"))

    assert await unread_or_non_public("www.example.com", 1.0) is None


async def test_a_name_that_cannot_be_resolved_is_refused_where_the_look_at_a_page_lets_it_by(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unresolved(host: str, port: object, **_: object) -> list[tuple[Any, ...]]:
        raise socket.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", unresolved)

    why = await unread_or_non_public("nowhere.invalid", 1.0)

    assert why is not None and "not known to be public" in why
    # The look at a page already read keeps its older rule: an unreadable address is not taken for a private one.
    assert await non_public_address("nowhere.invalid") is None


async def test_a_look_that_does_not_finish_in_time_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def hangs(host: str, port: object, **_: object) -> list[tuple[Any, ...]]:
        await asyncio.sleep(30)
        return []

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", hangs)

    why = await unread_or_non_public("slow.example", 0.05)

    assert why is not None and "TimeoutError" in why


async def test_a_name_with_no_answers_and_an_empty_host_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", _answer())

    assert await unread_or_non_public("empty.example", 1.0) is not None
    assert await unread_or_non_public("", 1.0) == "its address names no host"
