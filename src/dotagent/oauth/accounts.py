"""Accounts of Google and Microsoft signed in through their official sign-ins (item 422).

A sign-in starts for the owner's browser on this Mac (:meth:`Accounts.start`): the provider's page asks for a yes, then
sends the browser back to the daemon's ``/oauth/callback`` with a code, which is traded for tokens with PKCE and the
client the owner registered (:mod:`dotagent.oauth.providers`). The account is named by its address, its tokens go to
the Keychain (:mod:`dotagent.oauth.keychain`) and the list of accounts, with no secret in it, to the store. A tool asks
for an account's access token (:meth:`Accounts.access_token`), which is refreshed once it is about to run out. Nothing
here logs a token, a code or an address."""

import asyncio
import time
from urllib.parse import urlencode

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from dotagent.core.errors import SignInError
from dotagent.core.logs import get_logger
from dotagent.core.retry import RetryPolicy, retry_async
from dotagent.core.text import truncate_middle
from dotagent.core.types import FrozenModel
from dotagent.mcp.signin import CALLBACK_PATH
from dotagent.oauth.keychain import Keychain
from dotagent.oauth.pkce import challenge_of, new_state, new_verifier
from dotagent.oauth.providers import PROVIDERS, Provider, ProviderId
from dotagent.oauth.tokens import Account, Tokens, address_from, tokens_from
from dotagent.secrets import resolve_secret
from dotagent.store.db import Store

log = get_logger(__name__)

_ACCOUNTS_KEY: str = "oauth.accounts"
# How long a sign-in waits for the browser to come back.
_WAIT_S: float = 600.0
# An access token is refreshed this long before it runs out, so that a call made with it does not meet its end.
_REFRESH_MARGIN_S: float = 120.0
_HTTP_TIMEOUT_S: float = 30.0
_RETRY: RetryPolicy = RetryPolicy(attempts=3, base_delay_s=1.0, max_delay_s=8.0)
_ACCOUNT_LIST: TypeAdapter[list[Account]] = TypeAdapter(list[Account])


class Waiting(FrozenModel):
    """A sign-in that waits for the browser to come back."""

    provider: ProviderId
    verifier: str
    redirect_uri: str
    started_at: float


class AccountView(FrozenModel):
    address: str
    connected_at: float


class ProviderView(FrozenModel):
    """A provider as Customize shows it: whether the owner's client is set (``configured``), the names it is set
    under, and the accounts signed in."""

    id: ProviderId
    name: str
    configured: bool
    client_names: tuple[str, ...]
    accounts: tuple[AccountView, ...]


class CameBack(FrozenModel):
    """What the page the browser comes back to says, and its status."""

    title: str
    text: str
    status: int


def authorize_link(provider: Provider, client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    """The address of the provider's page that asks the owner for a yes."""
    parameters: dict[str, str] = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(provider.scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **dict(provider.extra),
    }
    return f"{provider.authorize_url}?{urlencode(parameters)}"


def client_names(provider: Provider) -> tuple[str, ...]:
    """The names the owner's client is saved under for ``provider``."""
    return (
        (provider.client_id_name,)
        if provider.client_secret_name is None
        else (
            provider.client_id_name,
            provider.client_secret_name,
        )
    )


def _refused(provider: Provider, response: httpx.Response, what: str) -> SignInError:
    """The error of a provider's refusal: its status and its own words for it (an error answer holds no token)."""
    error: object = None
    description: object = None
    try:
        body: object = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        description = body.get("error_description")
    return SignInError(
        f"{provider.name} refused {what}",
        {
            "provider": provider.id,
            "status": response.status_code,
            "error": None if error is None else truncate_middle(str(error), 100),
            "description": truncate_middle(str(description) if description is not None else response.text, 300),
        },
    )


def _json(provider: Provider, response: httpx.Response) -> object:
    try:
        answer: object = response.json()
    except ValueError as error:
        raise SignInError(
            f"{provider.name}'s answer was not JSON", {"provider": provider.id, "status": response.status_code}
        ) from error
    return answer


def _transient(error: Exception) -> bool:
    return isinstance(error, httpx.TransportError)


def _no_hint(_error: Exception) -> float | None:
    return None


class Accounts:
    """The accounts signed in, the sign-ins that wait, and the tokens: ``transport`` None for the network, a test's
    own otherwise."""

    def __init__(self, store: Store, keychain: Keychain, transport: httpx.AsyncBaseTransport | None) -> None:
        self._store: Store = store
        self._keychain: Keychain = keychain
        self._transport: httpx.AsyncBaseTransport | None = transport
        self._waiting: dict[str, Waiting] = {}
        self._refreshing: dict[str, asyncio.Lock] = {}

    def client(self) -> httpx.AsyncClient:
        """A client for the providers: the network, or the test's transport the accounts were made with."""
        return httpx.AsyncClient(
            transport=self._transport, timeout=_HTTP_TIMEOUT_S, headers={"Accept": "application/json"}
        )

    async def _client_of(self, provider: Provider) -> tuple[str, str | None]:
        """The owner's client for ``provider``: its id, and its secret where the provider gives one."""
        names: tuple[str, ...] = client_names(provider)
        values: list[str | None] = [await asyncio.to_thread(resolve_secret, name) for name in names]
        missing: list[str] = [name for name, value in zip(names, values, strict=True) if not value]
        if missing:
            raise SignInError(
                f"{provider.name}'s sign-in needs the client you registered with it: save "
                f"{' and '.join(missing)} (docs/extending.md says where to get them)",
                {"provider": provider.id, "missing": list[JsonValue](missing)},
            )
        client_id: str = values[0] or ""
        return client_id, values[1] if len(values) > 1 else None

    async def accounts(self) -> list[Account]:
        kept: str | None = await self._store.kv_get(_ACCOUNTS_KEY)
        if kept is None:
            return []
        try:
            return _ACCOUNT_LIST.validate_json(kept)
        except ValidationError as error:
            raise SignInError(
                "the list of accounts in the store could not be read", {"errors": error.error_count()}
            ) from error

    async def account_of(self, address: str) -> Account:
        """The account signed in whose address is ``address``, in any case; an error that names the accounts signed in
        when there is none."""
        accounts: list[Account] = await self.accounts()
        found: Account | None = next(
            (account for account in accounts if account.address.casefold() == address.strip().casefold()), None
        )
        if found is None:
            signed: str = ", ".join(f"{account.address} ({PROVIDERS[account.provider].name})" for account in accounts)
            raise SignInError(
                f"no account {address!r} is signed in: "
                + (f"the accounts are {signed}" if signed else "the owner signs one in on Customize › Plugins"),
                {"accounts": len(accounts)},
            )
        return found

    async def _keep(self, accounts: list[Account]) -> None:
        await self._store.kv_set(_ACCOUNTS_KEY, _ACCOUNT_LIST.dump_json(accounts).decode("utf-8"))

    async def providers(self) -> tuple[ProviderView, ...]:
        accounts: list[Account] = await self.accounts()
        views: list[ProviderView] = []
        for provider in PROVIDERS.values():
            names: tuple[str, ...] = client_names(provider)
            values: list[str | None] = [await asyncio.to_thread(resolve_secret, name) for name in names]
            views.append(
                ProviderView(
                    id=provider.id,
                    name=provider.name,
                    configured=all(values),
                    client_names=names,
                    accounts=tuple(
                        AccountView(address=account.address, connected_at=account.connected_at)
                        for account in accounts
                        if account.provider == provider.id
                    ),
                )
            )
        return tuple(views)

    async def start(self, provider_id: ProviderId, port: int, now: float) -> str:
        """Start a sign-in for a browser on this Mac; returns the address of the provider's page."""
        provider: Provider = PROVIDERS[provider_id]
        client_id, _ = await self._client_of(provider)
        self._waiting = {
            state: waiting for state, waiting in self._waiting.items() if now - waiting.started_at < _WAIT_S
        }
        state: str = new_state()
        verifier: str = new_verifier()
        redirect_uri: str = f"http://{provider.loopback}:{port}{CALLBACK_PATH}"
        self._waiting[state] = Waiting(
            provider=provider.id, verifier=verifier, redirect_uri=redirect_uri, started_at=now
        )
        log.info("sign_in_started", provider=provider.id)
        return authorize_link(provider, client_id, redirect_uri, state, challenge_of(verifier))

    async def come_back(self, state: str, code: str | None, error: str | None, now: float) -> CameBack | None:
        """What the page says to the browser that came back with ``state`` at ``now``; None when no sign-in of these
        waits for it (it may be an MCP server's)."""
        waiting: Waiting | None = self._waiting.pop(state, None)
        if waiting is None:
            return None
        provider: Provider = PROVIDERS[waiting.provider]
        if now - waiting.started_at >= _WAIT_S:
            log.info("sign_in_expired", provider=provider.id)
            return CameBack(
                title=f"Not signed in to {provider.name}",
                text=f"The sign-in waited more than {_WAIT_S / 60:g} minutes for the browser to come back. Start it "
                "again in Customize › Plugins.",
                status=400,
            )
        if error is not None or code is None:
            log.info("sign_in_declined", provider=provider.id, error=error)
            return CameBack(
                title=f"Not signed in to {provider.name}",
                text=f"The sign-in ended without a yes ({error or 'no code'}). Start it again in Customize › Plugins.",
                status=200,
            )
        try:
            account: Account = await self._finish(provider, waiting, code)
        except SignInError as failure:
            log.warning("sign_in_failed", provider=provider.id, error=failure.message, context=failure.context)
            return CameBack(
                title=f"Not signed in to {provider.name}",
                text=f"{failure.message}. Start it again in Customize › Plugins.",
                status=400,
            )
        return CameBack(
            title=f"Signed in to {provider.name}",
            text=f"Dot uses {account.address} from now on. You can close this tab.",
            status=200,
        )

    async def _finish(self, provider: Provider, waiting: Waiting, code: str) -> Account:
        client_id, secret = await self._client_of(provider)
        form: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": waiting.redirect_uri,
            "client_id": client_id,
            "code_verifier": waiting.verifier,
        }
        if secret is not None:
            form["client_secret"] = secret
        # A code is good once: the trade is not tried again.
        async with self.client() as client:
            try:
                response: httpx.Response = await client.post(provider.token_url, data=form)
            except httpx.HTTPError as error:
                raise SignInError(
                    f"{provider.name} could not be reached to trade the code",
                    {"provider": provider.id, "error": type(error).__name__},
                ) from error
        if response.status_code != 200:
            raise _refused(provider, response, "the sign-in's code")
        now: float = time.time()
        tokens: Tokens = tokens_from(_json(provider, response), now, None)
        address: str = await self._address(provider, tokens.access_token)
        await self._keychain.save(provider.id, address, tokens)
        others: list[Account] = [
            account
            for account in await self.accounts()
            if not (account.provider == provider.id and account.address == address)
        ]
        account: Account = Account(provider=provider.id, address=address, connected_at=now)
        await self._keep([*others, account])
        log.info("signed_in", provider=provider.id, refresh=tokens.refresh_token is not None)
        return account

    async def _address(self, provider: Provider, access_token: str) -> str:
        async def ask() -> httpx.Response:
            async with self.client() as client:
                return await client.get(provider.profile_url, headers={"Authorization": f"Bearer {access_token}"})

        try:
            response: httpx.Response = await retry_async(ask, _transient, _no_hint, _RETRY, f"{provider.id}_profile")
        except httpx.HTTPError as error:
            raise SignInError(
                f"{provider.name} could not be reached to say who signed in",
                {"provider": provider.id, "error": type(error).__name__},
            ) from error
        if response.status_code != 200:
            raise _refused(provider, response, "to say who signed in")
        return address_from(_json(provider, response))

    async def access_token(self, provider_id: ProviderId, address: str) -> str:
        """The account's access token, refreshed first when it is about to run out."""
        provider: Provider = PROVIDERS[provider_id]
        lock: asyncio.Lock = self._refreshing.setdefault(f"{provider_id}:{address}", asyncio.Lock())
        async with lock:
            tokens: Tokens | None = await self._keychain.read(provider.id, address)
            if tokens is None:
                raise SignInError(
                    f"this account is not signed in to {provider.name}: sign in in Customize › Plugins",
                    {"provider": provider.id},
                )
            if tokens.expires_at - _REFRESH_MARGIN_S > time.time():
                return tokens.access_token
            if tokens.refresh_token is None:
                raise SignInError(
                    f"{provider.name} gave no way to stay signed in: sign in again in Customize › Plugins",
                    {"provider": provider.id},
                )
            fresh: Tokens = await self._refresh(provider, tokens.refresh_token)
            await self._keychain.save(provider.id, address, fresh)
            return fresh.access_token

    async def refreshed_token(self, provider_id: ProviderId, address: str, refused: str) -> str:
        """An access token in place of ``refused``, which the provider turned away before its time: refreshed, unless
        another call refreshed it meanwhile."""
        provider: Provider = PROVIDERS[provider_id]
        lock: asyncio.Lock = self._refreshing.setdefault(f"{provider_id}:{address}", asyncio.Lock())
        async with lock:
            tokens: Tokens | None = await self._keychain.read(provider.id, address)
            if tokens is None:
                raise SignInError(
                    f"this account is not signed in to {provider.name}: sign in in Customize › Plugins",
                    {"provider": provider.id},
                )
            if tokens.access_token != refused:
                return tokens.access_token
            if tokens.refresh_token is None:
                raise SignInError(
                    f"{provider.name} turned the sign-in away and gave no way to stay signed in: sign in again in "
                    "Customize › Plugins",
                    {"provider": provider.id},
                )
            fresh: Tokens = await self._refresh(provider, tokens.refresh_token)
            await self._keychain.save(provider.id, address, fresh)
            return fresh.access_token

    async def _refresh(self, provider: Provider, refresh_token: str) -> Tokens:
        client_id, secret = await self._client_of(provider)
        form: dict[str, str] = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
        if secret is not None:
            form["client_secret"] = secret

        async def ask() -> httpx.Response:
            async with self.client() as client:
                return await client.post(provider.token_url, data=form)

        try:
            response: httpx.Response = await retry_async(ask, _transient, _no_hint, _RETRY, f"{provider.id}_refresh")
        except httpx.HTTPError as error:
            raise SignInError(
                f"{provider.name} could not be reached to refresh the sign-in",
                {"provider": provider.id, "error": type(error).__name__},
            ) from error
        if response.status_code != 200:
            raise _refused(provider, response, "to refresh the sign-in (sign in again in Customize › Plugins)")
        return tokens_from(_json(provider, response), time.time(), refresh_token)

    async def forget(self, provider_id: ProviderId, address: str) -> None:
        """Sign the account out: its tokens leave the Keychain and it leaves the list."""
        await self._keychain.forget(provider_id, address)
        await self._keep(
            [
                account
                for account in await self.accounts()
                if not (account.provider == provider_id and account.address == address)
            ]
        )
        log.info("signed_out", provider=provider_id)
