"""The push services Dot posts web pushes to (item 1311). A subscription's endpoint is where the daemon sends a signed
message that carries up to 140 characters of a question, a draft's subject or a result, so an endpoint is taken only
on a push service of a browser's maker: ``[notify] push_hosts``, whose entries are a host name (``web.push.apple.com``),
``*.`` and a domain for every host under it, and a path that the endpoint must begin with (``push.example/wp/``). A
domain that also serves what anyone may host (Google's ``googleapis.com``, whose Cloud Storage buckets are named by the
caller) is never a ``*.`` entry: Google's push hosts are listed one by one. A port is not part of an entry: a
host is the service's whatever port it names. Kept apart from :mod:`dotagent.notify.push`, whose sender pulls in HTTP
clients the server's request models do not need."""

from collections.abc import Sequence
from urllib.parse import unquote, urlsplit

_FORBIDDEN_CHARS: frozenset[str] = frozenset("\\ \t\r\n")
_WILDCARD: str = "*."


def _splits(entry: str) -> tuple[str, str]:
    """The host part and the path prefix (``""`` for any path) of one entry of ``[notify] push_hosts``."""
    host, slash, rest = entry.strip().partition("/")
    return host.lower().rstrip("."), (slash + rest if slash else "")


def _host_matches(host: str, pattern: str) -> bool:
    if pattern.startswith(_WILDCARD):
        return host.endswith(pattern[1:]) and len(host) > len(pattern) - 1
    return host == pattern


def push_host_problem(endpoint: str, hosts: Sequence[str], *, https_only: bool = True) -> str | None:
    """Why ``endpoint`` is not an address Dot posts a push to, or None when it is one: it is https, names a host that
    one entry of ``hosts`` covers (and the path that entry asks for), and carries no sign-in part, backslash or dot
    segment, by which what Dot checks and what the HTTP client reaches could differ. ``https_only`` False leaves the
    scheme to the check where the address came in (a stored one is at a host of the list all the same)."""
    if any(char in _FORBIDDEN_CHARS or ord(char) < 0x20 for char in endpoint):
        return "the address has a space, a backslash or a control character"
    parts = urlsplit(endpoint)
    if parts.scheme not in ({"https"} if https_only else {"https", "http"}):
        return "a push service is reached over https only"
    if "@" in parts.netloc:
        return "the address has a sign-in part before its host"
    try:
        hostname: str | None = parts.hostname
        port: int | None = parts.port
    except ValueError:
        return "the address has a port that is not a number"
    if hostname is None or hostname == "":
        return "the address names no host"
    if port == 0:
        return "the address has port 0"
    host: str = hostname.lower().rstrip(".")
    path: str = unquote(parts.path)
    if any(segment in {".", ".."} for segment in path.split("/")):
        return "the address has a dot segment in its path"
    for entry in hosts:
        pattern, prefix = _splits(entry)
        if pattern != "" and _host_matches(host, pattern) and path.startswith(prefix):
            return None
    return f"{host} is not a push service Dot sends to (Apple, Google Chrome, Firefox or Windows)"
