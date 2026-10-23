"""The rule of the plain web client, public addresses only (``[web] allow_private_hosts``), holds for a page the browser
reads for the web tools (item 554): each document of the way and the page it ended on were served from public
addresses, or the page is not given back; the crawl's reader of the run's own tab holds the page it landed on to the
same rule."""

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import Error as PlaywrightError

from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import PageView
from dotagent.config import load_default_config
from dotagent.core.errors import BrowserError, ForbiddenUrlError
from dotagent.tools.context import ToolContext
from dotagent.tools.web import _renderer_for
from dotagent.web import addresses
from dotagent.web.addresses import is_private_text, non_public_address

_PUBLIC: str = "93.184.216.34"


@pytest.mark.parametrize(
    ("address", "private"),
    [
        ("127.0.0.1", True),
        ("192.168.1.1", True),
        ("10.0.0.5", True),
        ("169.254.169.254", True),
        ("[::1]", True),
        ("::1", True),
        ("fe80::1%en0", True),
        ("::ffff:127.0.0.1", True),
        ("[::ffff:192.168.0.1]", True),
        ("not an address", True),
        (_PUBLIC, False),
        ("[2606:2800:220:1:248:1893:25c8:1946]", False),
    ],
)
def test_an_address_as_chrome_writes_it_is_private_or_public(address: str, private: bool) -> None:
    assert is_private_text(address) is private


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("host", "found"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("[::1]", "::1"),
        ("192.168.1.1", "192.168.1.1"),
        (_PUBLIC, None),
        ("localhost", "127.0.0.1"),
        ("a..b", None),
    ],
)
async def test_a_host_that_is_or_resolves_to_a_private_address_is_found(host: str, found: str | None) -> None:
    answer: str | None = await non_public_address(host)
    assert answer in ("127.0.0.1", "::1") if host == "localhost" else answer == found, answer


@dataclass
class _Served:
    """A response as Playwright gives it, to the one thing the rule asks of it: where it was served from."""

    address: str | None
    error: bool = False

    async def server_addr(self) -> dict[str, object] | None:
        if self.error:
            raise PlaywrightError("the response is gone")
        return None if self.address is None else {"ipAddress": self.address, "port": 443}


def _session(tmp_path: Path) -> BrowserSession:
    config = load_default_config()
    return BrowserSession(config.browser, config.server, tmp_path, tmp_path, no_runs_at_work)


@pytest.mark.asyncio
async def test_a_page_whose_documents_all_came_from_public_addresses_is_given_back(tmp_path: Path) -> None:
    session: BrowserSession = _session(tmp_path)
    documents = [_Served(_PUBLIC), _Served(None), _Served("[2606:2800:220:1:248:1893:25c8:1946]")]
    await session._require_public(f"https://{_PUBLIC}/page", documents)  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("private", ["192.168.1.1", "[::1]", "127.0.0.1", "169.254.169.254"])
async def test_a_page_a_hop_of_its_redirects_was_served_from_a_private_address_is_not_given_back(
    tmp_path: Path, private: str
) -> None:
    """The page that answered 403 sent the browser on, or the name was pointed at the local network once checked: the
    address Chrome connected to says so, whatever the address of the page reads."""
    session: BrowserSession = _session(tmp_path)
    documents = [_Served(_PUBLIC), _Served(private), _Served(_PUBLIC)]
    with pytest.raises(ForbiddenUrlError) as refused:
        await session._require_public("https://pages.example/after", documents)  # type: ignore[arg-type]
    assert "the web tools only reach the public internet" in refused.value.message
    assert refused.value.reason == f"it was served from {private}" and refused.value.resource_type == "document"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "landed", ["http://192.168.1.1/admin", "http://[::1]:11434/api/tags", "http://localhost:3000/"]
)
async def test_a_page_that_landed_on_a_private_host_is_not_given_back_whatever_chrome_reports(
    tmp_path: Path, landed: str
) -> None:
    session: BrowserSession = _session(tmp_path)
    with pytest.raises(ForbiddenUrlError):
        await session._require_public(landed, [_Served(None), _Served(None, error=True)])  # type: ignore[arg-type]


class _Browser:
    """The run's browser, as the crawl's reader of its tab uses it: it opens a page and reads its text."""

    def __init__(self, landed: str) -> None:
        self.landed: str = landed
        self.read: int = 0

    async def open_url(self, thread_id: str, url: str, hide_secrets: bool) -> PageView:
        return PageView(
            tab_index=1,
            url=self.landed,
            title="",
            status=200,
            mode="full",
            body="",
            overlay="",
            overlays=0,
            offscreen_refs=0,
            tabs=(),
            notes=(),
        )

    async def read_text(self, thread_id: str, ref: str | None, offset: int, max_chars: int) -> SimpleNamespace:
        self.read += 1
        return SimpleNamespace(text="What the page says.")


def _context(browser: _Browser, allow_private_hosts: bool) -> ToolContext:
    config = load_default_config()
    config = config.model_copy(
        update={"web": config.web.model_copy(update={"allow_private_hosts": allow_private_hosts})}
    )
    return SimpleNamespace(  # type: ignore[return-value]
        browser=browser, thread_id="t", approvals="ask", runtime=SimpleNamespace(config=config)
    )


@pytest.mark.asyncio
async def test_the_crawls_reader_of_the_run_tab_gives_back_no_page_that_landed_on_the_local_network() -> None:
    browser: _Browser = _Browser("http://192.168.1.1/admin")
    render = _renderer_for(_context(browser, allow_private_hosts=False))
    with pytest.raises(BrowserError, match="only reach the public internet") as refused:
        await render("https://pages.example/a", 1000)
    assert refused.value.context["address"] == "192.168.1.1" and browser.read == 0
    # With the setting on, as the owner chose, the page is read as it was.
    allowed = _renderer_for(_context(browser, allow_private_hosts=True))
    assert await allowed("https://pages.example/a", 1000) == "What the page says."
    # A page of a public address is read.
    public: _Browser = _Browser(f"https://{_PUBLIC}/a")
    assert await _renderer_for(_context(public, allow_private_hosts=False))("https://pages.example/a", 1000) == (
        "What the page says."
    )


def test_the_resolver_the_rule_uses_is_the_one_of_the_plain_client() -> None:
    """One definition of what is public: the plain client's connections and the browser's reads both ask it."""
    assert addresses.is_public_address(_PUBLIC) and not addresses.is_public_address("100.64.0.1")
