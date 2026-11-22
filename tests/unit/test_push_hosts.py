"""The push services Dot posts web pushes to (item 1311): a subscription's endpoint is taken only on a push service of
a browser's maker, and one stored at another host is not posted to. Dot's own packaged list is what these read."""

import pytest

from dotagent.config import NotifyConfig, load_defaults
from dotagent.notify.hosts import push_host_problem

_DEFAULT_HOSTS: list[str] = NotifyConfig.model_validate(load_defaults()["notify"]).push_hosts


@pytest.mark.parametrize(
    "endpoint",
    [
        # What an iPhone's home-screen app and Safari send.
        "https://web.push.apple.com/QOZrrf1y5nJHNyc0rEFQbMk",
        "https://api.push.apple.com/3/device/abc",
        # Chrome, Edge, Brave, Opera and Samsung's browser, on Firebase's service.
        "https://fcm.googleapis.com/fcm/send/dUmmy:APA91bE",
        "https://fcm.googleapis.com/wp/dummy",
        "https://android.googleapis.com/fcm/send/dummy",
        # Firefox.
        "https://updates.push.services.mozilla.com/wpush/v2/gAAAAAB",
        "https://updates-autopush.stage.push.services.mozilla.com/wpush/v2/x",
        # Edge on Windows.
        "https://wns2-par02p.notify.windows.com/w/?token=BQYAAAD",
        "https://WEB.PUSH.APPLE.COM./x",
    ],
)
def test_a_push_service_of_a_browsers_maker_is_taken(endpoint: str) -> None:
    assert push_host_problem(endpoint, _DEFAULT_HOSTS) is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://attacker.example/x",
        "https://web.push.apple.com.attacker.example/x",
        "https://evilpush.apple.com/x",
        "https://push.apple.com/x",
        "https://notify.windows.com/x",
        "https://storage.googleapis.com/a-bucket-anyone-owns/x",
        "https://storage.googleapis.com/fcm/x-in-a-bucket-named-fcm",
        "https://a-bucket-anyone-owns.storage.googleapis.com/fcm/send/x",
        "https://a-bucket-anyone-owns.storage.googleapis.com/x",
        "https://storage.googleapis.com/%66cm/x",
        "https://x.googleapis.com/fcm/send/x",
        "https://x.googleapis.com/fcm/../a-bucket/x",
        "https://x.googleapis.com/fcm/%2e%2e/a-bucket/x",
        "https://web.push.apple.com@attacker.example/x",
        "https://attacker.example\\@web.push.apple.com/x",
        "https://attacker.example#@web.push.apple.com/x",
        "https://web.push.apple.com:notaport/x",
        "https://127.0.0.1/x",
        "https:///x",
        "http://web.push.apple.com/x",
        "https://web.push.apple.com/x y",
    ],
)
def test_any_other_host_is_refused_with_why(endpoint: str) -> None:
    problem: str | None = push_host_problem(endpoint, _DEFAULT_HOSTS)
    assert problem is not None and problem != ""


def test_a_host_the_owner_adds_to_the_list_is_taken_and_the_message_names_the_host() -> None:
    assert push_host_problem("https://push.example/a", _DEFAULT_HOSTS) == (
        "push.example is not a push service Dot sends to (Apple, Google Chrome, Firefox or Windows)"
    )
    assert push_host_problem("https://push.example/a", [*_DEFAULT_HOSTS, "push.example"]) is None
    assert push_host_problem("https://a.b.push.example/a", [*_DEFAULT_HOSTS, "*.push.example"]) is None
    assert push_host_problem("https://push.example/a", [*_DEFAULT_HOSTS, "*.push.example"]) is not None


def test_the_packaged_list_names_the_services_of_the_three_browser_engines() -> None:
    assert _DEFAULT_HOSTS == [
        "web.push.apple.com",
        "*.push.apple.com",
        "fcm.googleapis.com",
        "android.googleapis.com",
        "updates.push.services.mozilla.com",
        "*.push.services.mozilla.com",
        "*.notify.windows.com",
    ]


def test_no_packaged_entry_takes_a_domain_that_serves_what_anyone_hosts() -> None:
    """``*.googleapis.com`` (with or without a path) takes ``<bucket>.storage.googleapis.com``, whose bucket the caller
    names, so Google's push hosts are listed one by one."""
    assert [entry for entry in _DEFAULT_HOSTS if "googleapis.com" in entry and not entry.startswith("*.")] == [
        "fcm.googleapis.com",
        "android.googleapis.com",
    ]
    assert not [entry for entry in _DEFAULT_HOSTS if entry.startswith("*.") and "googleapis" in entry]


def test_an_entry_with_a_path_takes_only_that_path() -> None:
    hosts: list[str] = ["push.example/wp/"]
    assert push_host_problem("https://push.example/wp/abc", hosts) is None
    assert push_host_problem("https://push.example/other/abc", hosts) is not None
    assert push_host_problem("https://push.example/%77p/abc", hosts) is None


@pytest.mark.parametrize("entry", ["", "*", "https://web.push.apple.com", "a*.example", "x.example:443", "a b.example"])
def test_an_entry_that_is_not_a_host_is_refused_where_the_config_is_read(entry: str) -> None:
    with pytest.raises(ValueError, match="push_hosts"):
        NotifyConfig(push_hosts=[entry])
