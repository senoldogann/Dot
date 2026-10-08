"""Which network addresses the web tools may connect to: the public internet, unless the user allowed more."""

import ipaddress
from collections.abc import Sequence


def is_public_address(address: str) -> bool:
    """Whether ``address`` is a globally routable unicast address.

    Loopback, private, link-local (cloud metadata lives there), shared carrier-grade and multicast ranges are not.
    An IPv4 address written as IPv6 (``::ffff:127.0.0.1``) is judged by its IPv4 part.
    """
    parsed: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(address)
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    return parsed.is_global and not parsed.is_multicast


def order_addresses(addresses: Sequence[str]) -> tuple[str, ...]:
    """Each address once, IPv4 first: a Mac without a working IPv6 route would stall on an AAAA record."""
    unique: tuple[str, ...] = tuple(dict.fromkeys(addresses))
    version_four: tuple[str, ...] = tuple(a for a in unique if ipaddress.ip_address(a).version == 4)
    return version_four + tuple(a for a in unique if a not in version_four)
