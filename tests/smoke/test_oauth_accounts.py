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
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from structlog.testing import capture_logs

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.errors import SignInError
from dotagent.core.paths import ensure_layout
from dotagent.oauth import accounts as accounts_module
from dotagent.oauth.accounts import Accounts, CameBack, ProviderView, keep_revoking
from dotagent.oauth.keychain import Keychain
from dotagent.oauth.pkce import challenge_of
from dotagent.oauth.providers import GOOGLE
from dotagent.oauth.tokens import Account, Tokens
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
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
        # The tokens given back to end their grant (item 558), and a refresh that is held for as long as the test wants.
        self.revoked: list[str] = []
        # What the revoke call answers (item 1312): 200 takes the token; a refusal is a status and a body; an error
        # is raised as the network would.
        self.revoke_answer: tuple[int, dict[str, str]] | httpx.ConnectError = (200, {})
        self.revoke_calls: int = 0
        self.asked: asyncio.Event = asyncio.Event()
        self.refreshing: asyncio.Event = asyncio.Event()
        self.hold: asyncio.Event | None = None

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == GOOGLE.profile_url:
            assert request.headers["authorization"] in ("Bearer at-1", "Bearer at-2")
            return httpx.Response(200, json={"email": "ada@example.com"})
        if str(request.url) == GOOGLE.revoke_url:
            self.revoke_calls += 1
            self.asked.set()
            if isinstance(self.revoke_answer, httpx.ConnectError):
                raise self.revoke_answer
            status, body = self.revoke_answer
            if status == 200:
                self.revoked.extend(parse_qs(request.content.decode())["token"])
            return httpx.Response(status, json=body)
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
        self.refreshing.set()
        if self.hold is not None:
            await self.hold.wait()
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
    return await accounts.come_back(query["state"][0], "the-code", None, None, time.time())


async def test_a_sign_in_keeps_the_account_its_tokens_refresh_and_signing_out_takes_them(
    keychain_file: str, clients: None, store: Store
) -> None:
    google: _Google = _Google()
    keychain: Keychain = Keychain(keychain_file)
    accounts: Accounts = Accounts(store, keychain, httpx.MockTransport(google))
    came: CameBack | None = await _signed_in(accounts, google)
    assert came == CameBack(kind="signed_in", provider="google", address="ada@example.com", reason=None)
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
    assert await accounts.forget("google", "ada@example.com") is True
    assert await keychain.read("google", "ada@example.com") is None
    assert [view.accounts for view in await accounts.providers()] == [(), ()]
    # Signing out gives the refresh token back to Google, which ends the grant: a copy taken before mints no more.
    assert google.revoked == ["rt-1"]


async def test_signing_out_during_a_refresh_leaves_no_token_behind(
    keychain_file: str, clients: None, store: Store
) -> None:
    """The refresh ended after the sign-out and wrote fresh tokens, the refresh token among them, into the Keychain
    again, with no account left in the list to sign out of."""
    google: _Google = _Google()
    keychain: Keychain = Keychain(keychain_file)
    accounts: Accounts = Accounts(store, keychain, httpx.MockTransport(google))
    assert (await _signed_in(accounts, google)) is not None
    kept: Tokens | None = await keychain.read("google", "ada@example.com")
    assert kept is not None
    await keychain.save("google", "ada@example.com", kept.model_copy(update={"expires_at": time.time() - 1}))
    google.hold = asyncio.Event()
    refreshing: asyncio.Task[str] = asyncio.create_task(accounts.access_token("google", "ada@example.com"))
    await asyncio.wait_for(google.refreshing.wait(), 30)
    signing_out: asyncio.Task[bool] = asyncio.create_task(accounts.forget("google", "ada@example.com"))
    await asyncio.sleep(0.2)
    assert not signing_out.done(), "the sign-out did not wait for the refresh under way"
    google.hold.set()
    assert await asyncio.wait_for(refreshing, 30) == "at-2"
    await asyncio.wait_for(signing_out, 30)
    assert await keychain.read("google", "ada@example.com") is None
    assert [view.accounts for view in await accounts.providers()] == [(), ()]
    # The account that is signed in is not asked for a token any more.
    with pytest.raises(SignInError, match="not signed in"):
        await accounts.access_token("google", "ada@example.com")


async def test_a_code_the_provider_refuses_a_no_and_a_state_nobody_waits_for(
    keychain_file: str, clients: None, store: Store
) -> None:
    google: _Google = _Google()
    accounts: Accounts = Accounts(store, Keychain(keychain_file), httpx.MockTransport(google))
    link: str = await accounts.start("google", 8765, time.time())
    query: dict[str, list[str]] = parse_qs(urlsplit(link).query)
    google.challenge = "not the sign-in's"
    refused: CameBack | None = await accounts.come_back(query["state"][0], "the-code", None, None, time.time())
    # The page says what the provider said: its error code and its description, no more.
    assert refused == CameBack(
        kind="failed",
        provider="google",
        address=None,
        reason="Google refused the sign-in's code: invalid_grant - Bad code.",
    )
    # A state is good once.
    assert await accounts.come_back(query["state"][0], "the-code", None, None, time.time()) is None
    declined_link: str = await accounts.start("google", 8765, time.time())
    declined: CameBack | None = await accounts.come_back(
        parse_qs(urlsplit(declined_link).query)["state"][0], None, "access_denied", None, time.time()
    )
    assert declined == CameBack(kind="declined", provider="google", address=None, reason=None)
    other_link: str = await accounts.start("google", 8765, time.time())
    other: CameBack | None = await accounts.come_back(
        parse_qs(urlsplit(other_link).query)["state"][0], None, "server_error", "Try later.", time.time()
    )
    assert other == CameBack(kind="declined", provider="google", address=None, reason="server_error: Try later.")
    assert await accounts.come_back("nobody-waits", "code", None, None, time.time()) is None
    assert await asyncio.wait_for(accounts.providers(), 30) is not None


async def test_an_answer_that_is_no_oauth_error_is_kept_for_the_log_and_not_shown(
    keychain_file: str, clients: None, store: Store
) -> None:
    transport: httpx.MockTransport = httpx.MockTransport(
        lambda request: httpx.Response(502, text="<html>Bad gateway BODY-FOR-THE-LOG</html>")
    )
    accounts: Accounts = Accounts(store, Keychain(keychain_file), transport)
    link: str = await accounts.start("google", 8765, time.time())
    state: str = parse_qs(urlsplit(link).query)["state"][0]
    came: CameBack | None = await accounts.come_back(state, "the-code", None, None, time.time())
    assert came == CameBack(kind="failed", provider="google", address=None, reason="Google refused the sign-in's code")


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
    late: CameBack | None = await accounts.come_back(query["state"][0], "the-code", None, None, started + 601)
    assert late == CameBack(kind="expired", provider="google", address=None, reason=None), late
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


async def test_a_refused_revoke_keeps_the_token_to_ask_again_and_the_sign_out_stands(
    keychain_file: str, clients: None, store: Store
) -> None:
    """Item 1312: the tokens were forgotten before the revoke and a refusal was only logged, so Google kept listing Dot
    with a refresh token that nothing could end. The sign-out still succeeds here; the token waits in the Keychain
    under a place of its own, with a record in the store, until Google takes it."""
    google: _Google = _Google()
    keychain: Keychain = Keychain(keychain_file)
    accounts: Accounts = Accounts(store, keychain, httpx.MockTransport(google))
    assert (await _signed_in(accounts, google)) is not None
    google.revoke_answer = (400, {"error": "invalid_request", "error_description": "Bad."})
    with capture_logs() as logs:
        assert await accounts.forget("google", "ada@example.com") is False
    refused: list[dict[str, object]] = [entry for entry in logs if entry["event"] == "sign_out_not_revoked"]
    assert [(entry["status"], entry["error"]) for entry in refused] == [(400, "invalid_request")]
    assert "rt-1" not in str(logs)
    # Signed out here: no account, no tokens in its place, nothing to sign out of again.
    assert [view.accounts for view in await accounts.providers()] == [(), ()]
    assert await keychain.read("google", "ada@example.com") is None
    with pytest.raises(SignInError, match="not signed in"):
        await accounts.access_token("google", "ada@example.com")
    # What waits is a record with no token in it, and the tokens under the Keychain's own name for it.
    [record] = await accounts._pending()
    assert record.provider == "google"
    assert "rt-1" not in (await store.kv_get("oauth.pending_revokes") or "")
    assert _items(keychain_file) == [f"google:pending-revoke-{record.id}", f"google:pending-revoke-{record.id}#a0"]
    # The next round, with Google answering: the refresh token is given back, and nothing is left.
    google.revoke_answer = (200, {})
    assert await accounts.retry_pending_revokes(time.time() + 3600) == 1
    assert google.revoked == ["rt-1"]
    assert await accounts._pending() == []
    assert _items(keychain_file) == []
    assert await accounts.retry_pending_revokes(time.time() + 7200) == 0


async def test_a_token_google_calls_invalid_is_already_revoked_and_nothing_is_kept(
    keychain_file: str, clients: None, store: Store
) -> None:
    google: _Google = _Google()
    accounts: Accounts = Accounts(store, Keychain(keychain_file), httpx.MockTransport(google))
    assert (await _signed_in(accounts, google)) is not None
    google.revoke_answer = (400, {"error": "invalid_token", "error_description": "Token expired or revoked"})
    assert await accounts.forget("google", "ada@example.com") is True
    assert google.revoke_calls == 1
    assert await accounts._pending() == []
    assert _items(keychain_file) == []


async def test_a_google_that_cannot_be_reached_is_asked_every_round_for_a_week_and_then_let_go(
    keychain_file: str, clients: None, store: Store
) -> None:
    google: _Google = _Google()
    accounts: Accounts = Accounts(store, Keychain(keychain_file), httpx.MockTransport(google))
    assert (await _signed_in(accounts, google)) is not None
    google.revoke_answer = httpx.ConnectError("no network")
    assert await accounts.forget("google", "ada@example.com") is False
    assert google.revoke_calls == 1
    [record] = await accounts._pending()
    now: float = record.since
    # Every round within the week asks again and keeps the record.
    assert await accounts.retry_pending_revokes(now + 3600) == 0
    assert await accounts.retry_pending_revokes(now + 6 * 24 * 3600) == 0
    assert google.revoke_calls == 3
    assert len(await accounts._pending()) == 1
    # A server error is no better than no network.
    google.revoke_answer = (503, {})
    assert await accounts.retry_pending_revokes(now + 6 * 24 * 3600 + 3600) == 0
    # After seven days it is given up: no call, the parked tokens forgotten.
    assert await accounts.retry_pending_revokes(now + 7 * 24 * 3600 + 1) == 1
    assert google.revoke_calls == 4
    assert await accounts._pending() == []
    assert _items(keychain_file) == []


async def test_microsoft_has_no_revoke_call_so_it_is_signed_out_at_once_and_not_revoked(
    keychain_file: str, clients: None, store: Store
) -> None:
    transport: httpx.MockTransport = httpx.MockTransport(lambda request: pytest.fail(f"asked {request.url}"))
    keychain: Keychain = Keychain(keychain_file)
    accounts: Accounts = Accounts(store, keychain, transport)
    await keychain.save(
        "microsoft", "ada@example.com", Tokens(access_token="a", refresh_token="r", expires_at=1.0, scope="")
    )
    await accounts._keep([Account(provider="microsoft", address="ada@example.com", connected_at=1.0)])
    assert await accounts.forget("microsoft", "ada@example.com") is False
    assert await accounts._pending() == []
    assert _items(keychain_file) == []


async def test_the_route_says_whether_the_provider_ended_the_grant_and_where_to_do_it_when_it_did_not(
    keychain_file: str, clients: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sign-out answers ``revoked`` and the sentence the page shows; it is a 200 either way."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    (tmp_path / "home" / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
    built: Runtime = await build_runtime(load_config(tmp_path / "home" / "config.toml"))
    google: _Google = _Google()
    accounts: Accounts = Accounts(built.store, Keychain(keychain_file), httpx.MockTransport(google))
    runtime: Runtime = replace(built, accounts=accounts)
    manager: RunManager = RunManager(runtime)
    token: str = "sign-out-test-token"
    try:
        context: ServerContext = ServerContext(
            runtime=runtime,
            manager=manager,
            scheduler=None,
            token=token,
            host="127.0.0.1",
            port=8765,
            started_at=time.time(),
            stopping=asyncio.Event(),
        )
        transport: httpx.ASGITransport = httpx.ASGITransport(app=create_app(context))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8765", headers={"Authorization": f"Bearer {token}"}
        ) as client:
            assert (await _signed_in(accounts, google)) is not None
            google.revoke_answer = (500, {})
            refused: httpx.Response = await client.delete("/api/accounts/google/ada@example.com")
            assert refused.status_code == 200, refused.text
            assert refused.json()["revoked"] is False
            assert refused.json()["notice"] == (
                "Signed out here; Google may still list Dot: remove it at myaccount.google.com/permissions"
            )
            assert [view["accounts"] for view in refused.json()["providers"]] == [[], []]
            google.revoke_answer = (200, {})
            assert (await _signed_in(accounts, google)) is not None
            taken: httpx.Response = await client.delete("/api/accounts/google/ada@example.com")
            assert taken.status_code == 200 and taken.json()["revoked"] is True and taken.json()["notice"] is None
    finally:
        await manager.shutdown()
        await close_runtime(built)


async def test_the_daemons_loop_asks_for_a_sign_out_google_did_not_take_as_it_starts_and_stops_with_it(
    keychain_file: str, clients: None, store: Store
) -> None:
    google: _Google = _Google()
    accounts: Accounts = Accounts(store, Keychain(keychain_file), httpx.MockTransport(google))
    assert (await _signed_in(accounts, google)) is not None
    google.revoke_answer = (500, {})
    assert await accounts.forget("google", "ada@example.com") is False
    google.revoke_answer = (200, {})
    google.asked.clear()
    stopping: asyncio.Event = asyncio.Event()
    looping: asyncio.Task[None] = asyncio.create_task(keep_revoking(accounts, stopping))
    await asyncio.wait_for(google.asked.wait(), 30)
    stopping.set()
    async with asyncio.timeout(30):
        await looping
    assert google.revoked == ["rt-1"]
    assert await accounts._pending() == []
    assert _items(keychain_file) == []


async def test_a_round_of_the_revoke_loop_that_crashes_is_logged_and_the_loop_asks_again(
    monkeypatch: pytest.MonkeyPatch, keychain_file: str, store: Store
) -> None:
    """Only Dot's own errors were caught, so any other exception ended the task for the rest of the daemon's life, with
    nothing logged until shutdown, and a sign-out Google did not take was never asked for again."""
    monkeypatch.setattr(accounts_module, "RETRY_EVERY_S", 0.001)
    accounts: Accounts = Accounts(store, Keychain(keychain_file), httpx.MockTransport(_Google()))
    rounds: list[int] = []
    asked_again: asyncio.Event = asyncio.Event()

    async def retry(now: float) -> int:
        rounds.append(len(rounds))
        if len(rounds) == 1:
            raise RuntimeError("a bug in a round")
        if len(rounds) == 2:
            raise OSError("the disk")
        asked_again.set()
        return 0

    monkeypatch.setattr(accounts, "retry_pending_revokes", retry)
    stopping: asyncio.Event = asyncio.Event()
    looping: asyncio.Task[None] = asyncio.create_task(keep_revoking(accounts, stopping))
    with capture_logs() as logs:
        await asyncio.wait_for(asked_again.wait(), 10)
    assert not looping.done(), "the loop is still there"
    stopping.set()
    async with asyncio.timeout(10):
        await looping
    crashed: list[dict[str, object]] = [entry for entry in logs if entry["event"] == "sign_out_revoke_retry_crashed"]
    assert [(entry["error_type"], entry["exc_info"]) for entry in crashed] == [
        ("RuntimeError", True),
        ("OSError", True),
    ]
