"""The page the browser is sent back to after a sign-in (item 437): every outcome of an account's sign-in and of an MCP
server's has a title, an h1 and the sheets of the interface, which it carries in its one style under the hash its
policy names, and asks the server for nothing but its faces (item 448): no link, no script, its pictures ``data:``
addresses. What a provider said is escaped."""

import base64
import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from dotagent.config import McpServerConfig
from dotagent.mcp.catalogue import MCP_SERVICES, McpService
from dotagent.oauth.accounts import CameBack, CameBackKind, denial_reason
from dotagent.server.callback_page import (
    PROVIDER_MARKS,
    WORDMARK,
    Callback,
    Outcome,
    Subject,
    account_callback,
    page_style,
    render,
    status_of,
    words_of,
)
from dotagent.server.guard import response_headers
from dotagent.server.routes_mcp import mcp_subject

_STATIC: Path = Path(__file__).resolve().parents[2] / "src" / "dotagent" / "server" / "static"
_NOTION: Subject = Subject(name="Notion", detail="mcp.notion.com", mark="notion-icon.svg")
_GOOGLE: Subject = Subject(name="Google", detail="ada@example.com", mark="google-icon.svg")
_MICROSOFT: Subject = Subject(name="Microsoft", detail=None, mark=None)
_OUTCOMES: tuple[Callback, ...] = (
    Callback(outcome="signed_in", subject=_GOOGLE, reason=None),
    Callback(outcome="back", subject=_NOTION, reason=None),
    Callback(outcome="declined", subject=_MICROSOFT, reason=None),
    Callback(outcome="declined", subject=_NOTION, reason="server_error: Try later."),
    Callback(outcome="expired", subject=_GOOGLE, reason=None),
    Callback(outcome="failed", subject=_MICROSOFT, reason="Microsoft refused the sign-in's code: invalid_client"),
    Callback(outcome="unknown", subject=None, reason=None),
)


def _body(page: str) -> str:
    """The page without its style, which names every class of the interface."""
    return re.sub(r"<style>.*?</style>", "", page, flags=re.DOTALL)


def _data_address(relative: str) -> str:
    return f"data:image/svg+xml;base64,{base64.b64encode((_STATIC / relative).read_bytes()).decode('ascii')}"


@pytest.mark.parametrize("callback", _OUTCOMES, ids=lambda one: f"{one.outcome}-{one.reason is not None}")
def test_every_outcome_is_a_titled_page_in_dots_own_look_that_asks_the_server_for_nothing(callback: Callback) -> None:
    page: str = render(callback)
    title: str = words_of(callback).title
    assert f"<title>{title} - Dot</title>" in page
    assert re.findall(r"<h1[^>]*>(.*?)</h1>", page) == [title]
    sheets: str = "\n".join((_STATIC / "css" / name).read_text(encoding="utf-8") for name in ("app.css", "themes.css"))
    assert re.findall(r"<style>(.*?)</style>", page, re.DOTALL) == [sheets]
    assert f'<link rel="icon" href="{_data_address("favicon.svg")}" type="image/svg+xml">' in page
    assert '<meta name="color-scheme" content="light dark">' in page
    assert WORDMARK in page
    # Nothing it shows is fetched (Dot's own Chrome refuses every file of the interface), and nothing leads into the
    # interface, which that Chrome never opens.
    assert all(address.startswith("data:") for address in re.findall(r'\s(?:src|href)="([^"]*)"', page))
    assert not re.search(r"<a\s|<script|\sstyle=|\son[a-z]+=", page)


def test_the_policy_of_the_page_lets_its_one_style_apply_and_nothing_else_inline() -> None:
    policy: str = dict(response_headers("/oauth/callback"))["Content-Security-Policy"]
    digest: str = base64.b64encode(hashlib.sha256(page_style().text.encode("utf-8")).digest()).decode("ascii")
    assert f"style-src 'sha256-{digest}';" in policy and "'unsafe-inline'" not in policy
    assert "default-src 'none'" in policy and "script-src" not in policy and "img-src data:;" in policy
    assert dict(response_headers("/oauth/callback"))["Cache-Control"] == "no-store"


def test_a_mark_is_the_name_of_an_svg_file_of_the_logos() -> None:
    for mark in ("../../config.toml", "icons/plugins/google-icon.svg", "google-icon.png"):
        with pytest.raises(ValidationError, match="a mark is the name of an SVG file"):
            Subject(name="Google", detail=None, mark=mark)


def test_the_status_of_a_page_says_whether_the_sign_in_went_on() -> None:
    statuses: dict[Outcome, int] = {
        "signed_in": 200,
        "back": 200,
        "declined": 200,
        "expired": 400,
        "failed": 400,
        "unknown": 400,
    }
    assert {outcome: status_of(outcome) for outcome in statuses} == statuses


def test_a_page_names_the_account_with_its_address_and_the_service_with_its_host() -> None:
    signed_in: str = _body(render(_OUTCOMES[0]))
    assert '<h1 class="page-title">Signed in to Google</h1>' in signed_in
    assert f'src="{_data_address("icons/plugins/google-icon.svg")}"' in signed_in and "ada@example.com" in signed_in
    assert "Dot on this Mac already shows it" in signed_in
    # A provider with no mark of its own shows its initial.
    declined: str = _body(render(_OUTCOMES[2]))
    assert '<span class="callback-mark" aria-hidden="true">M</span>' in declined and "callback-reason" not in declined
    # An MCP server's trade of its code happens after the page: the page does not say it signed in.
    back: str = _body(render(_OUTCOMES[1]))
    assert "Back from Notion</h1>" in back and "mcp.notion.com" in back and "Signed in" not in back


def test_what_a_provider_said_is_escaped() -> None:
    page: str = render(Callback(outcome="failed", subject=_MICROSOFT, reason='<img src=x onerror="alert(1)">'))
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in page and "<img src=x" not in page
    named: str = render(Callback(outcome="back", subject=Subject(name="<b>x</b>", detail=None, mark=None), reason=None))
    assert "&lt;b&gt;x&lt;/b&gt;" in named and "<b>x</b>" not in named


def test_only_a_state_nobody_waits_for_names_no_one() -> None:
    with pytest.raises(ValidationError, match="names no account or service"):
        Callback(outcome="signed_in", subject=None, reason=None)
    with pytest.raises(ValidationError, match="names no account or service"):
        Callback(outcome="unknown", subject=_GOOGLE, reason=None)


@pytest.mark.parametrize("kind", ["signed_in", "declined", "expired", "failed"])
def test_an_accounts_return_is_a_page_of_its_provider(kind: CameBackKind) -> None:
    came: CameBack = CameBack(kind=kind, provider="google", address="ada@example.com", reason=None)
    callback: Callback = account_callback(came)
    assert callback.outcome == kind and callback.subject == _GOOGLE


def test_the_no_is_no_reason_and_another_error_is_shown_as_the_provider_worded_it() -> None:
    assert denial_reason(None, None) is None and denial_reason("access_denied", "The user said no.") is None
    assert denial_reason("server_error", None) == "server_error"
    assert denial_reason("server_error", "") == "server_error"
    assert denial_reason("server_error", "Try later.") == "server_error: Try later."
    long: str | None = denial_reason("e" * 500, "d" * 900)
    assert long is not None and len(long) < 320


def test_an_mcp_server_is_named_as_the_catalogue_names_its_service_and_never_by_the_whole_address() -> None:
    service: McpService = next(one for one in MCP_SERVICES if one.id == "notion")
    assert service.url is not None
    notion: Subject = mcp_subject("notion", McpServerConfig(name="notion", url=service.url, transport="http"))
    assert notion.name == service.name and notion.mark is not None and notion.mark.endswith(".svg")
    assert notion.detail == urlsplit(service.url).netloc
    # A server of the owner's own is named as they named it, by its host: a key in its address stays out of the page.
    own: Subject = mcp_subject(
        "mine", McpServerConfig(name="mine", url="https://ada:pass@example.com:8443/mcp?key=SECRET", transport="http")
    )
    assert own == Subject(name="mine", detail="example.com:8443", mark=None)
    assert mcp_subject("gone", None) == Subject(name="gone", detail=None, mark=None)


def test_the_wordmark_is_the_one_of_the_page_kept_for_a_mac_out_of_reach() -> None:
    offline: str = (_STATIC / "offline.html").read_text(encoding="utf-8")
    found: re.Match[str] | None = re.search(r'<svg class="wordmark".*?</svg>', offline, re.DOTALL)
    assert found is not None
    assert re.sub(r">\s+<", "><", found.group(0)) == WORDMARK


def test_every_provider_mark_is_a_file_of_the_logos() -> None:
    """The page draws each provider's mark from the plugins' logos, so a file renamed there is caught here."""
    assert set(PROVIDER_MARKS) == {"google", "microsoft"}
    for mark in PROVIDER_MARKS.values():
        assert (_STATIC / "icons" / "plugins" / mark).is_file(), mark
