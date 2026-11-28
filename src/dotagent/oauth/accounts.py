"""Accounts of Google and Microsoft signed in through their official sign-ins (item 422).

A sign-in starts for the owner's browser on this Mac (:meth:`Accounts.start`): the provider's page asks for a yes, then
sends the browser back to the daemon's ``/oauth/callback`` with a code, which is traded for tokens with PKCE and the
client the owner registered (:mod:`dotagent.oauth.providers`). The account is named by its address, its tokens go to
the Keychain (:mod:`dotagent.oauth.keychain`) and the list of accounts, with no secret in it, to the store. A tool asks
for an account's access token (:meth:`Accounts.access_token`), which is refreshed once it is about to run out. Nothing
here logs a token, a code or an address."""

import asyncio
import secrets
import time
from collections.abc import Callable, Mapping
from typing import Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

from dotagent.core.errors import DotError, SignInError, StoreError
from dotagent.core.logs import get_logger
from dotagent.core.retry import RetryPolicy, retry_async
from dotagent.core.text import truncate_middle
from dotagent.core.types import FrozenModel
from dotagent.mcp.signin import CALLBACK_PATH
from dotagent.oauth.keychain import Keychain
from dotagent.oauth.pkce import challenge_of, new_state, new_verifier
from dotagent.oauth.providers import PROVIDERS, Provider, ProviderId
from dotagent.oauth.tokens import Account, Tokens, address_from, granted, tokens_from
from dotagent.secrets import resolve_secret
from dotagent.store.db import Store

log = get_logger(__name__)

_ACCOUNTS_KEY: str = "oauth.accounts"
# How long a sign-in waits for the browser to come back.
WAIT_S: float = 600.0
# How much of an error code and of its description a page shows: what the provider sent back in the address.
_CODE_CHARS: int = 100
_DESCRIPTION_CHARS: int = 200
# The error a provider sends back when the owner presses No: it says nothing the page's own words do not.
_NO: str = "access_denied"
# An access token is refreshed this long before it runs out, so that a call made with it does not meet its end.
_REFRESH_MARGIN_S: float = 120.0
_HTTP_TIMEOUT_S: float = 30.0
_RETRY: RetryPolicy = RetryPolicy(attempts=3, base_delay_s=1.0, max_delay_s=8.0)
_ACCOUNT_LIST: TypeAdapter[list[Account]] = TypeAdapter(list[Account])
_HTTP_OK: int = 200
_HTTP_BAD_REQUEST: int = 400
# What Google's revoke call answers (400) for a token that is already revoked or expired: no grant is left to end.
_ALREADY_INVALID: str = "invalid_token"
# The sign-outs a provider did not take (item 1312): the store's record, with no token in it, and how long they are
# asked for. The tokens wait in the Keychain under ``pending-revoke-<id>`` until the provider took them or time is up.
_PENDING_KEY: str = "oauth.pending_revokes"
GIVE_UP_AFTER_S: float = 7 * 24 * 3600.0
RETRY_EVERY_S: float = 3600.0
type RevokeOutcome = Literal["revoked", "already_invalid", "failed"]


class PendingRevoke(FrozenModel):
    """A sign-out whose revoke the provider has not taken yet: ``id`` names the tokens parked in the Keychain."""

    id: str
    provider: ProviderId
    since: float


class _RevokeAnswer(BaseModel):
    """What a provider's revoke call answers when it refuses: its error code (``{"error": "invalid_token"}``)."""

    model_config = ConfigDict(extra="ignore")
    error: str | None = None


_PENDING_LIST: TypeAdapter[list[PendingRevoke]] = TypeAdapter(list[PendingRevoke])


def _parked_address(pending_id: str) -> str:
    """The place in the Keychain where the tokens of a sign-out wait for their revoke."""
    return f"pending-revoke-{pending_id}"


class Waiting(FrozenModel):
    """A sign-in that waits for the browser to come back."""

    provider: ProviderId
    verifier: str
    redirect_uri: str
    started_at: float


class AccountView(FrozenModel):
    """``drafts``: whether its sign-in allowed drafts and sending (item 2047)."""

    address: str
    connected_at: float
    drafts: bool


class ProviderView(FrozenModel):
    """A provider as Customize shows it: whether the owner's client is set (``configured``), the names it is set
    under, and the accounts signed in."""

    id: ProviderId
    name: str
    configured: bool
    client_names: tuple[str, ...]
    accounts: tuple[AccountView, ...]


type CameBackKind = Literal["signed_in", "declined", "expired", "failed"]


class CameBack(FrozenModel):
    """How the browser's return for an account's sign-in went: it signed in (``address`` is the account's), the owner
    said no, it came after :data:`WAIT_S`, or it failed. ``reason`` is the provider's own words when it gave any (a
    failure's message with the error code and description of the refusal; a no's, unless it is the plain No). The page
    that says it is :mod:`dotagent.server.callback_page`."""

    kind: CameBackKind
    provider: ProviderId
    address: str | None
    reason: str | None


def denial_reason(error: str | None, description: str | None) -> str | None:
    """The words of a sign-in that ended without a yes, from what the provider put in the address of the way back: its
    error code and description, each cut to a line's length; None for the plain No (``access_denied``) and for a way
    back that carries neither."""
    if error is None or error == _NO:
        return None
    code: str = truncate_middle(error, _CODE_CHARS)
    return code if not description else f"{code}: {truncate_middle(description, _DESCRIPTION_CHARS)}"


def refresh_advice(provider_id: ProviderId, error: str | None) -> str:
    """What the owner does about a refresh the provider refused (item 1343). Google's ``invalid_grant`` is a refresh
    token that ran out or was taken away; a client whose app is still in Testing at Google gets refresh tokens that
    last 7 days, so a sign-in made that way dies every week until the app is published."""
    if provider_id == "google" and error == "invalid_grant":
        return (
            "(the sign-in ran out or was taken away: a Google client whose app is still in Testing signs in for 7 "
            "days only, so publish the app at Google Auth Platform › Audience, then sign in again in "
            "Settings › Connectors)"
        )
    return "(sign in again in Settings › Connectors)"


def authorize_link(
    provider: Provider,
    scopes: tuple[str, ...],
    hint: tuple[tuple[str, str], ...],
    client_id: str,
    redirect_uri: str,
    state: str,
    challenge: str,
) -> str:
    """The address of the provider's page that asks the owner for a yes to ``scopes``; ``hint`` the parameters that
    name the account (``login_hint``), none for a new one."""
    parameters: dict[str, str] = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **dict(provider.extra),
        **dict(hint),
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


class _Refusal(SignInError):
    """A provider's refusal: its status, and its own words for it, the error code and the description it gave (None
    for what it did not give). Only these words, and no other detail of a failure, go to the page that says it."""

    def __init__(
        self, message: str, context: Mapping[str, JsonValue], error: str | None, description: str | None
    ) -> None:
        super().__init__(message, context)
        self.error: str | None = error
        self.description: str | None = description


def _refused(provider: Provider, response: httpx.Response, what: str) -> _Refusal:
    """The error of a provider's refusal (an error answer holds no token): the text of an answer that is no OAuth
    error is kept for the log alone."""
    error: object = None
    description: object = None
    try:
        body: object = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        description = body.get("error_description")
    code: str | None = None if error is None else truncate_middle(str(error), _CODE_CHARS)
    words: str | None = None if description is None else truncate_middle(str(description), _DESCRIPTION_CHARS)
    return _Refusal(
        f"{provider.name} refused {what}",
        {
            "provider": provider.id,
            "status": response.status_code,
            "error": code,
            "description": words,
            "body": truncate_middle(response.text, 300) if code is None and words is None else None,
        },
        code,
        words,
    )


def _failure_reason(failure: SignInError) -> str:
    """What a failed sign-in tells the owner: the failure's message, with the error code and the description of a
    provider's refusal where it gave them."""
    if not isinstance(failure, _Refusal):
        return failure.message
    given: list[str] = [part for part in (failure.error, failure.description) if part is not None]
    return f"{failure.message}: {' - '.join(given)}" if given else failure.message


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
                + (f"the accounts are {signed}" if signed else "the owner signs one in on Settings › Connectors"),
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
                        AccountView(address=account.address, connected_at=account.connected_at, drafts=account.drafts)
                        for account in accounts
                        if account.provider == provider.id
                    ),
                )
            )
        return tuple(views)

    async def start(self, provider_id: ProviderId, port: int, now: float) -> str:
        """Start a sign-in for a browser on this Mac, which reads only; returns the address of the provider's page."""
        provider: Provider = PROVIDERS[provider_id]
        return await self._start(provider, provider.scopes, (), port, now)

    async def start_drafts(self, provider_id: ProviderId, address: str, port: int, now: float) -> str:
        """Start the sign-in that allows drafts and sending for the account ``address`` (item 2047): what a sign-in
        asks, and the provider's draft scopes; returns the address of the provider's page, which names the account."""
        provider: Provider = PROVIDERS[provider_id]
        account: Account = await self.account_of(address)
        if account.provider != provider.id:
            raise SignInError(f"{account.address} is not a {provider.name} account", {"provider": provider.id})
        hint: tuple[tuple[str, str], ...] = (("login_hint", account.address),)
        return await self._start(provider, (*provider.scopes, *provider.draft_scopes), hint, port, now)

    async def _start(
        self, provider: Provider, scopes: tuple[str, ...], hint: tuple[tuple[str, str], ...], port: int, now: float
    ) -> str:
        client_id, _ = await self._client_of(provider)
        self._waiting = {
            state: waiting for state, waiting in self._waiting.items() if now - waiting.started_at < WAIT_S
        }
        state: str = new_state()
        verifier: str = new_verifier()
        redirect_uri: str = f"http://{provider.loopback}:{port}{CALLBACK_PATH}"
        self._waiting[state] = Waiting(
            provider=provider.id, verifier=verifier, redirect_uri=redirect_uri, started_at=now
        )
        log.info("sign_in_started", provider=provider.id, drafts=bool(hint))
        return authorize_link(provider, scopes, hint, client_id, redirect_uri, state, challenge_of(verifier))

    async def come_back(
        self, state: str, code: str | None, error: str | None, error_description: str | None, now: float
    ) -> CameBack | None:
        """How the browser that came back with ``state`` at ``now`` fared: None when no sign-in of these waits for it
        (it may be an MCP server's). ``error`` and ``error_description`` are what the provider put in the address when
        the owner said no."""
        waiting: Waiting | None = self._waiting.pop(state, None)
        if waiting is None:
            return None
        provider: Provider = PROVIDERS[waiting.provider]
        if now - waiting.started_at >= WAIT_S:
            log.info("sign_in_expired", provider=provider.id)
            return CameBack(kind="expired", provider=provider.id, address=None, reason=None)
        if error is not None or code is None:
            log.info("sign_in_declined", provider=provider.id, error=error)
            return CameBack(
                kind="declined",
                provider=provider.id,
                address=None,
                reason=denial_reason(error, error_description),
            )
        try:
            account: Account = await self._finish(provider, waiting, code)
        except SignInError as failure:
            log.warning("sign_in_failed", provider=provider.id, error=failure.message, context=failure.context)
            return CameBack(kind="failed", provider=provider.id, address=None, reason=_failure_reason(failure))
        return CameBack(kind="signed_in", provider=provider.id, address=account.address, reason=None)

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
        # Drafts are allowed when the token holds the draft scopes: the owner may untick one on Google's page.
        account: Account = Account(
            provider=provider.id,
            address=address,
            connected_at=now,
            drafts=granted(provider.draft_scopes, tokens.scope),
        )
        await self._keep([*others, account])
        log.info("signed_in", provider=provider.id, refresh=tokens.refresh_token is not None, drafts=account.drafts)
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
                    f"this account is not signed in to {provider.name}: sign in in Settings › Connectors",
                    {"provider": provider.id},
                )
            if tokens.expires_at - _REFRESH_MARGIN_S > time.time():
                return tokens.access_token
            if tokens.refresh_token is None:
                raise SignInError(
                    f"{provider.name} gave no way to stay signed in: sign in again in Settings › Connectors",
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
                    f"this account is not signed in to {provider.name}: sign in in Settings › Connectors",
                    {"provider": provider.id},
                )
            if tokens.access_token != refused:
                return tokens.access_token
            if tokens.refresh_token is None:
                raise SignInError(
                    f"{provider.name} turned the sign-in away and gave no way to stay signed in: sign in again in "
                    "Settings › Connectors",
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
            refusal: _Refusal = _refused(provider, response, "to refresh the sign-in")
            raise _Refusal(
                f"{refusal.message} {refresh_advice(provider.id, refusal.error)}",
                refusal.context,
                refusal.error,
                refusal.description,
            )
        return tokens_from(_json(provider, response), time.time(), refresh_token)

    async def forget(self, provider_id: ProviderId, address: str) -> bool:
        """Sign the account out and say whether the provider ended the grant (item 1312). The account leaves the list
        and its tokens the account's place in the Keychain, whatever the provider answers: sign-out never fails for a
        provider that cannot be reached. Where the provider has a revoke call (:meth:`_revoke`) the tokens go first
        to a place of their own in the Keychain, with a record in the store, and stay there until the provider took
        the token or said it was already invalid; a provider that could not be asked is asked again by
        :meth:`retry_pending_revokes` for :data:`GIVE_UP_AFTER_S`. It waits for a refresh of the account's tokens that
        is under way, which would write fresh ones into the Keychain after they were removed, with no account left in
        the list to sign out of (item 558)."""
        provider: Provider = PROVIDERS[provider_id]
        lock: asyncio.Lock = self._refreshing.setdefault(f"{provider_id}:{address}", asyncio.Lock())
        async with lock:
            tokens: Tokens | None = await self._keychain.read(provider_id, address)
            parked: str | None = None
            if tokens is not None and provider.revoke_url is not None:
                parked = await self._park(provider_id, tokens)
            try:
                await self._keychain.forget(provider_id, address)
            except SignInError:
                # The account stays signed in, so the copy parked for its revoke must not outlive this attempt.
                if parked is not None:
                    await self._unpark(provider_id, parked)
                raise
            await self._keep(
                [
                    account
                    for account in await self.accounts()
                    if not (account.provider == provider_id and account.address == address)
                ]
            )
        outcome: RevokeOutcome | None = None
        if tokens is not None and provider.revoke_url is not None:
            outcome = await self._revoke(provider, tokens)
        revoked: bool = outcome is not None and outcome != "failed"
        if parked is not None and revoked:
            await self._unpark(provider_id, parked)
        log.info("signed_out", provider=provider_id, revoked=revoked, kept_to_retry=parked is not None and not revoked)
        return revoked

    async def _revoke(self, provider: Provider, tokens: Tokens) -> RevokeOutcome:
        """Give the refresh token (else the access token) back to the provider, which ends the grant: a copy of the
        token taken before the owner signed out mints no more. ``already_invalid`` is the provider's own word that the
        token is already revoked or expired (400 ``invalid_token``): nothing is left to end. Anything else is
        ``failed`` and logged with the provider's status and error code, never the token. Microsoft has no such call
        for a client like Dot's (``revoke_url`` None): the app's consent is taken away at the owner's account."""
        if provider.revoke_url is None:
            raise SignInError(f"{provider.name} has no call that ends a sign-in", {"provider": provider.id})
        token: str = tokens.refresh_token or tokens.access_token
        try:
            async with self.client() as client:
                response: httpx.Response = await client.post(provider.revoke_url, data={"token": token})
        except httpx.HTTPError as error:
            log.warning("sign_out_not_revoked", provider=provider.id, error=type(error).__name__)
            return "failed"
        if response.status_code == _HTTP_OK:
            return "revoked"
        try:
            given: str | None = _RevokeAnswer.model_validate_json(response.content).error
        except ValidationError:
            given = None
        if response.status_code == _HTTP_BAD_REQUEST and given == _ALREADY_INVALID:
            log.info("sign_out_already_revoked", provider=provider.id)
            return "already_invalid"
        log.warning(
            "sign_out_not_revoked",
            provider=provider.id,
            status=response.status_code,
            error=None if given is None else truncate_middle(given, _CODE_CHARS),
        )
        return "failed"

    async def _pending(self) -> list[PendingRevoke]:
        kept: str | None = await self._store.kv_get(_PENDING_KEY)
        if kept is None:
            return []
        try:
            return _PENDING_LIST.validate_json(kept)
        except ValidationError as error:
            raise SignInError(
                "the list of sign-outs the provider has not taken yet could not be read",
                {"errors": error.error_count()},
            ) from error

    async def _change_pending(self, change: Callable[[list[PendingRevoke]], list[PendingRevoke]]) -> None:
        def update(kept: str | None) -> str:
            try:
                before: list[PendingRevoke] = [] if kept is None else _PENDING_LIST.validate_json(kept)
            except ValidationError as error:
                raise SignInError(
                    "the list of sign-outs the provider has not taken yet could not be read",
                    {"errors": error.error_count()},
                ) from error
            return _PENDING_LIST.dump_json(change(before)).decode("utf-8")

        await self._store.kv_update(_PENDING_KEY, update)

    async def _park(self, provider_id: ProviderId, tokens: Tokens) -> str | None:
        """Keep ``tokens`` under a place of their own in the Keychain and a record in the store, so that a revoke the
        provider does not take can be asked again; its id, or None when the Keychain or the store would not keep them
        (logged: the sign-out goes on, with the one try it has)."""
        pending: PendingRevoke = PendingRevoke(id=secrets.token_hex(8), provider=provider_id, since=time.time())
        try:
            await self._keychain.save(provider_id, _parked_address(pending.id), tokens)
            await self._change_pending(lambda before: [*before, pending])
        except (SignInError, StoreError) as error:
            log.warning("sign_out_revoke_not_kept", provider=provider_id, error=str(error))
            return None
        return pending.id

    async def _unpark(self, provider_id: ProviderId, pending_id: str) -> None:
        """Forget the parked tokens and their record: the provider took the token, or it is not to be asked again."""
        try:
            await self._keychain.forget(provider_id, _parked_address(pending_id))
            await self._change_pending(lambda before: [item for item in before if item.id != pending_id])
        except (SignInError, StoreError) as error:
            log.warning("sign_out_revoke_record_not_cleared", provider=provider_id, error=str(error))

    async def retry_pending_revokes(self, now: float) -> int:
        """Ask the providers again for the tokens of the accounts signed out whose revoke they did not take: each is
        settled when the provider takes it or says it is already invalid, and given up, its tokens forgotten, once
        :data:`GIVE_UP_AFTER_S` have passed since the sign-out. Returns how many were settled."""
        settled: int = 0
        for pending in await self._pending():
            if now - pending.since > GIVE_UP_AFTER_S:
                log.warning("sign_out_revoke_given_up", provider=pending.provider, since=pending.since)
                await self._unpark(pending.provider, pending.id)
                settled += 1
                continue
            try:
                tokens: Tokens | None = await self._keychain.read(pending.provider, _parked_address(pending.id))
            except SignInError as error:
                log.warning("sign_out_revoke_not_read", provider=pending.provider, error=str(error))
                continue
            if tokens is not None and await self._revoke(PROVIDERS[pending.provider], tokens) == "failed":
                continue
            await self._unpark(pending.provider, pending.id)
            settled += 1
        return settled


async def keep_revoking(accounts: Accounts, stopping: asyncio.Event) -> None:
    """Ask for the sign-outs the providers did not take (:meth:`Accounts.retry_pending_revokes`) as the daemon starts
    and every :data:`RETRY_EVERY_S` after, until it stops. A round that fails is logged and the next one still runs: a
    fault of Dot's own (a bug, a database error) is logged with its traceback and must not end the task for the rest of
    the daemon's life, as the phone keeper's rounds do not (item 1313). A round is an hour apart, so no longer wait is
    added for the ones that fail."""
    while not stopping.is_set():
        try:
            await accounts.retry_pending_revokes(time.time())
        except DotError as error:
            log.warning("sign_out_revoke_retry_failed", error=str(error))
        except Exception as error:
            log.error("sign_out_revoke_retry_crashed", error=str(error), error_type=type(error).__name__, exc_info=True)
        try:
            await asyncio.wait_for(stopping.wait(), RETRY_EVERY_S)
        except TimeoutError:
            continue
