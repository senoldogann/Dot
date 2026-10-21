"""Which pages a login may be filled on (item 493), as a password manager's autofill decides it: a page whose host is a
host of one of the login's websites, or a host under it, over https; plain http only on this Mac itself. A page of any
other site never gets the login, whatever the model was told by the page it read."""

from urllib.parse import urlsplit

# The hosts a page may be served from over plain http: this Mac's own, where nothing travels.
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})


def website_hosts(websites: tuple[str, ...]) -> tuple[str, ...]:
    """The hosts of a login's websites, in lower case and without ``www.``, once each; a website that names no host
    (``example.com`` without its scheme reads as one) is read as one."""
    hosts: list[str] = []
    for website in websites:
        host: str = _host_of(website if "://" in website else f"https://{website}")
        if host and host not in hosts:
            hosts.append(host)
    return tuple(hosts)


def _host_of(url: str) -> str:
    host: str = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host.removeprefix("www.")


def fills_on(url: str, websites: tuple[str, ...]) -> bool:
    """Whether a login of ``websites`` may be filled on the page at ``url``: its host is one of theirs or under one,
    over https, or over http on this Mac itself."""
    parts = urlsplit(url)
    host: str = _host_of(url)
    if not host:
        return False
    if parts.scheme != "https" and not (parts.scheme == "http" and host in _LOOPBACK_HOSTS):
        return False
    # A host under a website's counts only under one that names a site of its own ("github.com", not "com").
    return any(host == site or ("." in site and host.endswith(f".{site}")) for site in website_hosts(websites))
