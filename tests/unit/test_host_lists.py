"""The host lists (``[browser] allowed_hosts`` and ``blocked_hosts``) are read the way a browser reads a host, entries
and hosts alike (item 554): an entry the owner wrote in Unicode, in punycode, with the dot of an absolute name or as a
numeric address names the same host as any other spelling of it."""

import pytest

from dotagent.browser.safety import UrlVerdict, check_url, host_matches


@pytest.mark.parametrize("entry", ["bücher.de", "xn--bcher-kva.de", "BÜCHER.de", ".bücher.de"])
@pytest.mark.parametrize("url", ["https://bücher.de/x", "https://xn--bcher-kva.de/x", "https://shop.bücher.de/"])
def test_a_blocked_international_name_blocks_every_spelling_of_it(entry: str, url: str) -> None:
    verdict: UrlVerdict = check_url(url, (), [entry])
    assert not verdict.allowed and "blocked list" in verdict.reason, verdict


@pytest.mark.parametrize("entry", ["bücher.de", "xn--bcher-kva.de"])
def test_an_allowed_international_name_lets_its_site_in_and_nothing_else(entry: str) -> None:
    assert check_url("https://bücher.de/x", [entry], ()).allowed
    assert check_url("https://xn--bcher-kva.de/x", [entry], ()).allowed
    assert not check_url("https://example.com/", [entry], ()).allowed


@pytest.mark.parametrize("entry", ["evil.com.", ".evil.com.", "EVIL.com"])
def test_a_blocked_name_written_as_an_absolute_host_blocks_the_host_with_or_without_its_dot(entry: str) -> None:
    for url in ("https://evil.com/x", "https://evil.com./x", "https://www.evil.com/x"):
        assert not check_url(url, (), [entry]).allowed, (entry, url)
    assert check_url("https://notevil.com/x", (), [entry]).allowed


@pytest.mark.parametrize("entry", ["2130706433", "0x7f.1", "127.1", "127.0.0.1"])
def test_a_blocked_address_blocks_every_numeric_spelling_of_it(entry: str) -> None:
    for url in ("https://127.0.0.1/x", "https://2130706433/x", "https://0x7f.0.0.1/x", "https://127.1/x"):
        assert not check_url(url, (), [entry]).allowed, (entry, url)
    assert check_url("https://127.0.0.2/x", (), [entry]).allowed


def test_a_plain_entry_matches_its_subdomains_and_not_a_longer_name() -> None:
    assert host_matches("shop.example.com", ["example.com"])
    assert host_matches("example.com", ["example.com"])
    assert not host_matches("badexample.com", ["example.com"])
    assert not host_matches("example.com.evil.net", ["example.com"])


def test_an_entry_that_cannot_be_read_as_a_host_names_none_and_an_empty_one_names_every_host_none() -> None:
    assert not host_matches("example.com", ["a·\ud800b.com"])
    assert not host_matches("example.com", ["", "."])
    assert not host_matches("", [""])
