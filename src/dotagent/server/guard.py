"""Who may reach the server: this Mac only, pages of the server itself only, and only with the bearer token.

Binding to a loopback address keeps other machines out. The Host check defeats DNS rebinding, the Origin check keeps
the pages of other sites from using the API through the browser, the Fetch Metadata check keeps them from sending a
browser to the UI (:func:`navigation_is_foreign`), and the token keeps other programs of this Mac out.
The clients send the token only to a server that first proves it holds it (:func:`daemon_proof`), so a program that took
the port while the daemon was down is sent nothing. A phone's app trades a pairing code for the token without it
(``/api/phone/pair``, item 267; :mod:`dotagent.server.pairing`), and since item 670 the phone gets a key of its own,
which the guard takes beside the token while the phone is listed (:mod:`dotagent.server.phones`); a phone's key cannot
make or remove a key (:func:`needs_main_token`). A page that a run showed (``/pages/<id>``) is served
without the token, to whoever names its id, which cannot be guessed; it runs the run's scripts, so it is sandboxed
wherever it opens (:data:`PAGE_HEADERS`).
"""

import asyncio
import hashlib
import hmac
import ipaddress
import os
import secrets
import time
from pathlib import Path

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dotagent.agent.pages import LIBRARY_HOSTS, PAGES_PREFIX
from dotagent.core.errors import ConfigError, PhoneRefused
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.mcp.signin import CALLBACK_PATH
from dotagent.server.callback_page import page_style
from dotagent.server.phones import PairedPhones

API_ROOT: str = "/api"
API_PREFIX: str = f"{API_ROOT}/"
PROOF_PATH: str = f"{API_ROOT}/proof"
PAIR_PATH: str = f"{API_ROOT}/phone/pair"
PUBLIC_PATHS: frozenset[str] = frozenset({f"{API_ROOT}/health", PROOF_PATH, PAIR_PATH})
# What answers the installation's token alone, never a phone's key (item 670): what opens or closes a way into Dot.
# Making a pairing code, and the list of the phones that hold a key, with their removal; switching the iMessage channel
# and pairing or removing a number of it (item 572). So a phone that is taken cannot open another way in to stay in
# after it is removed, nor close the owner's.
PAIRING_PATH: str = f"{API_ROOT}/phone/pairing"
PHONES_PATH: str = f"{API_ROOT}/phone/phones"
_WAYS_IN: frozenset[str] = frozenset(
    {
        PAIRING_PATH,
        PHONES_PATH,
        f"{API_ROOT}/channels/imessage/switch",
        f"{API_ROOT}/channels/imessage/code",
        f"{API_ROOT}/channels/imessage/confirm",
        f"{API_ROOT}/channels/imessage/remove",
    }
)
LOOKS_PREFIX: str = "/looks/"
# Where the guard leaves, in the state of a request a phone's key let in, the id of that phone (item 678): what a route
# reads to know the request came from a phone, whatever its Host header says, which any client may write.
PHONE_STATE: str = "dot_phone"

_PRIVATE_FILE_MODE: int = 0o600
_PERMISSION_BITS: int = 0o777
_OTHERS_BITS: int = 0o077
_TOKEN_BYTES: int = 32
_LOOPBACK_NAME: str = "localhost"
# The hosts ``[server] host`` may name, spelt as the Host check knows them (:func:`local_hosts`).
_LOCAL_HOSTS: tuple[str, ...] = ("127.0.0.1", _LOOPBACK_NAME, "::1")
_HTTP_BAD_REQUEST: int = 400
_HTTP_MISDIRECTED: int = 421
_HTTP_FORBIDDEN: int = 403
_HTTP_UNAUTHORIZED: int = 401
# Fetch Metadata (``Sec-Fetch-Mode``, ``Sec-Fetch-Site``) of a navigation that a page of another site started.
_NAVIGATE_MODE: str = "navigate"
_CROSS_SITE: str = "cross-site"
# What the proof of the daemon puts before the nonce, so that it is good for nothing else keyed with the token.
_PROOF_LABEL: bytes = b"dot daemon proof\n"

log = get_logger(__name__)

# The files a launcher asks for outside any page of the UI, to draw the app it adds to a home screen or a dock (item
# 410): a phone's own process fetches them, and reads them only when their headers let another origin do so.
LAUNCHER_PATHS: tuple[str, ...] = ("/apple-touch-icon", "/icons/", "/favicon.svg", "/manifest.webmanifest")
# The pages of the UI load only their own scripts and styles, frame only pages of the server itself (the pages runs
# showed), and nothing can frame them.
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    (
        "Content-Security-Policy",
        "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' blob: data:; "
        "manifest-src 'self'; frame-src 'self'; "
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


def callback_headers() -> tuple[tuple[str, str], ...]:
    """The headers of the page a sign-in comes back to (:mod:`dotagent.server.callback_page`): those of the UI, but for
    a policy that lets its one style apply by its hash, its pictures as ``data:`` addresses and its faces from this
    server, and nothing else."""
    policy: str = (
        f"default-src 'none'; style-src {page_style().source}; font-src 'self'; img-src data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    return (
        ("Content-Security-Policy", policy),
        *(header for header in SECURITY_HEADERS if header[0] != "Content-Security-Policy"),
    )


class Refusal(FrozenModel):
    status: int
    message: str


def require_loopback(host: str) -> None:
    """Refuse a bind address that other machines can reach, and a loopback one that the Host check does not know
    (:data:`_LOCAL_HOSTS`): the clients write the host as it stands into the address they send their requests to, so
    another spelling (127.0.0.2, ::1 written out in full) would start a daemon that refuses every request (item 561)."""
    if host in _LOCAL_HOSTS:
        return
    try:
        address: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ConfigError(
            f"the server host must be one of {', '.join(_LOCAL_HOSTS)}", {"host": host, "setting": "[server] host"}
        ) from exc
    reason: str = (
        "the server answers to no other spelling of a loopback address"
        if address.is_loopback
        else "Dot controls this Mac and is not served to the network"
    )
    raise ConfigError(
        f"the server host must be one of {', '.join(_LOCAL_HOSTS)}; {reason}",
        {"host": host, "setting": "[server] host"},
    )


def _host_header(host: str, port: int) -> str:
    """``host`` and ``port`` as a ``Host`` header carries them: an IPv6 address in brackets."""
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def local_hosts(port: int) -> frozenset[str]:
    """The ``Host`` header values with which a browser on this Mac reaches the server."""
    return frozenset(_host_header(host, port) for host in _LOCAL_HOSTS)


def host_is_local(host_header: str | None, port: int, reach: frozenset[str]) -> bool:
    """Whether the request names this server: its loopback address, or one of the names a reverse proxy on the
    owner's own network presents it as (``[server] reach``), with or without HTTPS's own port, which some clients
    write out."""
    if host_header is None:
        return False
    name: str = host_header.lower()
    names: set[str] = {host.lower() for host in reach}
    return name in local_hosts(port) or name in names or (name.endswith(":443") and name[:-4] in names)


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


def is_launcher_path(path: str) -> bool:
    return path.startswith(LAUNCHER_PATHS)


def needs_token(path: str) -> bool:
    return is_api_path(path) and path not in PUBLIC_PATHS


def is_page_path(path: str) -> bool:
    return path.startswith(PAGES_PREFIX)


def is_look_path(path: str) -> bool:
    return path.startswith(LOOKS_PREFIX)


def response_headers(path: str) -> tuple[tuple[str, str], ...]:
    """The security headers of the response to ``path``, with how long it may be cached: the API's answers, the pages
    runs showed and the page a sign-in comes back to never, the files of the UI once checked again."""
    if is_page_path(path):
        return (*PAGE_HEADERS, ("Cache-Control", "no-store"))
    if is_look_path(path):
        return (*LOOK_HEADERS, ("Cache-Control", "no-store"))
    if path == CALLBACK_PATH:
        return (*callback_headers(), ("Cache-Control", "no-store"))
    if is_launcher_path(path):
        # Public files that hold nothing of the owner's: a launcher's process may read them from any origin.
        return (
            *(header for header in SECURITY_HEADERS if header[0] != "Cross-Origin-Resource-Policy"),
            ("Cross-Origin-Resource-Policy", "cross-origin"),
            ("Cache-Control", "no-cache"),
        )
    return (*SECURITY_HEADERS, ("Cache-Control", "no-store" if is_api_path(path) else "no-cache"))


def has_dot_segment(path: str) -> bool:
    """Whether the decoded ``path`` has a ``.`` or ``..`` segment. No page of the server has one, and the files behind
    a prefix the guard knows (the launcher's, a run's page, a preview) would resolve it to a file of another prefix
    (``/icons/..%2Findex.html``)."""
    return any(segment in (".", "..") for segment in path.split("/"))


def needs_main_token(path: str) -> bool:
    """Whether ``path`` answers the installation's token alone (item 670): what opens or closes a way into Dot, a phone
    or a number of the iMessage channel."""
    return path in _WAYS_IN or path.startswith(f"{PHONES_PATH}/")


def from_a_phone(scope: Scope) -> bool:
    """Whether the guard let the request of ``scope`` in by a phone's key (:data:`PHONE_STATE`), not the installation's
    token."""
    state: object = scope.get("state")
    return isinstance(state, dict) and state.get(PHONE_STATE) is not None


def refuse_from_a_phone(scope: Scope, what: str) -> None:
    """Refuse the request of ``scope`` when a phone's key made it (item 678): what ``what`` names is done on the Mac,
    with the installation's token, so that a phone that is taken cannot leave Dot doing more without asking after the
    phone is removed."""
    if from_a_phone(scope):
        raise PhoneRefused(f"a phone cannot {what}: do it on your Mac", {"phone": True})


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
    phone: str | None = None,
) -> Refusal | None:
    """Why a request must not be served, or ``None`` when it may be. ``phone`` is the id of the listed phone whose key
    the request carries (:meth:`PairedPhones.authenticate`), ``None`` when it carries none."""
    if has_dot_segment(path):
        return Refusal(status=_HTTP_BAD_REQUEST, message="a path with a . or .. segment is refused")
    if not host_is_local(host, port, reach):
        return Refusal(status=_HTTP_MISDIRECTED, message="this server answers only to its loopback address")
    if not origin_is_local(origin, port, reach):
        return Refusal(status=_HTTP_FORBIDDEN, message="requests from other origins are refused")
    if navigation_is_foreign(fetch_site, fetch_mode) and not reached_from_other_sites(path):
        return Refusal(status=_HTTP_FORBIDDEN, message="navigations from pages of other sites are refused")
    if needs_token(path) and not token_matches(token, bearer_token(authorization)):
        if phone is None:
            return Refusal(status=_HTTP_UNAUTHORIZED, message="a valid bearer token is required")
        if needs_main_token(path):
            return Refusal(
                status=_HTTP_FORBIDDEN,
                message="a phone cannot open or close a way into Dot (a phone, a Messages number): do it on your Mac",
            )
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


def _ends_when_removed(receive: Receive, removed: asyncio.Event) -> Receive:
    """``receive`` that tells the app the client went away once ``removed`` is set: a stream a phone's key opened ends
    as soon as the phone is removed (item 670), the way it ends when the phone goes."""

    async def guarded_receive() -> Message:
        if removed.is_set():
            return {"type": "http.disconnect"}
        message: asyncio.Task[Message] = asyncio.ensure_future(receive())
        gone: asyncio.Task[bool] = asyncio.ensure_future(removed.wait())
        try:
            await asyncio.wait({message, gone}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            gone.cancel()
        if message.done():
            return message.result()
        message.cancel()
        return {"type": "http.disconnect"}

    return guarded_receive


class LocalGuard:
    """ASGI middleware that applies :func:`check_request` to every request and adds the security headers. ``phones``
    are the phones that hold a key of their own (item 670); none when it is ``None``."""

    def __init__(
        self, app: ASGIApp, token: str, port: int, reach: frozenset[str], phones: PairedPhones | None = None
    ) -> None:
        self._app: ASGIApp = app
        self._token: str = token
        self._port: int = port
        self._reach: frozenset[str] = reach
        self._phones: PairedPhones | None = phones

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
        authorization: str | None = headers.get("authorization")
        bearer: str | None = bearer_token(authorization)
        phone: str | None = None
        if self._phones is not None and needs_token(path) and not token_matches(self._token, bearer):
            phone = self._phones.authenticate(bearer)
        refusal: Refusal | None = check_request(
            path,
            headers.get("host"),
            headers.get("origin"),
            headers.get("sec-fetch-site"),
            headers.get("sec-fetch-mode"),
            authorization,
            self._token,
            self._port,
            self._reach,
            phone,
        )
        if refusal is None:
            if phone is not None and self._phones is not None:
                scope.setdefault("state", {})[PHONE_STATE] = phone
                await self._phones.used(phone, time.time())
                receive = _ends_when_removed(receive, self._phones.removed(phone))
            await self._app(scope, receive, secured)
            return
        # Why a request was turned away, for the owner who wonders why a phone's launcher drew no icon (item 410); the
        # token is never in it.
        log.info(
            "request_refused",
            path=path,
            status=refusal.status,
            reason=refusal.message,
            host=headers.get("host"),
            origin=headers.get("origin"),
        )
        response: JSONResponse = JSONResponse(
            {"message": refusal.message, "context": {}},
            status_code=refusal.status,
            headers={"WWW-Authenticate": "Bearer"} if refusal.status == _HTTP_UNAUTHORIZED else None,
        )
        await response(scope, receive, secured)
