"""Which pages a login may be filled on (item 493), as a password manager's autofill decides it: a page whose host is a
host of one of the login's websites, or a host under it, on the port the website names (its scheme's own when it names
none), over https; plain http only on this Mac itself. A page of any other site never gets the login, whatever the
model was told by the page it read."""

import contextlib
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
    """The host as a browser's address writes it: lower case, without a final dot or ``www.``, and in its ASCII form
    (item 786: a website saved as ``bücher.de`` is the page ``xn--bcher-kva.de`` Chrome is on). Python's codec is IDNA
    2003, which maps some letters to others (``ß`` to ``ss``, so ``straße.de`` would read as ``strasse.de``, another
    site): a host it does not give back unchanged is kept as it is, in Unicode, and matches no page."""
    host: str = (parts.hostname or "").lower().rstrip(".")
    if not host.isascii():
        with contextlib.suppress(UnicodeError):
            ascii_host: str = host.encode("idna").decode("ascii")
            if ascii_host.encode("ascii").decode("idna") == host:
                host = ascii_host
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


def site_key(website: str) -> tuple[str, int | None] | None:
    """The site a login's ``website`` names, as its host (as :func:`website_hosts` reads it) and the port it names,
    ``None`` for none or for its scheme's own (``github.com``, ``https://github.com`` and ``https://GitHub.com:443/``
    are one site); ``None`` when the website cannot be read, or names no host or a port that is no number."""
    parts: SplitResult | None = _parts_of(website.strip())
    if parts is None:
        return None
    try:
        port: int | None = parts.port
    except ValueError:
        return None
    host: str = _host_of(parts)
    if not host:
        return None
    return host, None if port == _SCHEME_PORTS.get(parts.scheme.lower()) else port


def same_site(first: str, second: str) -> bool:
    """Whether two websites name one site, by host and port (:func:`site_key`) and not by how they are written: a
    login the owner saved as ``shop.example.com`` and the address ``https://shop.example.com`` of a page are one."""
    key: tuple[str, int | None] | None = site_key(first)
    return key is not None and key == site_key(second)


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
    in the tab it was typed into and to no other, and two fills of the same value into the same origin are equal, so
    that it is kept once."""

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


def _home(host: str, port: int | None) -> str:
    """The address of a login's site: its host with the port the website names, over https, or over http on this Mac
    itself (where a login of http fills); no path or query, which a website may carry and need not be repeated."""
    scheme: str = "http" if host in _LOOPBACK_HOSTS else "https"
    return f"{scheme}://{host}" if port is None else f"{scheme}://{host}:{port}"


def elsewhere_advice(url: str, websites: tuple[str, ...], source_name: str) -> str:
    """What a run does when a login of ``websites`` (kept in ``source_name``) was not filled on the page at ``url``
    (item 1208): it goes to the login's own site, the one address the login is known for, and fills there. A site that
    signs in on another host (App Store Connect on ``account.apple.com``) brings it back to such a page: it does not
    fill there and tells the owner the host it was sent to, which the login does not name. Whether a login should name
    that host too is the owner's: no list of the suffixes names are sold under is read here, so two hosts that end
    alike (``co.uk``, ``github.io``) say nothing of one owner, and a page that names itself a sign-in is what a phishing
    page does too. What fills is :func:`fills_on` alone; these words only send the run to a page where it may."""
    sites: tuple[tuple[str, int | None], ...] = _sites(websites)
    if not sites:
        return f"The login names no website: tell the owner to add the site's address to it in {source_name}."
    host, port = sites[0]
    home: str = _home(host, port)
    parts: SplitResult | None = _split(url)
    page: str = "" if parts is None else _host_of(parts)
    if page and any(page == site for site, _ in sites):
        return (
            f"This page is on the login's own host but not over https or not on its port: open {home} with "
            "browser_open and fill there."
        )
    sent: str = f" to {page}" if page else ""
    there: str = page or "that page"
    return (
        f"Open the login's own site with browser_open {home} and call browser_fill_login on its sign-in page there. "
        f"If that site sends you back{sent} or to another site to sign in, do not fill there: tell the owner, in one "
        f"line, that the site signs in on {there}, which the login in {source_name} does not name, and what they do "
        f"once if they know {there} as the site's own: {_owner_adds(source_name)}. Then go on with what needs no "
        "sign-in."
    )


def _owner_adds(source_name: str) -> str:
    """The one step the owner takes for a login to fill on a host it does not name (item 1958): Dot's vault keeps one
    website a login, so the host is a login of its own there; 1Password keeps several, so it is one more of the item's.
    Only the owner does it; a run never adds a host to a login."""
    if source_name == "1Password":
        return "add that host to the item's websites in 1Password"
    return "add a login for that host in Settings › Vault, with the same user name and password"
