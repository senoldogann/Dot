"""Whether this Mac can connect to a model's server now (item 769): its name resolves and its port takes a TCP
connection. A call that broke tells only that it broke; this tells a network that is down (or a local server that is
not running) from a server that failed or a model that is slow, so that a run says which it waits for, stops waiting
on a call whose connection is gone, and asks again as soon as the connection is back. Nothing is sent but the
connection's opening; behind a proxy (``HTTPS_PROXY``, ``HTTP_PROXY``, ``ALL_PROXY``), which a direct connection does
not go through, the answer is always yes."""

import asyncio
import os
import socket
from contextlib import suppress
from urllib.parse import urlsplit

from dotagent.core.errors import DotError, ProviderTransportError

# How long the connection may take to open: a network that is up opens one to a model's server in well under a second.
REACH_TIMEOUT_S: float = 5.0
# The key of an error's context that names the host a call could not connect to.
OFFLINE: str = "offline"
_PROXIES: tuple[str, ...] = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
# How long one address of the host is tried before the next is tried beside it ("happy eyeballs", RFC 8305), as the
# connectors' own connections do (httpcore through anyio): a network whose IPv6 route drops packets connects over IPv4,
# rather than spending the whole check on the first address (the pre-push review of item 769).
_NEXT_ADDRESS_S: float = 0.25
# The lookups of the hosts' names that are out, one for each host and port: a lookup goes on in a thread of the default
# executor until the system's resolver gives up, which a check that ran out of time does not stop, so a check made
# meanwhile waits for it rather than starting another (the pre-push review of item 769).
_LOOKING_UP: dict[tuple[str, int], asyncio.Future[list[object]]] = {}


def host_of(url: str) -> str | None:
    """The host of ``url`` and its port when it names one (``localhost:11434``), as the run's page names it."""
    parts = urlsplit(url)
    if parts.hostname is None:
        return None
    return parts.hostname if parts.port is None else f"{parts.hostname}:{parts.port}"


async def reaches(url: str, timeout_s: float = REACH_TIMEOUT_S) -> bool:
    """Whether a TCP connection to the host and port of ``url`` opens within ``timeout_s``: no for a name that does not
    resolve, a connection refused or one that does not open in time; yes for an address without a host and behind a
    proxy."""
    if any(os.environ.get(name) for name in _PROXIES):
        return True
    parts = urlsplit(url)
    if parts.hostname is None:
        return True
    port: int = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        async with asyncio.timeout(timeout_s):
            await asyncio.shield(_lookup(parts.hostname, port))
            # The name is in the system's cache now: the connection's own lookup is answered at once.
            _reader, writer = await asyncio.open_connection(
                parts.hostname, port, happy_eyeballs_delay=_NEXT_ADDRESS_S, interleave=1
            )
    except (OSError, TimeoutError):
        return False
    writer.close()
    with suppress(OSError):
        await writer.wait_closed()
    return True


def _lookup(host: str, port: int) -> asyncio.Future[list[object]]:
    """The lookup of ``host`` that is out for ``port``, or a new one: one at a time for each (:data:`_LOOKING_UP`)."""
    running: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    out: asyncio.Future[list[object]] | None = _LOOKING_UP.get((host, port))
    if out is None or out.done() or out.get_loop() is not running:
        out = asyncio.ensure_future(_resolve(host, port))
        _LOOKING_UP[(host, port)] = out
    return out


async def _resolve(host: str, port: int) -> list[object]:
    return list(await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM))


async def cannot_connect(error: ProviderTransportError) -> ProviderTransportError | None:
    """The error of a call whose connection broke (``error``) when its server cannot be connected to now, as it says
    and with the host in its context under :data:`OFFLINE`; ``None`` when it can be, or the error names no address."""
    url: object = error.context.get("url")
    if not isinstance(url, str) or await reaches(url):
        return None
    host: str = host_of(url) or url
    return ProviderTransportError(f"cannot connect to {host}: {error.message}", {**error.context, OFFLINE: host})


def out_of_reach(error: Exception) -> str | None:
    """The host a model's call could not connect to, when ``error`` says so (:func:`cannot_connect`), else ``None``."""
    host: object = error.context.get(OFFLINE) if isinstance(error, DotError) else None
    return host if isinstance(host, str) else None
