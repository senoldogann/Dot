"""The browser never opens Dot's own interface (item 135): this Mac on the daemon's port in any spelling a browser
reads, a dot at the end of the host included (item 140), or a name of ``[server] reach``. A server of this Mac on
another port and the web stay open."""

import pytest

from dotagent.browser.safety import UrlVerdict, check_browser_url, forbidden_target, request_verdict
from dotagent.config import ServerConfig

SERVER: ServerConfig = ServerConfig(host="127.0.0.1", port=8765, reach=["mac.tail1234.ts.net"])


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
    ],
)
def test_dots_own_interface_is_never_opened(url: str) -> None:
    verdict: UrlVerdict = check_browser_url(url, (), (), SERVER)
    assert not verdict.allowed and "Dot's own interface" in verdict.reason, verdict


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
