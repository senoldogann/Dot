"""The owner's 1Password (item 493): which pages a login is filled on, what the browser tools' results mask (a value as
typed, as an address writes it and inside a JSON string), and what
the policy makes of a fill: asked in every mode but yolo whatever the rules say, a rule's hand-off alone stricter, and
answered by the owner, not the review model; the shell never runs 1Password's command line."""

from pathlib import Path

import pytest

from dotagent.policy.engine import decide, route_for
from dotagent.policy.shell import assess_shell
from dotagent.policy.types import ActionInfo
from dotagent.store.models import Behavior, RuleRecord
from dotagent.vault.hosts import fills_on, website_hosts
from dotagent.vault.masking import MASK, in_address, masked


@pytest.mark.parametrize(
    ("url", "websites", "fills"),
    [
        ("https://github.com/login", ("https://github.com",), True),
        ("https://gist.github.com/auth", ("github.com",), True),
        ("https://www.github.com/login", ("https://github.com/login",), True),
        ("https://github.com.evil.example/login", ("https://github.com",), False),
        ("https://evilgithub.com/login", ("https://github.com",), False),
        ("http://github.com/login", ("https://github.com",), False),
        ("http://127.0.0.1:8000/login", ("http://127.0.0.1:8000/login",), True),
        ("http://127.0.0.1:9000/login", ("http://127.0.0.1:8000/login",), False),
        ("http://localhost:3000/", ("http://localhost",), False),
        ("https://github.com:8443/login", ("https://github.com",), False),
        ("https://example.com:8443/a", ("example.com:8443",), True),
        ("https://example.com/login", ("http://example.com",), True),
        ("https://example.com/login", ("https://example.com:port",), False),
        ("https://example.com", ("com",), False),
        ("about:blank", ("https://github.com",), False),
        ("https://github.com", (), False),
    ],
)
def test_a_login_is_filled_on_its_own_sites_alone(url: str, websites: tuple[str, ...], fills: bool) -> None:
    assert fills_on(url, websites) == fills


def test_the_hosts_of_a_login_are_named_once_without_www() -> None:
    assert website_hosts(("https://www.GitHub.com/login", "github.com", "https://gist.github.com")) == (
        "github.com",
        "gist.github.com",
    )


def test_the_values_filled_are_masked_the_longest_first_and_short_ones_left() -> None:
    text: str = "user ada@example.com pass S3cret and S3cret-Passw0rd code 482913 at 12"
    assert masked(text, ["S3cret", "S3cret-Passw0rd", "482913", "12"]) == (
        f"user ada@example.com pass {MASK} and {MASK} code {MASK} at 12"
    )


def _fill() -> ActionInfo:
    return ActionInfo(
        tool="browser_fill_login",
        kind="browser_input",
        risk="high",
        summary="Fill your login GitHub from 1Password into this page: password",
        target="https://github.com/login",
        flags=("vault_fill",),
    )


def _rule(behavior: Behavior) -> RuleRecord:
    return RuleRecord(
        id="rule-1",
        position=1,
        enabled=True,
        behavior=behavior,
        tool_glob="browser_fill_login",
        match_regex=".*",
        description="logins",
        created_at=0.0,
    )


def test_a_fill_is_asked_whatever_the_rules_say_and_answered_by_the_owner() -> None:
    asked = decide(_fill(), "task", False, "aggressive", [], None)
    assert (asked.verdict, asked.source) == ("ask", "vault")
    allowed = decide(_fill(), "task", False, "aggressive", [_rule("allow")], None)
    assert (allowed.verdict, allowed.source) == ("ask", "vault")
    handed = decide(_fill(), "task", False, "aggressive", [_rule("handoff")], None)
    assert handed.verdict == "handoff"
    assert route_for(asked, "auto") == "user"
    assert route_for(asked, "ask") == "user"
    assert route_for(asked, "yolo") == "waived"


def test_the_shell_never_runs_1passwords_command_line() -> None:
    home: Path = Path.home()
    for command in ("op read op://Shared/GitHub/password", "/opt/homebrew/bin/op item list", "sh -c 'op vault list'"):
        assert "secret_access" in assess_shell(command, home, home, (), ()).flags, command


def test_a_value_is_masked_as_an_address_and_a_json_string_write_it_too() -> None:
    value: str = 'S3cret Passw0rd!"'
    text: str = 'at /home?pass=S3cret+Passw0rd%21%22 and /x/S3cret%20Passw0rd%21%22 and {"v": "S3cret Passw0rd!\\""}'
    assert masked(text, [value]) == f'at /home?pass={MASK} and /x/{MASK} and {{"v": "{MASK}"}}'
    # A browser writes a form's field with "*" kept and "~" encoded; a script's encodeURIComponent keeps both.
    starred: str = "S3cret*Pass~w0rd!"
    assert (
        masked("/home?pass=S3cret*Pass%7Ew0rd%21 /s?q=S3cret*Pass~w0rd!", [starred]) == f"/home?pass={MASK} /s?q={MASK}"
    )


@pytest.mark.parametrize(
    ("address", "carries"),
    [
        ("https://example.com/login?p=P%E4sswort1", True),
        ("https://example.com/login?p=P%C3%A4sswort1", True),
        ("https://example.com/x?p=pa%s%20word", True),
        ("https://example.com/x?p=pa%25s+word", True),
        ("https://example.com/x?p=pa%2525s%2520word", True),
        ("https://example.com/x?p=482913", True),
        ("https://example.com/x?p=48291", False),
    ],
)
def test_an_address_carries_a_value_however_its_escapes_write_it(address: str, carries: bool) -> None:
    assert in_address(address, ["Pässwort1", "pa%s word", "482913"]) == carries
