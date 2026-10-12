"""The browser never opens Dot's own interface (item 135): this Mac on the daemon's port in any spelling a browser
reads, a dot at the end of the host included (item 140), or a name of ``[server] reach``. A server of this Mac on
another port and the web stay open."""

import pytest

from dotagent.browser.safety import UrlVerdict, check_browser_url
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
