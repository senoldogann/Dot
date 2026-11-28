"""The official sign-ins of Google and Microsoft (item 422), their pure parts: PKCE's challenge, the address of a
provider's page, the tokens and the address read from what the provider answered, and which requests come from this
Mac's own address, and the pieces the tokens are kept in in the Keychain."""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.requests import Request

from dotagent.core.errors import SignInError
from dotagent.oauth import accounts as accounts_module
from dotagent.oauth.accounts import authorize_link, client_names
from dotagent.oauth.keychain import pieces_of, tokens_of, without_pieces
from dotagent.oauth.pkce import challenge_of, new_state, new_verifier
from dotagent.oauth.providers import GOOGLE, MICROSOFT
from dotagent.oauth.tokens import Tokens, address_from, granted, tokens_from
from dotagent.server.guard import PHONE_STATE
from dotagent.server.routes_accounts import on_this_mac


def test_the_challenge_is_the_verifiers_sha256_in_base64url_without_padding() -> None:
    verifier: str = new_verifier()
    assert 43 <= len(verifier) <= 128
    expected: str = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge_of(verifier) == expected and "=" not in expected
    assert new_state() != new_state()


def test_the_providers_page_asks_for_the_access_with_pkce_and_comes_back_to_this_mac() -> None:
    link: str = authorize_link(
        GOOGLE, GOOGLE.scopes, (), "client-1", "http://127.0.0.1:8765/oauth/callback", "state-1", "challenge-1"
    )
    parts = urlsplit(link)
    query: dict[str, list[str]] = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == GOOGLE.authorize_url
    assert query["client_id"] == ["client-1"] and query["state"] == ["state-1"]
    assert query["code_challenge"] == ["challenge-1"] and query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://127.0.0.1:8765/oauth/callback"]
    assert query["access_type"] == ["offline"] and "https://www.googleapis.com/auth/gmail.readonly" in query["scope"][0]
    assert client_names(GOOGLE) == ("DOT_GOOGLE_CLIENT_ID", "DOT_GOOGLE_CLIENT_SECRET")
    assert client_names(MICROSOFT) == ("DOT_MS_CLIENT_ID",)
    assert "offline_access" in MICROSOFT.scopes


def test_a_sign_in_asks_to_read_and_drafts_are_asked_apart() -> None:
    """Item 558: Google was asked for ``gmail.modify`` and ``calendar``, Microsoft for ``Mail.ReadWrite``,
    ``Mail.Send`` and ``Calendars.ReadWrite``, though every account tool only reads; a token read out of the Keychain
    by a script could then send mail as the owner. A sign-in still asks only to read. Item 2047's drafts ask, apart and
    for one account, for Gmail's ``gmail.compose`` and Graph's ``Mail.ReadWrite`` and ``Mail.Send``."""
    assert GOOGLE.scopes == (
        "openid",
        "email",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.readonly",
    )
    assert MICROSOFT.scopes == ("openid", "email", "offline_access", "User.Read", "Mail.Read", "Calendars.Read")
    for scope in (*GOOGLE.scopes, *MICROSOFT.scopes):
        assert not any(word in scope for word in ("modify", "send", "Send", "ReadWrite", "compose", "full_access"))
        assert scope != "https://www.googleapis.com/auth/calendar"
    assert GOOGLE.draft_scopes == ("https://www.googleapis.com/auth/gmail.compose",)
    assert MICROSOFT.draft_scopes == ("Mail.ReadWrite", "Mail.Send")
    link: str = authorize_link(
        MICROSOFT,
        (*MICROSOFT.scopes, *MICROSOFT.draft_scopes),
        (("login_hint", "ada@outlook.com"),),
        "client-ms",
        "http://localhost:8765/oauth/callback",
        "state-2",
        "challenge-2",
    )
    query: dict[str, list[str]] = parse_qs(urlsplit(link).query)
    assert query["login_hint"] == ["ada@outlook.com"] and query["scope"][0].endswith("Mail.ReadWrite Mail.Send")


def test_drafts_are_allowed_only_when_the_token_holds_every_draft_scope() -> None:
    assert granted(
        MICROSOFT.draft_scopes,
        "openid https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send",
    )
    assert granted(GOOGLE.draft_scopes, "openid https://www.googleapis.com/auth/gmail.compose")
    # Google lets the owner untick a scope on its page: the token then lacks it.
    assert not granted(GOOGLE.draft_scopes, "openid https://www.googleapis.com/auth/gmail.readonly")
    assert not granted(MICROSOFT.draft_scopes, "Mail.Read Mail.ReadWrite")


def test_only_google_has_a_call_that_ends_a_grant() -> None:
    assert GOOGLE.revoke_url == "https://oauth2.googleapis.com/revoke" and MICROSOFT.revoke_url is None


def test_tokens_run_out_when_the_provider_says_and_a_refresh_keeps_the_refresh_token_it_had() -> None:
    first = tokens_from({"access_token": "a", "refresh_token": "r", "expires_in": 3599, "scope": "s"}, 1000.0, None)
    assert (first.access_token, first.refresh_token, first.expires_at, first.scope) == ("a", "r", 4599.0, "s")
    refreshed = tokens_from({"access_token": "b", "expires_in": 60}, 2000.0, "r")
    assert (refreshed.refresh_token, refreshed.expires_at) == ("r", 2060.0)
    with pytest.raises(SignInError):
        tokens_from({"token": "no access token"}, 0.0, None)


def test_the_address_is_googles_email_or_microsofts_mail_or_its_user_name() -> None:
    assert address_from({"email": "ada@example.com", "sub": "1"}) == "ada@example.com"
    assert address_from({"mail": "ada@contoso.com", "userPrincipalName": "ada_x@contoso.com"}) == "ada@contoso.com"
    assert address_from({"mail": None, "userPrincipalName": "ada@outlook.com"}) == "ada@outlook.com"
    with pytest.raises(SignInError):
        address_from({"name": "Ada"})


def _request(host: str | None, phone: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = [] if host is None else [(b"host", host.encode())]
    state: dict[str, str] = {} if phone is None else {PHONE_STATE: phone}
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers, "state": state})


def test_a_sign_in_starts_only_from_this_macs_own_address() -> None:
    mine: tuple[str, ...] = ("127.0.0.1:8765", "localhost", "LOCALHOST:8765", "[::1]:8765")
    assert [on_this_mac(_request(host)) for host in mine] == [True] * 4
    assert [on_this_mac(_request(host)) for host in ("mac.tail1234.ts.net", "192.168.1.5:8765", None)] == [False] * 3
    # A phone's key is not this Mac, whatever Host its request writes (item 678).
    assert not on_this_mac(_request("127.0.0.1:8765", "029d9a7ce196"))


def test_tokens_longer_than_a_line_of_securitys_prompt_go_in_pieces_and_come_back_whole() -> None:
    tokens: Tokens = Tokens(access_token="x" * 5000, refresh_token="r" * 1500, expires_at=1.0, scope="Mail.Send")
    pieces: list[str] = pieces_of(tokens)
    assert len(pieces) == 3 and all(0 < len(piece) <= 3000 for piece in pieces)
    assert tokens_of(pieces) == tokens


def test_what_security_says_keeps_no_piece_of_a_token() -> None:
    piece: str = pieces_of(Tokens(access_token="a" * 900, refresh_token=None, expires_at=1.0, scope="s"))[0]
    said: str = f'security: unknown command "{piece[:400]}"\nSecKeychainItemCreateFromContent: The item already exists.'
    cut: str = without_pieces(said)
    assert piece[:40] not in cut and "[cut]" in cut and "SecKeychainItemCreateFromContent" in cut


def test_a_google_refresh_token_that_ran_out_names_the_seven_days_of_testing_and_the_way_out() -> None:
    """Google's ``invalid_grant`` on a refresh is the 7-day refresh token of an app still in Testing (item 1343): the
    words say to publish the app before signing in again, else the next sign-in dies the same way a week later."""
    advice: str = accounts_module.refresh_advice("google", "invalid_grant")
    assert "Testing" in advice and "7 days" in advice and "Audience" in advice and "Settings › Connectors" in advice
    assert accounts_module.refresh_advice("google", "invalid_client") == "(sign in again in Settings › Connectors)"
    assert accounts_module.refresh_advice("microsoft", "invalid_grant") == "(sign in again in Settings › Connectors)"
