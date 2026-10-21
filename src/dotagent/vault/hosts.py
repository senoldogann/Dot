"""Which pages a login may be filled on (item 493), as a password manager's autofill decides it: a page whose host is a
host of one of the login's websites, or a host under it, on the port the website names (its scheme's own when it names
none), over https; plain http only on this Mac itself. A page of any other site never gets the login, whatever the
model was told by the page it read."""

from urllib.parse import SplitResult, urlsplit

# The hosts a page may be served from over plain http: this Mac's own, where nothing travels.
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})
# The port of an address that names none, by its scheme.
_SCHEME_PORTS: dict[str, int] = {"https": 443, "http": 80}


def website_hosts(websites: tuple[str, ...]) -> tuple[str, ...]:
    """The hosts of a login's websites, in lower case and without ``www.``, once each; a website that names no host
    (``example.com`` without its scheme reads as one) is read as one."""
    hosts: list[str] = []
    for website in websites:
        host: str = _host_of(_parts_of(website))
        if host and host not in hosts:
            hosts.append(host)
    return tuple(hosts)


def _parts_of(website: str) -> SplitResult:
    """A website as an address: one that names no scheme (``example.com``) reads as https."""
    return urlsplit(website if "://" in website else f"https://{website}")


def _host_of(parts: SplitResult) -> str:
    host: str = (parts.hostname or "").lower().rstrip(".")
    return host.removeprefix("www.")


def _sites(websites: tuple[str, ...]) -> tuple[tuple[str, int | None], ...]:
    """The hosts of a login's websites, each with the port it names (``None`` when it names none); a website whose port
    cannot be read names no site."""
    sites: list[tuple[str, int | None]] = []
    for website in websites:
        parts: SplitResult = _parts_of(website)
        try:
            port: int | None = parts.port
        except ValueError:
            continue
        host: str = _host_of(parts)
        if host and (host, port) not in sites:
            sites.append((host, port))
    return tuple(sites)


def fills_on(url: str, websites: tuple[str, ...]) -> bool:
    """Whether a login of ``websites`` may be filled on the page at ``url``: its host is one of theirs or under one, on
    the port the website names (the page's scheme's own when it names none), over https, or over http on this Mac
    itself."""
    parts: SplitResult = urlsplit(url)
    host: str = _host_of(parts)
    if not host:
        return False
    if parts.scheme != "https" and not (parts.scheme == "http" and host in _LOOPBACK_HOSTS):
        return False
    own: int = _SCHEME_PORTS[parts.scheme]
    try:
        named: int | None = parts.port
    except ValueError:
        return False
    port: int = own if named is None else named
    # A host under a website's counts only under one that names a site of its own ("github.com", not "com").
    return any(
        (host == site or ("." in site and host.endswith(f".{site}"))) and port == (own if wanted is None else wanted)
        for site, wanted in _sites(websites)
    )
