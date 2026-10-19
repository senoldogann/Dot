"""The official sign-ins of Google and Microsoft (item 422), their pure parts: PKCE's challenge, the address of a
provider's page, the tokens and the address read from what the provider answered, and which requests come from this
Mac's own address."""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

from dotagent.core.errors import SignInError
from dotagent.oauth.accounts import authorize_link, client_names
from dotagent.oauth.pkce import challenge_of, new_state, new_verifier
from dotagent.oauth.providers import GOOGLE, MICROSOFT
from dotagent.oauth.tokens import address_from, tokens_from
from dotagent.server.routes_accounts import on_this_mac


def test_the_challenge_is_the_verifiers_sha256_in_base64url_without_padding() -> None:
    verifier: str = new_verifier()
    assert 43 <= len(verifier) <= 128
    expected: str = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge_of(verifier) == expected and "=" not in expected
    assert new_state() != new_state()


def test_the_providers_page_asks_for_the_access_with_pkce_and_comes_back_to_this_mac() -> None:
    link: str = authorize_link(GOOGLE, "client-1", "http://127.0.0.1:8765/oauth/callback", "state-1", "challenge-1")
    parts = urlsplit(link)
    query: dict[str, list[str]] = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == GOOGLE.authorize_url
    assert query["client_id"] == ["client-1"] and query["state"] == ["state-1"]
    assert query["code_challenge"] == ["challenge-1"] and query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://127.0.0.1:8765/oauth/callback"]
    assert query["access_type"] == ["offline"] and "https://www.googleapis.com/auth/gmail.modify" in query["scope"][0]
    assert client_names(GOOGLE) == ("DOT_GOOGLE_CLIENT_ID", "DOT_GOOGLE_CLIENT_SECRET")
    assert client_names(MICROSOFT) == ("DOT_MS_CLIENT_ID",)
    assert "offline_access" in MICROSOFT.scopes


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


def test_a_sign_in_starts_only_from_this_macs_own_address() -> None:
    assert [on_this_mac(host) for host in ("127.0.0.1:8765", "localhost", "LOCALHOST:8765", "[::1]:8765")] == [True] * 4
    assert [on_this_mac(host) for host in ("mac.tail1234.ts.net", "192.168.1.5:8765", None)] == [False] * 3
