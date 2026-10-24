"""Which pages a login may be filled on (item 493), as a password manager's autofill decides it: a page whose host is a
host of one of the login's websites, or a host under it, on the port the website names (its scheme's own when it names
none), over https; plain http only on this Mac itself. A page of any other site never gets the login, whatever the
model was told by the page it read."""

from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

# The hosts a page may be served from over plain http: this Mac's own, where nothing travels.
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})
# The port of an address that names none, by its scheme.
_SCHEME_PORTS: dict[str, int] = {"https": 443, "http": 80}
# The first character that is not a control character.
_SPACE: int = 32
# The longest address of a site that a login of Dot's vault keeps.
MOST_WEBSITE_CHARS: int = 2000
# What a site that cannot be read is called: its address is not repeated, which may carry a password.
_UNREADABLE: str = "an unreadable address"
# Why an address with a user name or a password in it is no site of a login: it would be kept, listed and shown whole.
USERINFO_PROBLEM: str = "Write the site's address without a user name or a password in it, like github.com."


def website_hosts(websites: tuple[str, ...]) -> tuple[str, ...]:
    """The hosts of a login's websites, in lower case and without ``www.``, once each; a website that names no host
    (``example.com`` without its scheme reads as one) is read as one, and one that cannot be read names none."""
    hosts: list[str] = []
    for website in websites:
        parts: SplitResult | None = _parts_of(website)
        host: str = "" if parts is None else _host_of(parts)
        if host and host not in hosts:
            hosts.append(host)
    return tuple(hosts)


def _split(address: str) -> SplitResult | None:
    """``address`` split into its parts, or ``None`` when Python cannot read it (an unbalanced bracket, a character that
    makes a delimiter once normalized). What it cannot read says nothing: the error it raises would quote the address,
    and an address may carry a user name and a password."""
    try:
        return urlsplit(address)
    except ValueError:
        return None


def _parts_of(website: str) -> SplitResult | None:
    """A website as an address: one that names no scheme (``example.com``) reads as https; ``None`` when it cannot be
    read."""
    return _split(website if "://" in website else f"https://{website}")


def _host_of(parts: SplitResult) -> str:
    host: str = (parts.hostname or "").lower().rstrip(".")
    return host.removeprefix("www.")


def _sites(websites: tuple[str, ...]) -> tuple[tuple[str, int | None], ...]:
    """The hosts of a login's websites, each with the port it names (``None`` when it names none); a website whose port
    cannot be read names no site."""
    sites: list[tuple[str, int | None]] = []
    for website in websites:
        parts: SplitResult | None = _parts_of(website)
        if parts is None:
            continue
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
    parts: SplitResult | None = _split(url)
    if parts is None:
        return False
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


def website_problem(website: str) -> str | None:
    """Why ``website`` cannot be the site of a login of Dot's vault (item 621), in the words the page shows; ``None``
    when it can: a web address or the name of a site (``github.com``, ``https://github.com/login``,
    ``localhost:3000``)."""
    text: str = website.strip()
    if text == "" or any(char.isspace() or ord(char) < _SPACE or char == "\\" for char in text):
        return "Write the site's address, like github.com."
    if len(text) > MOST_WEBSITE_CHARS:
        return f"This address is too long ({MOST_WEBSITE_CHARS} characters at most)."
    given: SplitResult | None = _split(text)
    if given is None:
        return "This does not look like the address of a site: write it like github.com."
    if "://" in text and given.scheme.lower() not in ("http", "https"):
        return "Dot fills logins on web pages: write the site's name, like github.com."
    parts: SplitResult | None = _parts_of(text)
    if parts is None:
        return "This does not look like the address of a site: write it like github.com."
    if "@" in parts.netloc:
        return USERINFO_PROBLEM
    try:
        named: int | None = parts.port
    except ValueError:
        return "The port in this address is not a number."
    if named == 0:
        return "The port in this address is not a number."
    host: str = _host_of(parts)
    if host == "" or ("." not in host and host not in _LOOPBACK_HOSTS):
        return "This does not look like the address of a site: write it like github.com."
    return None


def site_label(website: str) -> str:
    """How a login of ``website`` is named: its host without ``www.``, with its port when the address names one that is
    not its scheme's own (``github.com``, ``localhost:3000``)."""
    parts: SplitResult | None = _parts_of(website.strip())
    if parts is None:
        return _UNREADABLE
    host: str = _host_of(parts)
    try:
        port: int | None = parts.port
    except ValueError:
        return host
    own: int = _SCHEME_PORTS.get(parts.scheme, 443)
    return host if port is None or port == own else f"{host}:{port}"


@dataclass(frozen=True, slots=True)
class SameOrigin:
    """The documents of one origin (item 621): the scheme, the host and the port of an address, as a test of the address
    of another document. A secret filled into a field is scoped to the origin of the document that holds the field
    (:class:`dotagent.browser.types.HeldValue`), not to every site the login names: what Dot does by what the secret
    is (keeping it out of the addresses a page sends, marking a field again) is done to the documents of that origin
    and to no other, and two fills of the same value into the same origin are equal, so that it is kept once."""

    scheme: str
    host: str
    port: int

    @classmethod
    def of(cls, address: str) -> "SameOrigin | None":
        """The origin of an http or https ``address``; ``None`` for any other, and for one Python cannot read. The host
        is as the browser keeps it, lower case and nothing else: ``example.com.`` (a host with a dot at its end) and
        ``example.com`` are two origins to Chrome, so they are two here, whatever the site rule says of the two names
        (:func:`fills_on` reads them as one site, which they are, and the value is scoped to the origin it was typed
        into)."""
        parts: SplitResult | None = _split(address)
        if parts is None:
            return None
        scheme: str = parts.scheme.lower()
        host: str = (parts.hostname or "").lower()
        if scheme not in _SCHEME_PORTS or host.strip(".") == "":
            return None
        try:
            port: int | None = parts.port
        except ValueError:
            return None
        return cls(scheme, host, _SCHEME_PORTS[scheme] if port is None else port)

    def __call__(self, address: str) -> bool:
        return SameOrigin.of(address) == self
