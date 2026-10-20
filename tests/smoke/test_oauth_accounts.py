"""The official sign-ins of Google and Microsoft (item 422), end to end against stand-ins: the provider is an
``httpx.MockTransport`` that checks PKCE and the client, the Keychain is a throw-away keychain file (the owner's is
never touched), and the client names are in the environment. A sign-in comes back with a code traded for tokens, the
account is named by its address and kept, an access token is refreshed once it runs out, and signing out takes the
tokens away. Tokens longer than a line of ``security``'s prompt are kept whole, in pieces that leave nothing behind."""

import asyncio
import re
import subprocess
import time
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio

from dotagent.core.errors import SignInError
from dotagent.oauth.accounts import Accounts, CameBack, ProviderView
from dotagent.oauth.keychain import Keychain
from dotagent.oauth.pkce import challenge_of
from dotagent.oauth.providers import GOOGLE
from dotagent.oauth.tokens import Tokens
from dotagent.store.db import Store

pytestmark = pytest.mark.asyncio(loop_scope="function")

_SECURITY: str = "/usr/bin/security"
_ACCOUNT: re.Pattern[str] = re.compile(r'"acct"<blob>="([^"]*)"')


def _items(file: str) -> list[str]:
    """The accounts of the items ``file`` holds, sorted."""
    said: str = subprocess.run([_SECURITY, "dump-keychain", file], check=True, capture_output=True, text=True).stdout
    return sorted(_ACCOUNT.findall(said))


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """The clients the owner registered, from the environment: the Keychain's ``dot-agent`` is not read."""
    monkeypatch.setenv("DOT_GOOGLE_CLIENT_ID", "client-1")
    monkeypatch.setenv("DOT_GOOGLE_CLIENT_SECRET", "secret-1")
    monkeypatch.setenv("DOT_MS_CLIENT_ID", "client-ms")


class _Google:
    """Google's token endpoint and profile as far as a sign-in needs them; the challenge of the sign-in started is set
    before the code comes back, and every form it was sent is kept."""

    def __init__(self) -> None:
        self.challenge: str = ""
        self.forms: list[dict[str, list[str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == GOOGLE.profile_url:
            assert request.headers["authorization"] in ("Bearer at-1", "Bearer at-2")
            return httpx.Response(200, json={"email": "ada@example.com"})
        assert str(request.url) == GOOGLE.token_url, request.url
        form: dict[str, list[str]] = parse_qs(request.content.decode())
        self.forms.append(form)
        assert form["client_id"] == ["client-1"] and form["client_secret"] == ["secret-1"]
        if form["grant_type"] == ["authorization_code"]:
            if form["code"] != ["the-code"] or challenge_of(form["code_verifier"][0]) != self.challenge:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Bad code."})
            return httpx.Response(
                200, json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600, "scope": "s"}
            )
        assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["rt-1"]
        return httpx.Response(200, json={"access_token": "at-2", "expires_in": 3600})


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    opened: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        yield opened
    finally:
        await opened.close()


async def _signed_in(accounts: Accounts, google: _Google) -> CameBack | None:
    link: str = await accounts.start("google", 8765, time.time())
    query: dict[str, list[str]] = parse_qs(urlsplit(link).query)
    assert query["redirect_uri"] == ["http://127.0.0.1:8765/oauth/callback"]
    google.challenge = query["code_challenge"][0]
    return await accounts.come_back(query["state"][0], "the-code", None, time.time())


async def test_a_sign_in_keeps_the_account_its_tokens_refresh_and_signing_out_takes_them(
    keychain_file: str, clients: None, store: Store
) -> None:
    google: _Google = _Google()
    keychain: Keychain = Keychain(keychain_file)
    accounts: Accounts = Accounts(store, keychain, httpx.MockTransport(google))
    came: CameBack | None = await _signed_in(accounts, google)
    assert came is not None and came.status == 200 and came.title == "Signed in to Google", came
    providers: tuple[ProviderView, ...] = await accounts.providers()
    assert [(view.id, view.configured, [a.address for a in view.accounts]) for view in providers] == [
        ("google", True, ["ada@example.com"]),
        ("microsoft", True, []),
    ]
    assert await accounts.access_token("google", "ada@example.com") == "at-1"
    # The access token ran out: the next ask refreshes it, and the refresh token stays.
    kept: Tokens | None = await keychain.read("google", "ada@example.com")
    assert kept is not None
    await keychain.save("google", "ada@example.com", kept.model_copy(update={"expires_at": time.time() - 1}))
    assert await accounts.access_token("google", "ada@example.com") == "at-2"
    refreshed: Tokens | None = await keychain.read("google", "ada@example.com")
    assert refreshed is not None and refreshed.refresh_token == "rt-1"
    assert [form["grant_type"] for form in google.forms] == [["authorization_code"], ["refresh_token"]]
    await accounts.forget("google", "ada@example.com")
    assert await keychain.read("google", "ada@example.com") is None
    assert [view.accounts for view in await accounts.providers()] == [(), ()]


async def test_a_code_the_provider_refuses_a_no_and_a_state_nobody_waits_for(
    keychain_file: str, clients: None, store: Store
) -> None:
    google: _Google = _Google()
    accounts: Accounts = Accounts(store, Keychain(keychain_file), httpx.MockTransport(google))
    link: str = await accounts.start("google", 8765, time.time())
    query: dict[str, list[str]] = parse_qs(urlsplit(link).query)
    google.challenge = "not the sign-in's"
    refused: CameBack | None = await accounts.come_back(query["state"][0], "the-code", None, time.time())
    assert refused is not None and refused.status == 400 and "Google refused the sign-in's code" in refused.text
    # A state is good once.
    assert await accounts.come_back(query["state"][0], "the-code", None, time.time()) is None
    declined_link: str = await accounts.start("google", 8765, time.time())
    declined: CameBack | None = await accounts.come_back(
        parse_qs(urlsplit(declined_link).query)["state"][0], None, "access_denied", time.time()
    )
    assert declined is not None and "without a yes (access_denied)" in declined.text
    assert await accounts.come_back("nobody-waits", "code", None, time.time()) is None
    assert await asyncio.wait_for(accounts.providers(), 30) is not None


async def test_a_sign_in_without_the_owners_client_says_what_to_save(
    keychain_file: str, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only the client's id is saved: the lookup never reaches the owner's Keychain, whatever it holds.
    saved: dict[str, str] = {"DOT_GOOGLE_CLIENT_ID": "client-1"}
    monkeypatch.setattr("dotagent.oauth.accounts.resolve_secret", saved.get)
    accounts: Accounts = Accounts(store, Keychain(keychain_file), httpx.MockTransport(_Google()))
    with pytest.raises(SignInError, match="save DOT_GOOGLE_CLIENT_SECRET"):
        await accounts.start("google", 8765, time.time())


async def test_a_browser_that_comes_back_too_late_is_turned_away(clients: None, store: Store, tmp_path: Path) -> None:
    google: _Google = _Google()
    accounts: Accounts = Accounts(store, Keychain(str(tmp_path / "none.keychain-db")), httpx.MockTransport(google))
    started: float = time.time()
    link: str = await accounts.start("google", 8765, started)
    query: dict[str, list[str]] = parse_qs(urlsplit(link).query)
    google.challenge = query["code_challenge"][0]
    late: CameBack | None = await accounts.come_back(query["state"][0], "the-code", None, started + 601)
    assert late is not None and late.status == 400 and "more than 10 minutes" in late.text, late
    assert google.forms == []


async def test_tokens_longer_than_a_line_of_the_prompt_are_kept_whole_and_leave_nothing_behind(
    keychain_file: str,
) -> None:
    keychain: Keychain = Keychain(keychain_file)
    # Microsoft's tokens are some kilobytes: these would not fit on one line of security's prompt.
    long: Tokens = Tokens(
        access_token="eyJ0." + "x" * 4000, refresh_token="0.AXEA" + "r" * 1800, expires_at=1.0, scope="Mail.Send " * 20
    )
    await keychain.save("microsoft", "ada@example.com", long)
    assert await keychain.read("microsoft", "ada@example.com") == long
    assert _items(keychain_file) == [f"microsoft:ada@example.com{end}" for end in ("", "#a0", "#a1", "#a2")]
    short: Tokens = Tokens(access_token="at-2", refresh_token="rt-2", expires_at=2.0, scope="s")
    await keychain.save("microsoft", "ada@example.com", short)
    assert await keychain.read("microsoft", "ada@example.com") == short
    assert _items(keychain_file) == ["microsoft:ada@example.com", "microsoft:ada@example.com#b0"]
    await keychain.forget("microsoft", "ada@example.com")
    assert await keychain.read("microsoft", "ada@example.com") is None
    assert _items(keychain_file) == []
