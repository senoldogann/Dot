"""The web connector: requests whose connections only reach checked addresses, with redirects followed by hand."""

import asyncio
import http.cookiejar
import socket
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from dotagent.browser.safety import check_url
from dotagent.config import WebConfig
from dotagent.core.errors import WebError, WebTransportError
from dotagent.core.logs import get_logger
from dotagent.core.retry import RetryPolicy, retry_async
from dotagent.web.addresses import is_public_address, order_addresses
from dotagent.web.gate import RequestGate
from dotagent.web.ladder import AnswerMemory, EngineBench
from dotagent.web.types import FetchedDocument

log = get_logger(__name__)

_MAX_REDIRECTS: int = 6
_SHOWN_CHARS: int = 200  # remote text in an error is clipped; the model reads errors outside any fence
_CAUSE_DEPTH: int = 6
_CONNECT_TIMEOUT_S: float = 10.0
_RETRY: RetryPolicy = RetryPolicy(attempts=2, base_delay_s=0.4, max_delay_s=2.0)
# A search that met the bot check of its engine tries again right away; the pause is the gate's (see search.py).
_REDIRECT_STATUS: frozenset[int] = frozenset({301, 302, 303, 307, 308})
_ACCEPT: str = "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.8,text/plain;q=0.7,*/*;q=0.5"
_ACCEPT_LANGUAGE: str = "en-US,en;q=0.9,tr;q=0.8"
# The content codings Dot asks for, and reads (:func:`read_capped`): httpx asks for these two when it has no library
# for another, and the request says so itself, so that a library added later does not change what servers send.
ACCEPT_ENCODING: str = "gzip, deflate"
_CODINGS: frozenset[str] = frozenset({"gzip", "x-gzip", "deflate"})
# What a browser sends with the address bar's navigation (item 98); a wall that looks for them lets the request by.
_NAVIGATION_HEADERS: dict[str, str] = {
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def request_headers(method: str, user_agent: str, extra: Mapping[str, str]) -> dict[str, str]:
    """The headers of one request: the user agent, what is accepted, and on a GET the headers a browser sends when its
    address bar navigates. ``extra`` (an API's headers) comes last and wins."""
    navigation: dict[str, str] = _NAVIGATION_HEADERS if method == "GET" else {}
    return {
        "User-Agent": user_agent,
        "Accept": _ACCEPT,
        "Accept-Language": _ACCEPT_LANGUAGE,
        "Accept-Encoding": ACCEPT_ENCODING,
        **navigation,
        **extra,
    }


# Failures in transit, which another try can help with.
_TRANSIENT_ERRORS: tuple[type[httpx.HTTPError], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
)
# The failures before any byte of the request was sent: a connection that could not be opened or taken from the pool.
_UNSENT_ERRORS: tuple[type[httpx.HTTPError], ...] = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


@dataclass(frozen=True)
class _Exchange:
    """One request/response pair; ``location`` is set when the answer is a redirect."""

    status: int
    media_type: str
    charset: str | None
    location: str | None
    body: bytes
    truncated: bool


def _shown(text: str) -> str:
    return text if len(text) <= _SHOWN_CHARS else f"{text[: _SHOWN_CHARS - 1]}…"


def _cause_chain(exc: BaseException) -> str:
    """The error and the errors behind it, outermost first, without repeating what the wrapping errors repeat. Some
    connection errors carry no text of their own, so the type names are what says what happened."""
    parts: list[str] = []
    previous_name: str = ""
    previous_detail: str = ""
    current: BaseException | None = exc
    for _ in range(_CAUSE_DEPTH):
        if current is None:
            break
        name, detail = type(current).__name__, str(current)
        if (name, detail) != (previous_name, previous_detail):
            parts.append(name if detail in ("", previous_detail) else f"{name}: {detail}")
        previous_name, previous_detail = name, detail
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)


def _is_transient(exc: Exception) -> bool:
    return isinstance(exc, WebTransportError)


def _was_unsent(exc: Exception) -> bool:
    """Whether ``exc`` failed a request before it was sent. Only then does a POST try again: a POST the server got may
    have been done (a search the API bills) even when its answer was lost on the way back."""
    return isinstance(exc, WebTransportError) and isinstance(exc.__cause__, _UNSENT_ERRORS)


def _no_hint(exc: Exception) -> float | None:
    return None


def _split_content_type(value: str) -> tuple[str, str | None]:
    """Media type and charset of a ``Content-Type`` header."""
    media_type, _, parameters = value.partition(";")
    charset: str | None = None
    for parameter in parameters.split(";"):
        name, _, setting = parameter.partition("=")
        if name.strip().lower() == "charset" and setting.strip():
            charset = setting.strip().strip("\"'")
    return media_type.strip().lower(), charset


def _deny_all_cookies() -> httpx.Cookies:
    """A cookie jar that stores nothing: every fetch starts without state, whatever earlier sites asked for."""
    return httpx.Cookies(http.cookiejar.CookieJar(policy=http.cookiejar.DefaultCookiePolicy(allowed_domains=[])))


class _CheckedBackend(httpcore.AsyncNetworkBackend):
    """Opens TCP connections to addresses that were resolved and checked a moment before, so a host name cannot be
    pointed at the local network between the check and the connection. Verification of the certificate still uses
    the host name of the request."""

    def __init__(self, allow_private_hosts: bool) -> None:
        self._inner: httpcore.AsyncNetworkBackend = httpcore.AnyIOBackend()
        self._allow_private_hosts: bool = allow_private_hosts

    async def _checked_addresses(self, host: str, port: int) -> tuple[str, ...]:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise WebError("the host name could not be resolved", {"host": _shown(host), "error": str(exc)}) from exc
        except UnicodeError as exc:
            # The IDNA codec refuses a label over 63 characters or an empty one (``a..b``) before any lookup (item 116).
            raise WebError(
                "the host name is not a valid domain name", {"host": _shown(host), "error": str(exc)}
            ) from exc
        found: tuple[str, ...] = order_addresses([str(info[4][0]) for info in infos])
        private: tuple[str, ...] = tuple(address for address in found if not is_public_address(address))
        if private and not self._allow_private_hosts:
            raise WebError(
                "the host is on a private or local network address; the web tools only reach the public internet "
                "(web.allow_private_hosts)",
                {"host": _shown(host), "addresses": list(private)},
            )
        return found

    # The signature is the one of the httpcore interface this class implements, defaults included.
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses: tuple[str, ...] = await self._checked_addresses(host, port)
        failure: httpcore.ConnectError | httpcore.ConnectTimeout | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(address, port, timeout, local_address, socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                failure = exc
        if failure is None:
            raise WebError("the host name resolved to no address", {"host": _shown(host)})
        raise failure

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None
    ) -> httpcore.AsyncNetworkStream:
        raise WebError("unix sockets are not reachable through the web tools", {"path": path})

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _CheckedTransport(httpx.AsyncHTTPTransport):
    """The standard httpx transport with its connection pool opened through the address check."""

    def __init__(self, backend: httpcore.AsyncNetworkBackend, limits: httpx.Limits) -> None:
        super().__init__(http2=False, limits=limits, trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=True,
            http2=False,
            network_backend=backend,
        )


class WebClient:
    """Fetches documents from the web: public addresses only (unless ``web.allow_private_hosts``), no proxies from the
    environment, no cookies kept, and the browser's host allow and block lists apply. ``search_gate`` paces the
    searches that go through a scraped result page; ``search_bench`` holds the engines that limited this machine and
    ``search_memory`` the answers of recent queries."""

    def __init__(self, config: WebConfig, allowed_hosts: Sequence[str], blocked_hosts: Sequence[str]) -> None:
        self._config: WebConfig = config
        self._allowed_hosts: tuple[str, ...] = tuple(allowed_hosts)
        self._blocked_hosts: tuple[str, ...] = tuple(blocked_hosts)
        self.search_gate: RequestGate = RequestGate(config.search_spacing_s)
        self.search_bench: EngineBench = EngineBench()
        self.search_memory: AnswerMemory = AnswerMemory()
        limits: httpx.Limits = httpx.Limits(max_connections=16, max_keepalive_connections=8, keepalive_expiry=30.0)
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            transport=_CheckedTransport(_CheckedBackend(config.allow_private_hosts), limits),
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT_S,
                read=config.fetch_timeout_s,
                write=_CONNECT_TIMEOUT_S,
                pool=_CONNECT_TIMEOUT_S,
            ),
            follow_redirects=False,
            cookies=_deny_all_cookies(),
        )

    @property
    def user_agent(self) -> str:
        """The User-Agent Dot sends, for a request of the search's own."""
        return self._config.user_agent

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, url: str, max_bytes: int) -> FetchedDocument:
        """GET ``url`` and follow its redirects; every hop is checked like the first request."""
        visited: list[str] = []
        current: str = url
        for _ in range(_MAX_REDIRECTS + 1):
            exchange: _Exchange = await self._exchange("GET", current, {}, None, max_bytes)
            if exchange.location is None:
                return _document(current, exchange, tuple(visited))
            visited.append(current)
            current = urljoin(current, exchange.location)
        raise WebError("too many redirects", {"url": _shown(url), "redirects": len(visited), "limit": _MAX_REDIRECTS})

    async def post_json(self, url: str, headers: Mapping[str, str], body: bytes, max_bytes: int) -> FetchedDocument:
        """POST a JSON body to an API; redirects are not followed, and the request is tried again only when it was not
        sent (a connection that could not be opened)."""
        exchange: _Exchange = await self._exchange(
            "POST", url, {**headers, "Content-Type": "application/json"}, body, max_bytes
        )
        if exchange.location is not None:
            raise WebError("the API answered with a redirect", {"url": _shown(url), "status": exchange.status})
        return _document(url, exchange, ())

    async def _exchange(
        self, method: str, url: str, headers: Mapping[str, str], content: bytes | None, max_bytes: int
    ) -> _Exchange:
        async def attempt() -> _Exchange:
            return await self._exchange_once(method, url, headers, content, max_bytes)

        retryable: Callable[[Exception], bool] = _is_transient if method == "GET" else _was_unsent
        return await retry_async(attempt, retryable, _no_hint, _RETRY, f"web {method}")

    def _require_allowed(self, url: str) -> None:
        verdict = check_url(url, self._allowed_hosts, self._blocked_hosts)
        if not verdict.allowed:
            raise WebError(
                "the web tools will not open this address", {"url": _shown(url), "reason": _shown(verdict.reason)}
            )
        try:
            credentials: bool = urlsplit(url).username is not None
        except ValueError as exc:
            raise WebError("the address is malformed", {"url": _shown(url), "error": _shown(str(exc))}) from exc
        if credentials:
            raise WebError("addresses with a user name or password are not supported", {"url": _shown(url)})

    async def _exchange_once(
        self, method: str, url: str, headers: Mapping[str, str], content: bytes | None, max_bytes: int
    ) -> _Exchange:
        self._require_allowed(url)
        try:
            request: httpx.Request = self._client.build_request(
                method,
                url,
                headers=request_headers(method, self._config.user_agent, headers),
                content=content,
            )
            response: httpx.Response = await self._client.send(request, stream=True)
            try:
                location: str | None = (
                    response.headers.get("location") if response.status_code in _REDIRECT_STATUS else None
                )
                media_type, charset = _split_content_type(response.headers.get("content-type", ""))
                body, truncated = (b"", False) if location is not None else await read_capped(response, max_bytes)
            finally:
                await response.aclose()
        except _TRANSIENT_ERRORS as exc:
            raise WebTransportError(
                "the request failed in transit",
                {"url": _shown(url), "error_type": type(exc).__name__, "error": _shown(_cause_chain(exc))},
            ) from exc
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise WebError(
                "the request failed",
                {"url": _shown(url), "error_type": type(exc).__name__, "error": _shown(_cause_chain(exc))},
            ) from exc
        log.debug("web_exchange", method=method, status=response.status_code, bytes=len(body))
        return _Exchange(
            status=response.status_code,
            media_type=media_type,
            charset=charset,
            location=location,
            body=body,
            truncated=truncated,
        )


def _document(url: str, exchange: _Exchange, redirected_from: tuple[str, ...]) -> FetchedDocument:
    return FetchedDocument(
        url=url,
        status=exchange.status,
        media_type=exchange.media_type,
        charset=exchange.charset,
        body=exchange.body,
        truncated=exchange.truncated,
        redirected_from=redirected_from,
    )


def _content_codings(header: str) -> list[str]:
    """The codings a ``Content-Encoding`` header lists, in the order they were applied, ``identity`` left out."""
    return [token for token in (part.strip().lower() for part in header.split(",")) if token and token != "identity"]


class _Inflater:
    """One layer of gzip or deflate, undone piece by piece with a limit on what a piece may inflate to: zlib stops when
    the room is used, and a few hundred bytes of nested compression do not become gigabytes in one call (item 554)."""

    def __init__(self, coding: str) -> None:
        self._first: bool = True
        self._deflate: bool = coding == "deflate"
        window: int = zlib.MAX_WBITS if self._deflate else zlib.MAX_WBITS | 16
        self._inflater: zlib._Decompress = zlib.decompressobj(window)

    def decode(self, data: bytes, room: int) -> bytes:
        """At most ``room`` bytes (at least 1) of what ``data`` inflates to. The rest of a piece that would inflate to
        more is not decoded: the caller stops reading then."""
        first: bool = self._first
        self._first = False
        try:
            return self._inflater.decompress(data, room)
        except zlib.error:
            # A deflate body that comes without the zlib header, as some servers send it: read raw (httpx does too).
            if not (first and self._deflate):
                raise
            self._inflater = zlib.decompressobj(-zlib.MAX_WBITS)
            return self._inflater.decompress(data, room)


async def read_capped(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    """The body up to ``max_bytes``, its content coding undone, and whether more would have followed. It is read as it
    comes and decoded here, not by httpx, which inflates each piece of the body whole: a body that is compressed twice
    (``Content-Encoding: gzip, gzip``) and a few hundred bytes long inflated to gigabytes in one call, whatever the cap
    (item 554). Dot asks for gzip and deflate, once; an answer in more codings, or in another, is refused."""
    codings: list[str] = _content_codings(response.headers.get("content-encoding", ""))
    if len(codings) > 1 or any(coding not in _CODINGS for coding in codings):
        raise WebError(
            "the answer is compressed in a way the web tools do not read (gzip or deflate, once)",
            {"content_encoding": _shown(response.headers.get("content-encoding", ""))},
        )
    inflater: _Inflater | None = _Inflater(codings[0]) if codings else None
    chunks: list[bytes] = []
    size: int = 0
    async for raw in response.aiter_raw():
        try:
            piece: bytes = raw if inflater is None else inflater.decode(raw, max_bytes + 1 - size)
        except zlib.error as exc:
            raise WebError("the answer could not be decompressed", {"error": _shown(str(exc))}) from exc
        chunks.append(piece)
        size += len(piece)
        if size > max_bytes:
            return b"".join(chunks)[:max_bytes], True
    return b"".join(chunks), False
