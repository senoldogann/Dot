"""The web connector: requests whose connections only reach checked addresses, with redirects followed by hand."""

import asyncio
import http.cookiejar
import socket
from collections.abc import Iterable, Mapping, Sequence
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
from dotagent.web.types import FetchedDocument

log = get_logger(__name__)

_MAX_REDIRECTS: int = 6
_SHOWN_CHARS: int = 200  # remote text in an error is clipped; the model reads errors outside any fence
_CAUSE_DEPTH: int = 6
_CONNECT_TIMEOUT_S: float = 10.0
_RETRY: RetryPolicy = RetryPolicy(attempts=2, base_delay_s=0.4, max_delay_s=2.0)
# A search that met the bot check of its engine tries again right away; the pause is the gate's (see search.py).
_SEARCH_RETRY_DELAY_S: float = 0.2
_REDIRECT_STATUS: frozenset[int] = frozenset({301, 302, 303, 307, 308})
_ACCEPT: str = "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.8,text/plain;q=0.7,*/*;q=0.5"
_ACCEPT_LANGUAGE: str = "en-US,en;q=0.9,tr;q=0.8"
# A connection that could not be opened has sent nothing, so another try is safe even for a POST.
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
    environment, no cookies kept, and the browser's host allow and block lists apply. ``search_gate`` and
    ``search_retry`` pace the searches that go through a scraped result page."""

    def __init__(self, config: WebConfig, allowed_hosts: Sequence[str], blocked_hosts: Sequence[str]) -> None:
        self._config: WebConfig = config
        self._allowed_hosts: tuple[str, ...] = tuple(allowed_hosts)
        self._blocked_hosts: tuple[str, ...] = tuple(blocked_hosts)
        self.search_gate: RequestGate = RequestGate(config.search_spacing_s)
        self.search_retry: RetryPolicy = RetryPolicy(
            attempts=config.search_attempts, base_delay_s=_SEARCH_RETRY_DELAY_S, max_delay_s=_SEARCH_RETRY_DELAY_S
        )
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
        """POST a JSON body to an API; redirects are not followed."""
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

        return await retry_async(attempt, _is_transient, _no_hint, _RETRY, f"web {method}")

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
                headers={
                    "User-Agent": self._config.user_agent,
                    "Accept": _ACCEPT,
                    "Accept-Language": _ACCEPT_LANGUAGE,
                    **headers,
                },
                content=content,
            )
            response: httpx.Response = await self._client.send(request, stream=True)
            try:
                location: str | None = (
                    response.headers.get("location") if response.status_code in _REDIRECT_STATUS else None
                )
                media_type, charset = _split_content_type(response.headers.get("content-type", ""))
                body, truncated = (b"", False) if location is not None else await _read_capped(response, max_bytes)
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


async def _read_capped(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    """The (decoded) body up to ``max_bytes``, and whether more would have followed."""
    chunks: list[bytes] = []
    size: int = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size > max_bytes:
            return b"".join(chunks)[:max_bytes], True
    return b"".join(chunks), False
