"""The owner's 1Password (item 493): which pages a login is filled on, what the browser tools' results mask (a value as
typed, as an address writes it and inside a JSON string), and what
the policy makes of a fill: asked in every mode but yolo whatever the rules say, a rule's hand-off alone stricter, and
answered by the owner, not the review model; the shell never runs 1Password's command line."""

import time
from pathlib import Path

import pytest
from playwright.async_api import Error as PlaywrightError

from dotagent.browser.session import BrowserSession, no_runs_at_work, sending_document
from dotagent.browser.types import HeldValue
from dotagent.config import load_config, load_default_config
from dotagent.core.errors import BrowserError, ForbiddenUrlError
from dotagent.policy.engine import decide, route_for
from dotagent.policy.shell import assess_shell
from dotagent.policy.types import ActionInfo
from dotagent.store.models import Behavior, RuleRecord
from dotagent.vault.hosts import SameOrigin, fills_on, website_hosts
from dotagent.vault.masking import MASK, in_address, mask_error, masked


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


def test_a_value_is_masked_with_the_word_it_is_given_and_an_error_names_it_in_every_place_it_says_it() -> None:
    """The cookies a script ran with are hidden as the password manager's values are, under their own word (item 533):
    in the text of an error, in its context and in the address a refused one names."""
    assert masked("session=tok/en+value==12345", ["tok/en+value==12345"], "[hidden]") == "session=[hidden]"
    refused: ForbiddenUrlError = ForbiddenUrlError(
        "the address may not be opened", "http://localhost:1/?c=tok%2Fen%2Bvalue%3D%3D12345", "blocked", "document"
    )
    mask_error(refused, ["tok/en+value==12345"], "[hidden]")
    assert "tok" not in refused.message and refused.url == "http://localhost:1/?c=[hidden]"
    assert refused.context["url"] == "http://localhost:1/?c=[hidden]" and "12345" not in str(refused)
    plain: BrowserError = BrowserError("it said tok/en+value==12345", {"nested": {"list": ["tok/en+value==12345"]}})
    mask_error(plain, ["tok/en+value==12345"])
    assert str(plain) == f'it said {MASK} | context={{"nested": {{"list": ["{MASK}"]}}}}'


def _session(tmp_path: Path) -> BrowserSession:
    """A session that starts nothing: Chrome is launched when a tool first needs it."""
    settings: Path = tmp_path / "config.toml"
    settings.write_text("[browser]\nheadless = true\n", encoding="utf-8")
    return BrowserSession(
        load_config(settings).browser, load_default_config().server, tmp_path / "p", tmp_path / "d", no_runs_at_work
    )


def test_what_a_chat_holds_back_is_taken_out_of_every_text_error_and_address_the_session_gives(tmp_path: Path) -> None:
    """One place masks the password manager's values and the cookies a script ran with (items 493 and 533), under their
    own words: a result, an error, the address a log line or a refusal names, and what a judge reads."""
    session: BrowserSession = _session(tmp_path)
    session._vault_values["chat"] = {"S3cret-Passw0rd"}
    session._cookie_values["chat"] = {"abcdef0123456789"}
    assert session.holds_back("chat") and not session.holds_back("other")
    assert session.held_back("chat", "pass S3cret-Passw0rd, session abcdef0123456789") == (
        f"pass {MASK}, session [hidden]"
    )
    assert session.held_back("other", "session abcdef0123456789") == "session abcdef0123456789"
    error: BrowserError = BrowserError("saw abcdef0123456789", {"url": "https://x.test/?c=abcdef0123456789"})
    session.mask_held("chat", error)
    assert str(error) == 'saw [hidden] | context={"url": "https://x.test/?c=[hidden]"}'
    # The log names an address without the cookies of any chat.
    assert session._shown("https://x.test/?c=abcdef0123456789") == "https://x.test/?c=[hidden]"


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
        ("https://example.com/x?p=%2561%2562%2563%2564", True),
        ("https://example.com/x?p=48291", False),
    ],
)
def test_an_address_carries_a_value_however_its_escapes_write_it(address: str, carries: bool) -> None:
    assert in_address(address, ["Pässwort1", "pa%s word", "482913", "abcd"]) == carries


def test_an_address_that_carries_a_value_is_named_by_its_host_its_scheme_or_alone() -> None:
    live: tuple[str, ...] = ("secret-value",)
    assert BrowserSession._named("https://example.com/x?p=secret-value", live, True) == (
        "https://example.com/[an address that carries a filled value]"
    )
    assert BrowserSession._named("https://secret-value.example/x", live, True) == (
        "https://[a host that carries a filled value]"
    )
    assert BrowserSession._named("http://[secret-value/x", live, True) == "[an address that carries a filled value]"
    assert BrowserSession._named("https://example.com/x", live, False) == "https://example.com/x"


class _Shown:
    """A frame as a request names it: the address it shows now and the frame it lies in."""

    def __init__(self, url: str, parent: "_Shown | None" = None) -> None:
        self.url: str = url
        self.parent_frame: _Shown | None = parent


class _Sent:
    """A request, whose frame is the browser's to name: a service worker's has none, and nor has the first request of a
    popup (Playwright raises for both)."""

    def __init__(self, frame: _Shown | None) -> None:
        self._frame: _Shown | None = frame

    @property
    def frame(self) -> _Shown:
        if self._frame is None:
            raise PlaywrightError("Frame for this navigation request is not available")
        return self._frame


def test_the_document_that_sent_a_request_is_the_one_its_frame_shows_and_none_for_a_request_of_no_frame() -> None:
    shop: _Shown = _Shown("https://shop.test/pay")
    assert sending_document(_Sent(shop)) == "https://shop.test/pay"  # type: ignore[arg-type]
    assert sending_document(_Sent(_Shown("about:blank", shop))) == "https://shop.test/pay"  # type: ignore[arg-type]
    assert sending_document(_Sent(_Shown("about:blank"))) is None  # type: ignore[arg-type]
    assert sending_document(_Sent(None)) is None  # type: ignore[arg-type]


def test_what_no_request_may_carry_is_looked_for_in_the_requests_a_page_sends_from_the_origin_it_was_typed_into(
    tmp_path: Path,
) -> None:
    """A page of another origin, in any tab, that tried addresses and saw which were refused would learn a weak
    password: no request of its documents is held to a value it was not given. A request that no document can be named
    for is not either, and every request is under the rules of the URLs whoever sent it."""
    session: BrowserSession = _session(tmp_path)
    forever: float = float("inf")
    shop: SameOrigin | None = SameOrigin.of("https://shop.test/signin")
    github: SameOrigin | None = SameOrigin.of("https://github.com/login")
    assert shop is not None and github is not None
    session._vault_secrets[HeldValue("Summer2024!", shop)] = forever
    session._vault_secrets[HeldValue("Wint3r-Passw0rd", github)] = forever
    session._vault_secrets[HeldValue("482913", shop)] = time.monotonic() - 1

    def judged(address: str, sender: str | None, navigation: bool = False) -> bool:
        page = object() if navigation else None
        return session._judge(address, navigation, "document" if navigation else "fetch", page, sender).allowed  # type: ignore[arg-type]

    signin: str = "https://shop.test/signin"
    assert not judged("https://x.test/p?w=Summer2024%21", signin)
    assert not judged("https://x.test/p?w=Summer2024%21", "https://shop.test/other?id=1")
    assert not judged("https://x.test/p?w=Wint3r-Passw0rd", "https://github.com/settings")
    for sender in (
        "https://evil.test/a",
        "https://shop.test:8443/pay",
        "http://shop.test/pay",
        "https://sub.shop.test/pay",
        "about:blank",
        "data:text/html,x",
        "chrome-error://chromewebdata/",
        None,
    ):
        assert judged("https://x.test/p?w=Summer2024%21", sender), sender
        assert judged("https://x.test/p?w=Wint3r-Passw0rd", sender), sender
    # Each value is the business of the documents of its own origin.
    assert judged("https://x.test/p?w=Summer2024%21", "https://github.com/settings")
    assert judged("https://x.test/p?w=Wint3r-Passw0rd", signin)
    # A value whose minutes are over is kept out of no address.
    assert judged("https://x.test/p?c=482913", signin)
    # The rules on addresses hold for every sender: Dot's own port is no one's to reach.
    own: str = f"http://127.0.0.1:{session._server.port}/x"
    assert not judged(own, None) and not judged(own, "https://evil.test/a")
    # Dot's words for an address name it without the value of any origin: they are read by no page.
    assert (
        session._shown("https://x.test/p?w=Summer2024%21") == "https://x.test/[an address that carries a filled value]"
    )


def test_a_navigation_of_a_tabs_main_frame_is_the_runs_own_and_is_judged_by_no_value_filled(tmp_path: Path) -> None:
    """The run opens an address, clicks a link, types into the form a page then sends: a refusal by a value of its
    navigation would tell it whether a guess is the password (22 calls from a list of 1,640 words found one). What it
    asks for is judged by the rules on addresses alone; the fetches and the frames of a page are judged by the value."""
    session: BrowserSession = _session(tmp_path)
    shop: SameOrigin | None = SameOrigin.of("https://shop.test/signin")
    assert shop is not None
    session._vault_secrets[HeldValue("Summer2024!", shop)] = float("inf")
    signin: str = "https://shop.test/signin"
    address: str = "https://shop.test/search?q=Winter1990;Summer2024!;Autumn1999"
    # A navigation of the main frame, from the page the password was typed into: the run's own.
    assert session._judge(address, True, "document", object(), signin).allowed  # type: ignore[arg-type]
    assert session._judge(address, True, "document", object(), None).allowed  # type: ignore[arg-type]
    # The same address as a fetch or a frame of that page is refused, the page's own doing.
    assert not session._judge(address, False, "fetch", None, signin).allowed
    assert not session._judge(address, True, "document", None, signin).allowed, "a frame of the page"
    # The rules on addresses still judge it.
    own: str = f"http://127.0.0.1:{session._server.port}/x"
    assert not session._judge(own, True, "document", object(), signin).allowed  # type: ignore[arg-type]
