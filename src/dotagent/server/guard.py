"""Who may reach the server: this Mac only, pages of the server itself only, and only with the bearer token.

Binding to a loopback address keeps other machines out. The Host check defeats DNS rebinding, the Origin check keeps
the pages of other sites from using the API through the browser, the Fetch Metadata check keeps them from sending a
browser to the UI (:func:`navigation_is_foreign`), and the token keeps other programs of this Mac out.
The clients send the token only to a server that first proves it holds it (:func:`daemon_proof`), so a program that took
the port while the daemon was down is sent nothing. A phone's app trades a pairing code for the token without it
(``/api/phone/pair``, item 267; :mod:`dotagent.server.pairing`). A page that a run showed (``/pages/<id>``) is served without the
token, to whoever names its id, which cannot be guessed; it runs the run's scripts, so it is sandboxed wherever it
opens (:data:`PAGE_HEADERS`).
"""

import hashlib
import hmac
import ipaddress
import os
import secrets
from pathlib import Path

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dotagent.agent.pages import LIBRARY_HOSTS
from dotagent.core.errors import ConfigError
from dotagent.core.types import FrozenModel
from dotagent.mcp.signin import CALLBACK_PATH

API_ROOT: str = "/api"
API_PREFIX: str = f"{API_ROOT}/"
PROOF_PATH: str = f"{API_ROOT}/proof"
PAIR_PATH: str = f"{API_ROOT}/phone/pair"
PUBLIC_PATHS: frozenset[str] = frozenset({f"{API_ROOT}/health", PROOF_PATH, PAIR_PATH})
PAGES_PREFIX: str = "/pages/"
LOOKS_PREFIX: str = "/looks/"
# What a browser sends as the Origin of a sandboxed page, whose origin is opaque.
_OPAQUE_ORIGIN: str = "null"

_PRIVATE_FILE_MODE: int = 0o600
_PERMISSION_BITS: int = 0o777
_OTHERS_BITS: int = 0o077
_TOKEN_BYTES: int = 32
_LOOPBACK_NAME: str = "localhost"
_HTTP_MISDIRECTED: int = 421
_HTTP_FORBIDDEN: int = 403
_HTTP_UNAUTHORIZED: int = 401
# Fetch Metadata (``Sec-Fetch-Mode``, ``Sec-Fetch-Site``) of a navigation that a page of another site started.
_NAVIGATE_MODE: str = "navigate"
_CROSS_SITE: str = "cross-site"
# What the proof of the daemon puts before the nonce, so that it is good for nothing else keyed with the token.
_PROOF_LABEL: bytes = b"dot daemon proof\n"

# The pages of the UI load only their own scripts and styles, frame only pages of the server itself (the pages runs
# showed), and nothing can frame them.
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    (
        "Content-Security-Policy",
        "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' blob: data:; manifest-src 'self'; "
        "frame-src 'self'; "
        # The desktop shell's bridge (Tauri IPC) is a connection the page makes; a browser never sees these sources.
        "connect-src 'self' ipc: http://ipc.localhost; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    ),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)
_LIBRARIES: str = " ".join(f"https://{host}" for host in LIBRARY_HOSTS)
# A page that a run showed runs the run's scripts. Sandboxed, framed or opened full size, its origin is opaque, so it
# reads no storage of the UI and reaches neither the API nor the pages that frame it; it loads libraries from the
# CDNs of LIBRARY_HOSTS and nothing else, submits no form and asks for no camera, microphone or position. Only the
# pages of the server itself may frame it.
PAGE_HEADERS: tuple[tuple[str, str], ...] = (
    (
        "Content-Security-Policy",
        "sandbox allow-scripts; default-src 'none'; "
        f"script-src 'unsafe-inline' 'unsafe-eval' {_LIBRARIES}; style-src 'unsafe-inline' {_LIBRARIES}; "
        f"font-src data: {_LIBRARIES}; connect-src {_LIBRARIES}; worker-src blob: {_LIBRARIES}; "
        "img-src data: blob:; media-src data: blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'",
    ),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("X-DNS-Prefetch-Control", "off"),
)
# The drawing Quick Look made of an Office file (item 268) runs Quick Look's scripts (a workbook's tabs). Sandboxed, its
# origin is opaque wherever it opens: it reads no storage of the UI, reaches neither the API nor the pages that frame
# it, and loads nothing but its own parts. Its parts are loaded by that opaque origin, so they are not kept to this
# server's own (no ``Cross-Origin-Resource-Policy``).
LOOK_HEADERS: tuple[tuple[str, str], ...] = (
    (
        "Content-Security-Policy",
        "sandbox allow-scripts; default-src 'none'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; frame-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'self'",
    ),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-DNS-Prefetch-Control", "off"),
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


def navigation_is_foreign(fetch_site: str | None, fetch_mode: str | None) -> bool:
    """Whether a request is a navigation that a page of another site started: a link or a script of that page, or a
    redirect it sent the browser through (Chrome reads the whole redirect chain into ``Sec-Fetch-Site``). The address
    bar, a bookmark, the desktop shell and a program send ``none``; the UI's own links and the pages it frames
    ``same-origin``; a browser that sends no Fetch Metadata is not refused on it. A run's browser that is sent to the
    interface through such a page is stopped here even where its own rules were not asked (item 135)."""
    if fetch_site is None or fetch_mode is None:
        return False
    return fetch_mode.lower() == _NAVIGATE_MODE and fetch_site.lower() == _CROSS_SITE


def reached_from_other_sites(path: str) -> bool:
    """The paths that other sites send a browser to by design, which a foreign navigation reaches: the page a sign-in
    comes back to (an authorization server redirects the browser there, item 279), and the pages runs showed and the
    drawings of Office files, whose origin is opaque wherever they open (:data:`PAGE_HEADERS`, :data:`LOOK_HEADERS`),
    so a page of another site gains nothing by opening one; a drawing's own frames are such navigations. The UI and the
    API, where the token is, are reached from this server's own pages alone."""
    return path == CALLBACK_PATH or is_page_path(path) or is_look_path(path)


def bearer_token(authorization: str | None) -> str | None:
    """The token of an ``Authorization: Bearer`` header; ``None`` for any other header."""
    if authorization is None:
        return None
    scheme, _, value = authorization.partition(" ")
    cleaned: str = value.strip()
    return cleaned if scheme.lower() == "bearer" and cleaned != "" else None


def token_matches(expected: str, supplied: str | None) -> bool:
    return supplied is not None and hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))


def daemon_proof(token: str, nonce: str) -> str:
    """How the daemon answers ``nonce`` (``GET /api/proof``) to show that it holds ``token`` without sending it:
    HMAC-SHA256 keyed with the token over a fixed label and the nonce, in hex. A client sends the token only to a
    server that answers a fresh nonce of its own this way; a program that took the port cannot."""
    return hmac.new(token.encode("utf-8"), _PROOF_LABEL + nonce.encode("ascii"), hashlib.sha256).hexdigest()


def is_api_path(path: str) -> bool:
    return path == API_ROOT or path.startswith(API_PREFIX)


def needs_token(path: str) -> bool:
    return is_api_path(path) and path not in PUBLIC_PATHS


def is_page_path(path: str) -> bool:
    return path.startswith(PAGES_PREFIX)


def is_look_path(path: str) -> bool:
    return path.startswith(LOOKS_PREFIX)


def response_headers(path: str) -> tuple[tuple[str, str], ...]:
    """The security headers of the response to ``path``, with how long it may be cached: the API's answers and the
    pages runs showed never, the files of the UI once checked again."""
    if is_page_path(path):
        return (*PAGE_HEADERS, ("Cache-Control", "no-store"))
    if is_look_path(path):
        return (*LOOK_HEADERS, ("Cache-Control", "no-store"))
    return (*SECURITY_HEADERS, ("Cache-Control", "no-store" if is_api_path(path) else "no-cache"))


def check_request(
    path: str,
    host: str | None,
    origin: str | None,
    fetch_site: str | None,
    fetch_mode: str | None,
    authorization: str | None,
    token: str,
    port: int,
    reach: frozenset[str],
) -> Refusal | None:
    """Why a request must not be served, or ``None`` when it may be."""
    if not host_is_local(host, port, reach):
        return Refusal(status=_HTTP_MISDIRECTED, message="this server answers only to its loopback address")
    # A drawing of an Office file asks for its own parts from its opaque origin, as "null" (a font, item 268).
    drawing_part: bool = is_look_path(path) and origin is not None and origin.lower() == _OPAQUE_ORIGIN
    if not origin_is_local(origin, port, reach) and not drawing_part:
        return Refusal(status=_HTTP_FORBIDDEN, message="requests from other origins are refused")
    if navigation_is_foreign(fetch_site, fetch_mode) and not reached_from_other_sites(path):
        return Refusal(status=_HTTP_FORBIDDEN, message="navigations from pages of other sites are refused")
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


def _secured(send: Send, added: tuple[tuple[str, str], ...]) -> Send:
    """``send`` with the headers ``added`` set on the response that is started through it."""

    async def secured_send(message: Message) -> None:
        if message["type"] == "http.response.start":
            message.setdefault("headers", [])
            headers: MutableHeaders = MutableHeaders(scope=message)
            for name, value in added:
                headers[name] = value
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
        secured: Send = _secured(send, response_headers(path))
        refusal: Refusal | None = check_request(
            path,
            headers.get("host"),
            headers.get("origin"),
            headers.get("sec-fetch-site"),
            headers.get("sec-fetch-mode"),
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
