"""Who may reach the server: this Mac only, pages of the server itself only, and only with the bearer token.

Binding to a loopback address keeps other machines out. The Host check defeats DNS rebinding, the Origin check keeps
the pages of other sites from using the API through the browser, and the token keeps other programs of this Mac out.
"""

import hmac
import ipaddress
import os
import secrets
from pathlib import Path

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dotagent.core.errors import ConfigError
from dotagent.core.types import FrozenModel

API_ROOT: str = "/api"
API_PREFIX: str = f"{API_ROOT}/"
PUBLIC_PATHS: frozenset[str] = frozenset({f"{API_ROOT}/health"})

_PRIVATE_FILE_MODE: int = 0o600
_PERMISSION_BITS: int = 0o777
_OTHERS_BITS: int = 0o077
_TOKEN_BYTES: int = 32
_LOOPBACK_NAME: str = "localhost"
_HTTP_MISDIRECTED: int = 421
_HTTP_FORBIDDEN: int = 403
_HTTP_UNAUTHORIZED: int = 401

# The pages of the UI load only their own scripts and styles, and nothing can frame them.
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    (
        "Content-Security-Policy",
        "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' blob: data:; manifest-src 'self'; "
        # The desktop shell's bridge (Tauri IPC) is a connection the page makes; a browser never sees these sources.
        "connect-src 'self' ipc: http://ipc.localhost; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    ),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)


class Refusal(FrozenModel):
    status: int
    message: str


def require_loopback(host: str) -> None:
    """Refuse a bind address that other machines can reach."""
    if host == _LOOPBACK_NAME:
        return
    try:
        address: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ConfigError(
            "the server host must be localhost or a loopback address", {"host": host, "setting": "[server] host"}
        ) from exc
    if not address.is_loopback:
        raise ConfigError(
            "the server host must be localhost or a loopback address; Dot controls this Mac and is not served to "
            "the network",
            {"host": host, "setting": "[server] host"},
        )


def local_hosts(port: int) -> frozenset[str]:
    """The ``Host`` header values with which a browser on this Mac reaches the server."""
    return frozenset({f"127.0.0.1:{port}", f"{_LOOPBACK_NAME}:{port}", f"[::1]:{port}"})


def host_is_local(host_header: str | None, port: int, reach: frozenset[str]) -> bool:
    """Whether the request names this server: its loopback address, or one of the names a reverse proxy on the
    owner's own network presents it as (``[server] reach``)."""
    if host_header is None:
        return False
    name: str = host_header.lower()
    return name in local_hosts(port) or name in {host.lower() for host in reach}


def origin_is_local(origin: str | None, port: int, reach: frozenset[str]) -> bool:
    """A request without ``Origin`` did not come from a web page; one with it must come from a page of this server,
    reached directly or through a name of ``reach`` over HTTPS."""
    if origin is None:
        return True
    allowed: set[str] = {f"http://{host}" for host in local_hosts(port)} | {f"https://{host.lower()}" for host in reach}
    return origin.lower() in allowed


def bearer_token(authorization: str | None) -> str | None:
    """The token of an ``Authorization: Bearer`` header; ``None`` for any other header."""
    if authorization is None:
        return None
    scheme, _, value = authorization.partition(" ")
    cleaned: str = value.strip()
    return cleaned if scheme.lower() == "bearer" and cleaned != "" else None


def token_matches(expected: str, supplied: str | None) -> bool:
    return supplied is not None and hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))


def is_api_path(path: str) -> bool:
    return path == API_ROOT or path.startswith(API_PREFIX)


def needs_token(path: str) -> bool:
    return is_api_path(path) and path not in PUBLIC_PATHS


def check_request(
    path: str,
    host: str | None,
    origin: str | None,
    authorization: str | None,
    token: str,
    port: int,
    reach: frozenset[str],
) -> Refusal | None:
    """Why a request must not be served, or ``None`` when it may be."""
    if not host_is_local(host, port, reach):
        return Refusal(status=_HTTP_MISDIRECTED, message="this server answers only to its loopback address")
    if not origin_is_local(origin, port, reach):
        return Refusal(status=_HTTP_FORBIDDEN, message="requests from other origins are refused")
    if needs_token(path) and not token_matches(token, bearer_token(authorization)):
        return Refusal(status=_HTTP_UNAUTHORIZED, message="a valid bearer token is required")
    return None


def read_token(path: Path) -> str:
    """The token stored at ``path``; refused when the file is empty or other users could read it."""
    status: os.stat_result = path.stat()
    if status.st_uid != os.getuid() or status.st_mode & _OTHERS_BITS:
        raise ConfigError(
            "the API token file must belong to you and be readable by nobody else",
            {"path": str(path), "mode": oct(status.st_mode & _PERMISSION_BITS), "fix": f"chmod 600 {path}"},
        )
    token: str = path.read_text(encoding="utf-8").strip()
    if token == "":
        raise ConfigError("the API token file is empty; delete it and a new token is made", {"path": str(path)})
    return token


def read_or_create_token(path: Path) -> str:
    """The bearer token of this installation. The first call creates it, readable by this user only."""
    try:
        descriptor: int = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _PRIVATE_FILE_MODE)
    except FileExistsError:
        return read_token(path)
    token: str = secrets.token_urlsafe(_TOKEN_BYTES)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(token)
    return token


def _secured(send: Send, api: bool) -> Send:
    """``send`` with the security headers added to the response that is started through it."""

    async def secured_send(message: Message) -> None:
        if message["type"] == "http.response.start":
            message.setdefault("headers", [])
            headers: MutableHeaders = MutableHeaders(scope=message)
            for name, value in SECURITY_HEADERS:
                headers[name] = value
            headers["Cache-Control"] = "no-store" if api else "no-cache"
        await send(message)

    return secured_send


class LocalGuard:
    """ASGI middleware that applies :func:`check_request` to every request and adds the security headers."""

    def __init__(self, app: ASGIApp, token: str, port: int, reach: frozenset[str]) -> None:
        self._app: ASGIApp = app
        self._token: str = token
        self._port: int = port
        self._reach: frozenset[str] = reach

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        headers: Headers = Headers(scope=scope)
        path: str = scope["path"]
        secured: Send = _secured(send, is_api_path(path))
        refusal: Refusal | None = check_request(
            path,
            headers.get("host"),
            headers.get("origin"),
            headers.get("authorization"),
            self._token,
            self._port,
            self._reach,
        )
        if refusal is None:
            await self._app(scope, receive, secured)
            return
        response: JSONResponse = JSONResponse(
            {"message": refusal.message, "context": {}},
            status_code=refusal.status,
            headers={"WWW-Authenticate": "Bearer"} if refusal.status == _HTTP_UNAUTHORIZED else None,
        )
        await response(scope, receive, secured)
