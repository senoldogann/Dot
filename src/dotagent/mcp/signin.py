"""Signing in to an MCP server reached at a URL (item 279), as MCP's authorization asks: OAuth 2.1 with PKCE, Dot
registered with the server's authorization server on the first sign-in (dynamic client registration), the code brought
back to Dot's own page, ``/oauth/callback``, by the browser that signed in. The flow itself is the MCP library's
(:class:`mcp.client.auth.oauth2.OAuthClientProvider`); Dot keeps what it gives and answers its two questions: where to
send the owner (the link the Connectors page shows) and what came back (the code the callback hands over).

What a sign-in gave is kept in Dot's home, one file per server readable by the owner alone (:func:`sign_in_path`): the
tokens, the client the authorization server registered, the redirect address it was registered with, and when the access
token runs out. The library renews an access token with the refresh token before it runs out; that time is kept with the
tokens, so that a token is renewed after a restart of the daemon too. A run never signs in: when a server asks for a new
sign-in while Dot works, the server is left out with a message that sends the owner to Customize › Connectors
(:func:`kept_sign_in`).
"""

import asyncio
import json
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from mcp.client.auth.oauth2 import OAuthClientProvider, OAuthContext
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
)
from pydantic import AnyUrl, ValidationError

from dotagent.config import McpServerConfig
from dotagent.core.errors import McpError
from dotagent.core.files import write_whole
from dotagent.core.logs import get_logger
from dotagent.core.paths import mcp_sign_ins_dir
from dotagent.core.types import FrozenModel

log = get_logger(__name__)

# The path of Dot's page the authorization server sends the browser back to, on the address the owner signed in from.
CALLBACK_PATH: str = "/oauth/callback"
# How long a sign-in waits for the owner to come back from the authorization server's page.
SIGN_IN_WAIT_S: float = 600.0
_DIR_MODE: int = 0o700
_CLIENT_NAME: str = "Dot"
NOT_SIGNED_IN: str = "the MCP server needs you to sign in first: Customize › Connectors › Sign in again"
SIGN_IN_AGAIN: str = "the MCP server asks you to sign in again: Customize › Connectors › Sign in again"
# One change of a server's file at a time in this process: each reads it, changes it and replaces it.
_FILE_LOCK: threading.Lock = threading.Lock()


class StoredSignIn(FrozenModel):
    """What signing in to one server gave Dot: the address its client was registered to come back to, the client, the
    tokens, and when the access token runs out (seconds since 1970; ``None`` when the server did not say)."""

    redirect_uri: str
    client: OAuthClientInformationFull | None
    tokens: OAuthToken | None
    expires_at: float | None
    # Where the authorization server is and what it says of itself, found as the owner signed in (the review of every
    # module): without them a token that ran out is renewed at ``/token`` of the MCP server's own origin, which a server
    # whose token endpoint is elsewhere answers 404, and Dot asked to sign in again at every start.
    metadata: OAuthMetadata | None = None
    auth_server_url: str | None = None


def sign_in_path(server: str) -> Path:
    return mcp_sign_ins_dir() / f"{server}.json"


def read_sign_in(path: Path) -> StoredSignIn | None:
    """The sign-in kept at ``path``; ``None`` when there is none. Raises McpError when the file cannot be read."""
    if not path.exists():
        return None
    try:
        return StoredSignIn.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as error:
        raise McpError(
            f"what signing in to the MCP server gave cannot be read ({type(error).__name__})", {"path": str(path)}
        ) from error


def _write(path: Path, record: StoredSignIn) -> None:
    path.parent.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    write_whole(path, json.dumps(record.model_dump(mode="json"), indent=2) + "\n")


def forget_sign_in(path: Path) -> None:
    """Delete what signing in to a server gave: Dot keeps no token of a server the owner removed."""
    with _FILE_LOCK:
        path.unlink(missing_ok=True)


def signed_in(path: Path) -> bool:
    """Whether Dot keeps tokens for the server whose sign-in is at ``path``."""
    stored: StoredSignIn | None = read_sign_in(path)
    return stored is not None and stored.tokens is not None


class FileTokenStorage:
    """The library's ``TokenStorage`` over a server's file. A client registered for another redirect address (the
    owner signed in from the phone before, from the Mac now) is not handed back, so the library registers anew."""

    def __init__(self, path: Path, redirect_uri: str) -> None:
        self._path: Path = path
        self._redirect_uri: str = redirect_uri
        # The provider's context, whose authorization server is kept with the tokens it gave (:meth:`set_tokens`).
        self.context: OAuthContext | None = None

    def _read(self) -> StoredSignIn:
        stored: StoredSignIn | None = read_sign_in(self._path)
        if stored is None:
            return StoredSignIn(redirect_uri=self._redirect_uri, client=None, tokens=None, expires_at=None)
        return stored

    def _change(self, change: Callable[[StoredSignIn], StoredSignIn]) -> None:
        with _FILE_LOCK:
            _write(self._path, change(self._read()))

    async def get_tokens(self) -> OAuthToken | None:
        return (await asyncio.to_thread(self._read)).tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        expires_at: float | None = None if tokens.expires_in is None else time.time() + tokens.expires_in
        found: dict[str, object] = {}
        if self.context is not None and self.context.oauth_metadata is not None:
            found = {"metadata": self.context.oauth_metadata, "auth_server_url": self.context.auth_server_url}
        await asyncio.to_thread(
            self._change,
            lambda stored: stored.model_copy(update={"tokens": tokens, "expires_at": expires_at, **found}),
        )

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        stored: StoredSignIn = await asyncio.to_thread(self._read)
        if stored.client is None or stored.redirect_uri != self._redirect_uri:
            return None
        return stored.client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        await asyncio.to_thread(
            self._change,
            lambda stored: stored.model_copy(update={"client": client_info, "redirect_uri": self._redirect_uri}),
        )


def callback_address(origin: str) -> str:
    """The address the authorization server sends the browser back to, on the origin of the page that signed in."""
    return f"{origin.rstrip('/')}{CALLBACK_PATH}"


def _client_metadata(redirect_uri: str) -> OAuthClientMetadata:
    return OAuthClientMetadata(
        client_name=_CLIENT_NAME,
        redirect_uris=[AnyUrl(redirect_uri)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )


def _provider(
    config: McpServerConfig,
    storage: FileTokenStorage,
    redirect_uri: str,
    redirect: Callable[[str], Awaitable[None]],
    callback: Callable[[], Awaitable[AuthorizationCodeResult]],
    expires_at: float | None,
    kept: StoredSignIn | None = None,
) -> OAuthClientProvider:
    if config.url is None:
        raise McpError("only an MCP server at a URL signs in", {"server": config.name})
    provider: OAuthClientProvider = OAuthClientProvider(
        server_url=config.url,
        client_metadata=_client_metadata(redirect_uri),
        storage=storage,
        redirect_handler=redirect,
        callback_handler=callback,
    )
    # The library reads the tokens back from the file but not when they run out, which it learns only from a token it
    # receives itself; told it here, it renews a token that ran out while the daemon was stopped instead of using it.
    provider.context.token_expiry_time = expires_at
    # Where the token is renewed, as the sign-in found it: the token endpoint, not ``/token`` of the server's origin.
    if kept is not None and kept.metadata is not None:
        provider.context.oauth_metadata = kept.metadata
        provider.context.auth_server_url = kept.auth_server_url
    storage.context = provider.context
    return provider


def kept_sign_in(config: McpServerConfig) -> OAuthClientProvider:
    """The sign-in the connections of runs use: the kept tokens, renewed when they run out, and never a new sign-in, as
    no one would open its page: a server that asks for one is left out with :data:`SIGN_IN_AGAIN`. Raises McpError
    when Dot keeps no tokens for the server, :data:`NOT_SIGNED_IN`."""
    stored: StoredSignIn | None = read_sign_in(sign_in_path(config.name))
    if stored is None or stored.tokens is None:
        raise McpError(NOT_SIGNED_IN, {"server": config.name})

    async def refuse(_link: str) -> None:
        raise McpError(SIGN_IN_AGAIN, {"server": config.name})

    async def no_code() -> AuthorizationCodeResult:
        raise McpError(SIGN_IN_AGAIN, {"server": config.name})

    storage: FileTokenStorage = FileTokenStorage(sign_in_path(config.name), stored.redirect_uri)
    return _provider(config, storage, stored.redirect_uri, refuse, no_code, stored.expires_at, stored)


class _Waiting(FrozenModel):
    server: str
    link: str


# The hosts of this Mac, where an authorization server of one's own may answer over plain http.
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def checked_link(link: str) -> str:
    """``link``, the authorization server's page the owner is sent to, once it is an https address, or an http one on
    this Mac. The page opens it as a link, so anything else (``javascript:``, ``data:``, plain http to another host)
    is refused with :class:`McpError`: the authorization server is the MCP server's, not Dot's."""
    parts = urlparse(link)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS):
        return link
    raise McpError(
        "the authorization server's page is not an https address",
        {"scheme": parts.scheme, "host": parts.hostname or ""},
    )


def _state_of(link: str) -> str:
    states: list[str] = parse_qs(urlparse(link).query).get("state", [])
    if len(states) != 1 or states[0] == "":
        raise McpError("the authorization server's page names no state to come back with", {"link": link})
    return states[0]


class SignIns:
    """The sign-ins under way, by the state each sent the browser off with: the link the owner opens, and the code the
    callback hands back to the flow that waits for it."""

    def __init__(self) -> None:
        self._codes: dict[str, asyncio.Future[AuthorizationCodeResult]] = {}
        self._waiting: dict[str, _Waiting] = {}

    def link_of(self, server: str) -> str | None:
        """The link of the sign-in under way to ``server``; ``None`` when none waits for the owner."""
        return next((waiting.link for waiting in self._waiting.values() if waiting.server == server), None)

    def flow(self, config: McpServerConfig, origin: str, on_link: Callable[[str], None]) -> OAuthClientProvider:
        """The library's flow for a sign-in from a page on ``origin``, which only a server that answers 401 starts:
        ``on_link`` gets the authorization server's page to send the owner to, and the flow then waits for the
        callback with the code (at most :data:`SIGN_IN_WAIT_S`)."""
        redirect_uri: str = callback_address(origin)
        path: Path = sign_in_path(config.name)
        stored: StoredSignIn | None = read_sign_in(path)
        pending: dict[str, str] = {}

        async def redirect(link: str) -> None:
            state: str = _state_of(checked_link(link))
            self._codes[state] = asyncio.get_running_loop().create_future()
            self._waiting[state] = _Waiting(server=config.name, link=link)
            pending["state"] = state
            log.info("mcp_sign_in_link", server=config.name)
            on_link(link)

        async def callback() -> AuthorizationCodeResult:
            state: str = pending["state"]
            try:
                async with asyncio.timeout(SIGN_IN_WAIT_S):
                    return await self._codes[state]
            except TimeoutError as error:
                raise McpError(
                    "no one came back from the sign-in page in time",
                    {"server": config.name, "waited_s": SIGN_IN_WAIT_S},
                ) from error
            finally:
                self._codes.pop(state, None)
                self._waiting.pop(state, None)

        return _provider(
            config,
            FileTokenStorage(path, redirect_uri),
            redirect_uri,
            redirect,
            callback,
            None if stored is None else stored.expires_at,
            stored,
        )

    def answer(self, state: str, code: str | None, issuer: str | None, error: str | None) -> str | None:
        """Hand what the authorization server sent back to the sign-in that waits for it; the server's name, or
        ``None`` when no sign-in waits with ``state``. An ``error`` (the owner said no) ends the sign-in with it."""
        waiting: asyncio.Future[AuthorizationCodeResult] | None = self._codes.get(state)
        sign_in: _Waiting | None = self._waiting.get(state)
        if waiting is None or sign_in is None or waiting.done():
            return None
        if error is not None or code is None:
            waiting.set_exception(
                McpError("the sign-in was not completed", {"server": sign_in.server, "error": error or "no code"})
            )
        else:
            waiting.set_result(AuthorizationCodeResult(code=code, state=state, iss=issuer))
        return sign_in.server
