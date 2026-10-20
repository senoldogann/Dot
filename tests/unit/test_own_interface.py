"""The browser never opens Dot's own interface (item 135): this Mac on the daemon's port in any spelling a browser
reads, a dot at the end of the host included (item 140), or a name of ``[server] reach``. A server of this Mac on
another port and the web stay open."""

import pytest

from dotagent.browser.safety import UrlVerdict, check_browser_url, forbidden_target, request_verdict
from dotagent.config import AppConfig, ServerConfig, load_default_config
from dotagent.core.errors import ToolInputError
from dotagent.tools.browser import checked_url

SERVER: ServerConfig = ServerConfig(host="127.0.0.1", port=8765, reach=["mac.tail1234.ts.net"], keep_awake=False)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765/",
        "http://localhost:8765/#/approvals",
        "http://LOCALHOST:8765/",
        "http://app.localhost:8765/",
        "http://[::1]:8765/api/approvals",
        "http://[::ffff:127.0.0.1]:8765/",
        "http://127.1:8765/",
        "http://2130706433:8765/",
        "http://0x7f.0.0.1:8765/",
        "http://0.0.0.0:8765/",
        "http://127.0.0.1.:8765/",
        "http://localhost.:8765/",
        "https://mac.tail1234.ts.net/#/settings",
        "https://mac.tail1234.ts.net./",
        # The other dots of Unicode and full-width digits, which a browser reads as IDNA does, and percent escapes in a
        # host, which it decodes first (the code review's Y1).
        "http://127\u30020\u30020\u30021:8765/",
        "http://\uff11\uff12\uff17.0.0.1:8765/",
        "http://127%2e0%2e0%2e1:8765/",
        "http://%31%32%37.0.0.1:8765/",
        "http://localhost%2E:8765/",
    ],
)
def test_dots_own_interface_is_never_opened(url: str) -> None:
    verdict: UrlVerdict = check_browser_url(url, (), (), SERVER)
    assert not verdict.allowed and "Dot's own interface" in verdict.reason, verdict


@pytest.mark.parametrize(
    "raw",
    ["127.0.0.1:8765", "localhost:8765/#/approvals", "\uff11\uff12\uff17.0.0.1:8765", "http://127%2e0%2e0%2e1:8765/"],
)
def test_the_address_checked_url_gives_the_browser_is_the_one_it_checked(raw: str) -> None:
    """The code review's Y1: what the model wrote is made a whole address first (a bare host gets its scheme), and
    that address, the one the browser is given, is the one checked."""
    config: AppConfig = load_default_config()
    with pytest.raises(ToolInputError, match="Dot's own interface"):
        checked_url(raw, config)
    assert checked_url("example.com", config) == "https://example.com"


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:3000/", "http://localhost:5173/", "https://example.com/", "about:blank"]
)
def test_a_server_of_this_mac_on_another_port_and_the_web_stay_open(url: str) -> None:
    assert check_browser_url(url, (), (), SERVER).allowed
    assert forbidden_target(url, (), SERVER).allowed


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:8765/", "https://mac.tail1234.ts.net/api/approvals", "http://127.1:8765/x.png"]
)
def test_no_request_of_any_kind_reaches_dots_own_interface(url: str) -> None:
    """A resource a page loads, a fetch or a worker's request is refused like a page (item 135): what a page of another
    site would use to read or drive the interface."""
    assert not forbidden_target(url, (), SERVER).allowed
    for navigation in (True, False):
        verdict: UrlVerdict = request_verdict(url, navigation, (), (), SERVER)
        assert not verdict.allowed and "Dot's own interface" in verdict.reason, (navigation, verdict)


def test_allowed_hosts_bind_the_pages_and_not_what_they_load() -> None:
    """With ``browser.allowed_hosts`` set, a page of another host is refused as before, while the scripts, images and
    fetches of an allowed page come from wherever it has them; a blocked host is refused either way."""
    allowed: tuple[str, ...] = ("example.com",)
    blocked: tuple[str, ...] = ("tracker.example",)
    assert request_verdict("https://example.com/", True, allowed, blocked, SERVER).allowed
    assert not request_verdict("https://other.example/", True, allowed, blocked, SERVER).allowed
    assert request_verdict("https://cdn.other.example/app.js", False, allowed, blocked, SERVER).allowed
    assert not request_verdict("https://tracker.example/pixel.gif", False, allowed, blocked, SERVER).allowed
    assert not request_verdict("https://tracker.example/", True, allowed, blocked, SERVER).allowed


@pytest.mark.parametrize("url", ["blob:https://example.com/0b6e-4c1a", "data:image/png;base64,iVBORw0KGgo="])
def test_what_a_page_made_itself_is_let_through_as_a_resource_only(url: str) -> None:
    assert request_verdict(url, False, (), (), SERVER).allowed
    assert not request_verdict(url, True, (), (), SERVER).allowed


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://127.0.0.1:8765\\@example.com/", "backslash"),
        ("https://example.com\\@169.254.169.254/", "backslash"),
        ("http://2852039166/latest/meta-data/", "blocked list"),
        ("http://169.254.169.254./", "blocked list"),
        ("http://169%2e254%2e169%2e254/latest/meta-data/", "blocked list"),
        ("http://%ff%fe.example/", "cannot be read"),
    ],
)
def test_an_address_a_browser_reads_otherwise_is_refused_or_read_as_the_browser_does(url: str, reason: str) -> None:
    """The code review's Y1: Python's parser read the host of ``http://127.0.0.1:8765\\@example.com/`` as example.com
    where a browser goes to 127.0.0.1, and a blocked host written as one number passed the block list."""
    verdict: UrlVerdict = check_browser_url(url, (), ("169.254.169.254",), SERVER)
    assert not verdict.allowed and reason in verdict.reason, verdict


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765/oauth/callback?state=abc&code=4/0AX&scope=email",
        "http://localhost:8765/oauth/callback?code=M.C518&state=abc",
        "http://[::1]:8765/oauth/callback?state=abc",
    ],
)
def test_the_way_back_of_a_sign_in_opens_as_a_page_and_as_nothing_else(url: str) -> None:
    """Item 445: a sign-in that opened in Dot's own Chrome comes back to the daemon; the guard lets that page through,
    and the same address loaded by a page (a fetch, an image) stays refused."""
    assert request_verdict(url, True, (), (), SERVER).allowed
    assert not request_verdict(url, False, (), (), SERVER).allowed
    # The model's own open of the address is judged as before.
    assert not check_browser_url(url, (), (), SERVER).allowed


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765/oauth/callback/",
        "http://127.0.0.1:8765/oauth/callbackx?state=abc",
        "http://127.0.0.1:8765/OAUTH/callback?state=abc",
        "http://127.0.0.1:8765/oauth/callback/../../api/approvals",
        "http://127.0.0.1:8765/api/approvals?next=/oauth/callback",
        "https://mac.tail1234.ts.net/oauth/callback?state=abc",
        # A browser reads the backslash as /: this is evil.example, which Python's parser takes for the loopback host.
        "http://evil.example\\@127.0.0.1:8765/oauth/callback?state=abc",
    ],
)
def test_nothing_else_of_dots_interface_opens_with_the_way_back(url: str) -> None:
    assert not request_verdict(url, True, (), (), SERVER).allowed
