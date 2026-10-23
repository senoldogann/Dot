"""Which network addresses the web tools may connect to: the public internet, unless the user allowed more."""

import asyncio
import ipaddress
import socket
from collections.abc import Sequence

# IPv6 addresses whose last 32 bits are an IPv4 address: the well-known prefix of NAT64, which a gateway translates to
# that address, and the old IPv4-compatible form.
_NAT64: ipaddress.IPv6Network = ipaddress.IPv6Network("64:ff9b::/96")
_IPV4_COMPATIBLE: ipaddress.IPv6Network = ipaddress.IPv6Network("::/96")


def _carried_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address ``address`` carries (mapped, compatible or through NAT64), or ``None``."""
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address in _NAT64 or address in _IPV4_COMPATIBLE:
        return ipaddress.IPv4Address(int(address) & 0xFFFF_FFFF)
    return None


def is_public_address(address: str) -> bool:
    """Whether ``address`` is a globally routable unicast address.

    Loopback, private, link-local (cloud metadata lives there), shared carrier-grade and multicast ranges are not.
    An IPv4 address written as IPv6 (``::ffff:127.0.0.1``, ``::127.0.0.1``, ``64:ff9b::127.0.0.1``) is judged by its
    IPv4 part (item 215).
    """
    parsed: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(address)
    carried: ipaddress.IPv4Address | None = _carried_ipv4(parsed) if isinstance(parsed, ipaddress.IPv6Address) else None
    if carried is not None:
        parsed = carried
    return parsed.is_global and not parsed.is_multicast


def is_private_text(address: str) -> bool:
    """Whether ``address``, as Chrome or a resolver writes it (an IPv6 address may come in brackets), is not a public
    one; text that is no address is not taken for one (item 554)."""
    try:
        return not is_public_address(address.strip("[]"))
    except ValueError:
        return True


async def non_public_address(host: str) -> str | None:
    """The first address of ``host`` that is not a public one, or ``None`` when every address it has is public or none
    can be found (item 554). A host that is an address is judged as it is, a name by what it resolves to now: the rule
    of the plain client (``web.allow_private_hosts``) for a page the browser read for the web tools."""
    literal: str = host.strip("[]")
    try:
        ipaddress.ip_address(literal)
    except ValueError:
        pass
    else:
        return literal if is_private_text(literal) else None
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return None
    return next((str(info[4][0]) for info in infos if is_private_text(str(info[4][0]))), None)


def order_addresses(addresses: Sequence[str]) -> tuple[str, ...]:
    """Each address once, IPv4 first: a Mac without a working IPv6 route would stall on an AAAA record."""
    unique: tuple[str, ...] = tuple(dict.fromkeys(addresses))
    version_four: tuple[str, ...] = tuple(a for a in unique if ipaddress.ip_address(a).version == 4)
    return version_four + tuple(a for a in unique if a not in version_four)
