"""Recon (item 776, docs/recon.md): what a site or a host says about itself before anything is tried against it.

A passive audit reads what any browser's visit reads: the addresses the name has, the TLS certificate, the HTTP answer
and its headers, and the technology the page and its headers name; from those it writes what a site's owner should fix.
An active audit also asks which of the common ports answer, and reads the banner a service gives without a word sent to
it. It runs only on a host the owner listed in ``[recon] owned_hosts``: scanning a host is not reading a page, and the
one who runs it must be the one who may.

Nothing here sends a payload, guesses a password or tries to exploit what it finds: the tool writes findings, and what
to do about them is the run's work or the owner's.
"""

import asyncio
import contextlib
import ipaddress
import re
import ssl
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.x509.oid import NameOID
from pydantic import Field

from dotagent.core.errors import DotError, ToolInputError, WebError
from dotagent.core.text import truncate_tail
from dotagent.policy.types import ActionInfo
from dotagent.tools.base import (
    ALL_MODES,
    RegisteredTool,
    ToolArgs,
    ToolResult,
    fail,
    make_tool,
    mark_untrusted,
    ok,
)
from dotagent.tools.context import ToolContext
from dotagent.tools.web import checked_address
from dotagent.web.client import WebClient, checked_addresses
from dotagent.web.text import decode_body
from dotagent.web.types import FetchedDocument

RECON_TOOL: str = "recon"
_TIMEOUT_S: float = 90.0
_SUMMARY_CHARS: int = 120
_MAX_BODY_BYTES: int = 512_000
_CONNECT_TIMEOUT_S: float = 8.0
_PORT_TIMEOUT_S: float = 0.9
_BANNER_TIMEOUT_S: float = 1.5
_BANNER_CHARS: int = 160
_DAYS_SOON: int = 30
_MAX_FINDINGS: int = 12
_MAX_TECH: int = 8

# The ports a host of the owner's is asked about. The list is the usual public services, not a range: a scan of every
# port is long, and the services below are the ones that answer a wrong configuration.
_PORTS: Mapping[int, str] = {
    21: "ftp",
    22: "ssh",
    23: "telnet",
    25: "smtp",
    53: "dns",
    80: "http",
    110: "pop3",
    111: "rpcbind",
    135: "msrpc",
    139: "netbios",
    143: "imap",
    443: "https",
    445: "smb",
    587: "submission",
    993: "imaps",
    995: "pop3s",
    1433: "mssql",
    1521: "oracle",
    2049: "nfs",
    3000: "http-alt",
    3306: "mysql",
    3389: "rdp",
    5432: "postgresql",
    5900: "vnc",
    6379: "redis",
    8000: "http-alt",
    8080: "http-alt",
    8081: "http-alt",
    8443: "https-alt",
    8888: "http-alt",
    9000: "http-alt",
    9200: "elasticsearch",
    11211: "memcached",
    27017: "mongodb",
}
# The ports whose service says who it is before anything is sent; on the others a read would wait for a request.
_BANNER_PORTS: frozenset[int] = frozenset({21, 22, 23, 25, 110, 143, 993, 995, 3306, 5432, 27017})
# A service of these answers a host that should not be reachable from the internet.
_PRIVATE_PORTS: frozenset[int] = frozenset(
    {21, 23, 111, 135, 139, 445, 1433, 1521, 2049, 3306, 3389, 5432, 5900, 6379, 9200, 11211, 27017}
)
# What the page or its headers may say about the stack, and the words that say it.
_TECH_MARKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/wp-content/|/wp-includes/|wp-json", re.IGNORECASE), "WordPress"),
    (re.compile(r"/sites/default/files/|drupal-settings-json|Drupal\.settings", re.IGNORECASE), "Drupal"),
    (re.compile(r"/media/jui/|/components/com_", re.IGNORECASE), "Joomla"),
    (re.compile(r"/_next/static/|__NEXT_DATA__", re.IGNORECASE), "Next.js"),
    (re.compile(r"/_nuxt/|__NUXT__", re.IGNORECASE), "Nuxt"),
    (re.compile(r"cdn\.shopify\.com|shopify\.theme", re.IGNORECASE), "Shopify"),
    (re.compile(r"static\.wixstatic\.com", re.IGNORECASE), "Wix"),
    (re.compile(r"squarespace\.com|static1\.squarespace\.com", re.IGNORECASE), "Squarespace"),
    (re.compile(r"wpengine\.com", re.IGNORECASE), "WP Engine"),
    (re.compile(r"__NEXT_DATA__|data-reactroot", re.IGNORECASE), "React"),
    (re.compile(r"ng-version=|ng-app=", re.IGNORECASE), "Angular"),
    (re.compile(r"data-v-[0-9a-f]{8}|__vue__", re.IGNORECASE), "Vue"),
    (re.compile(r"laravel_session", re.IGNORECASE), "Laravel"),
    (re.compile(r"csrfmiddlewaretoken", re.IGNORECASE), "Django"),
    (re.compile(r"phpsessid", re.IGNORECASE), "PHP"),
    (re.compile(r"asp\.net|__viewstate", re.IGNORECASE), "ASP.NET"),
)
_GENERATOR: re.Pattern[str] = re.compile(
    r"<meta[^>]+name=[\"']generator[\"'][^>]+content=[\"']([^\"']{1,120})", re.IGNORECASE
)
_TITLE: re.Pattern[str] = re.compile(r"<title[^>]*>(.{0,200}?)</title>", re.IGNORECASE | re.DOTALL)
_VERSIONED: re.Pattern[str] = re.compile(r"\d+\.\d+")


class ReconArgs(ToolArgs):
    target: str = Field(
        min_length=3,
        max_length=300,
        description="The site or host to look at: a domain (`example.com`), an address, or a URL whose path is kept.",
    )
    depth: Literal["passive", "ports"] = Field(
        default="passive",
        description=(
            "`passive` reads the addresses, the TLS certificate, the HTTP answer and its headers, as a browser's visit "
            "does. `ports` also asks which common ports answer, and reads their banners: only on a host listed in "
            "[recon] owned_hosts, one you own or may test."
        ),
    )


@dataclass(frozen=True)
class Target:
    """The address to look at: the URL as its path is read, the host and the port."""

    url: str
    host: str
    port: int


@dataclass(frozen=True)
class TlsInfo:
    """What the certificate of a host says: for whom it was made, by whom, until when and which names it covers."""

    subject: str
    issuer: str
    not_after: datetime
    names: tuple[str, ...]
    verified: bool
    covers: bool


@dataclass(frozen=True)
class TlsCheck:
    """The certificate of a host, or the reason there is none to read."""

    info: TlsInfo | None
    error: str


@dataclass(frozen=True)
class HttpAnswer:
    """The answer of a site: where it ended, its status, its headers and a piece of its body for the stack it names."""

    document: FetchedDocument
    scheme: str
    fallback: bool


def parse_target(text: str) -> Target | str:
    """The target ``text`` names, or the line that says why it is none. A name with no scheme is read over https, and a
    URL keeps its path; a name with a port keeps it."""
    address: str = text.strip()
    if not address:
        return "The target is empty: name a domain, such as example.com."
    if "://" not in address:
        address = f"https://{address}"
    try:
        parts = urlsplit(address)
    except ValueError as exc:
        return f"The target is not an address: {exc}"
    if parts.scheme not in {"http", "https"}:
        return f"The target must be a site: its scheme is {parts.scheme!r}, not http or https."
    if parts.username is not None or parts.password is not None:
        return "The target must not carry a user name or a password."
    host: str | None = parts.hostname
    if host is None or not host.strip("."):
        return "The target names no host."
    try:
        port: int = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return "The target's port is not a number between 1 and 65535."
    if not 1 <= port <= 65_535:
        return "The target's port is not a number between 1 and 65535."
    try:
        ascii_host: str = host.encode("idna").decode("ascii")
    except UnicodeError:
        return "The target's host is not a name the network can read."
    path: str = parts.path or "/"
    query: str = f"?{parts.query}" if parts.query else ""
    return Target(url=f"{parts.scheme}://{parts.netloc}{path}{query}", host=ascii_host.lower(), port=port)


def owned(host: str, owned_hosts: Sequence[str]) -> bool:
    """Whether ``host`` is one the owner listed, or a subdomain of one: ``example.com`` covers ``www.example.com`` and
    nothing that only ends in the same letters (``notexample.com``)."""
    wanted: str = host.strip().lower().rstrip(".")
    return any(wanted == entry or wanted.endswith(f".{entry}") for entry in owned_hosts)


def _header(headers: Mapping[str, str], name: str) -> str:
    return headers.get(name.lower(), "")


def technology(headers: Mapping[str, str], body: str) -> tuple[str, ...]:
    """The stack the headers and the body name, each once, at most :data:`_MAX_TECH` of them."""
    found: list[str] = []
    server: str = _header(headers, "server")
    if server:
        found.append(server.split("/")[0].strip()[:40])
    powered: str = _header(headers, "x-powered-by")
    if powered:
        found.append(powered.split(",")[0].strip()[:40])
    via: str = _header(headers, "via")
    if via:
        found.append(f"via {via.split(',')[0].strip()[:30]}")
    if _header(headers, "cf-ray"):
        found.append("Cloudflare")
    if _header(headers, "x-vercel-id"):
        found.append("Vercel")
    if _header(headers, "x-amz-cf-id") or _header(headers, "x-amz-request-id"):
        found.append("AWS")
    generator: re.Match[str] | None = _GENERATOR.search(body)
    if generator is not None:
        found.append(generator.group(1).strip()[:60])
    for pattern, name in _TECH_MARKS:
        if pattern.search(body) or pattern.search(" ".join(headers.values())):
            found.append(name)
    if _header(headers, "x-shopify-stage") or _header(headers, "x-shopid"):
        found.append("Shopify")
    return tuple(dict.fromkeys(found))[:_MAX_TECH]


def page_title(body: str) -> str:
    """The title of the page, one line, or an empty string."""
    title: re.Match[str] | None = _TITLE.search(body)
    if title is None:
        return ""
    return " ".join(title.group(1).split())[:120]


def header_findings(scheme: str, headers: Mapping[str, str]) -> tuple[str, ...]:
    """What the HTTP headers should have and do not, and what they say too much of."""
    findings: list[str] = []
    csp: str = _header(headers, "content-security-policy")
    if not csp:
        findings.append(
            "No Content-Security-Policy: a script or an image a page takes from elsewhere runs in its visitors' "
            "browsers; a policy naming where its own things come from is the fix."
        )
    if scheme == "https" and not _header(headers, "strict-transport-security"):
        findings.append(
            "No Strict-Transport-Security: a browser may reach the site over http first, and that first request is "
            "the one a network in between can read."
        )
    if _header(headers, "x-content-type-options").lower() != "nosniff":
        findings.append(
            "No X-Content-Type-Options: nosniff: a browser may read a file as a kind it is not, and run it."
        )
    if not _header(headers, "x-frame-options") and "frame-ancestors" not in csp:
        findings.append(
            "No X-Frame-Options and no frame-ancestors: another site may put this one in a frame and take the clicks "
            "meant for it."
        )
    if not _header(headers, "referrer-policy"):
        findings.append("No Referrer-Policy: the addresses of the site's pages travel to every site they link to.")
    if not _header(headers, "permissions-policy"):
        findings.append(
            "No Permissions-Policy: a page a script puts in the site may ask for the camera, the microphone and the "
            "location."
        )
    server: str = _header(headers, "server")
    if server and _VERSIONED.search(server):
        findings.append(f"The Server header names its version ({server[:80]}): a scanner learns what to try first.")
    powered: str = _header(headers, "x-powered-by")
    if powered:
        findings.append(f"The X-Powered-By header names the stack ({powered[:80]}).")
    if _header(headers, "access-control-allow-origin").strip() == "*":
        findings.append(
            "Access-Control-Allow-Origin is *: any site's script may read this one's answers in a visitor's browser."
        )
    return tuple(findings)


def _covers(name: str, host: str) -> bool:
    """Whether the certificate name ``name`` covers ``host``: a wildcard covers one label, not the bare domain and not
    two labels, and an address is written the one way both sides agree on."""
    certificate: str = name.strip().lower().rstrip(".")
    wanted: str = host.strip().lower().rstrip(".")
    with contextlib.suppress(ValueError):
        certificate = str(ipaddress.ip_address(certificate))
    with contextlib.suppress(ValueError):
        wanted = str(ipaddress.ip_address(wanted))
    if certificate.startswith("*."):
        return wanted.endswith(certificate[1:]) and wanted.count(".") == certificate.count(".")
    return certificate == wanted


def tls_findings(info: TlsInfo, now: datetime) -> tuple[str, ...]:
    """What the certificate says that its owner should see: an expiry close or past, a name it does not cover, a
    verification that failed."""
    findings: list[str] = []
    days: int = (info.not_after - now).days
    if days < 0:
        findings.append(f"The certificate expired {abs(days)} days ago ({info.not_after.date().isoformat()}).")
    elif days <= _DAYS_SOON:
        findings.append(f"The certificate expires in {days} days ({info.not_after.date().isoformat()}).")
    if not info.verified:
        findings.append(
            "The certificate does not verify on this Mac: it is self-signed, expired, or its chain is incomplete."
        )
    if not info.covers:
        findings.append(f"The certificate does not name the host: it covers {', '.join(info.names) or 'no name'}.")
    return tuple(findings)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _banner(data: bytes) -> str:
    text: str = data.decode("utf-8", "replace")
    return _one_line(text)[:_BANNER_CHARS]


async def _http(client: WebClient, target: Target) -> HttpAnswer | str:
    """The site's answer over the scheme its target names, and over the other one when that scheme could not be read
    at all: a site that answers only one of the two is reported with the other one's answer, marked as a fallback."""
    first: str = urlsplit(target.url).scheme
    answer: HttpAnswer | str = await _fetch(client, target, first)
    if isinstance(answer, HttpAnswer):
        return answer
    second: str = "http" if first == "https" else "https"
    fallback: HttpAnswer | str = await _fetch(client, target, second)
    if isinstance(fallback, HttpAnswer):
        return HttpAnswer(document=fallback.document, scheme=fallback.scheme, fallback=True)
    return answer


async def _fetch(client: WebClient, target: Target, scheme: str) -> HttpAnswer | str:
    """One GET of the target over ``scheme``, the body kept whole up to the limit."""
    parts = urlsplit(target.url)
    hostname: str = parts.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port: str = "" if parts.port is None else f":{parts.port}"
    url: str = f"{scheme}://{hostname}{port}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")
    try:
        document: FetchedDocument = await client.get(url, _MAX_BODY_BYTES)
    except DotError as exc:
        return f"{scheme} could not be read: {_one_line(exc.message)[:300]}"
    except OSError as exc:
        return f"{scheme} could not be read: {_one_line(str(exc))[:300]}"
    return HttpAnswer(document=document, scheme=scheme, fallback=False)


async def _tls(addresses: Sequence[str], host: str, port: int) -> TlsCheck:
    """The certificate ``host`` gives on ``port``, read once verified and once not, so that a bad one is reported
    rather than lost. The handshake goes to the address the web client's check read, with ``host`` kept as the TLS
    server name: a second lookup cannot move the connection elsewhere."""
    if not addresses:
        return TlsCheck(info=None, error="the name does not resolve, so there is no certificate to read")
    info: TlsInfo | None = None
    error: str = ""
    verification_failed: bool = False
    for verify in (True, False):
        context: ssl.SSLContext = ssl.create_default_context()
        if not verify:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        for address in addresses:
            try:
                certificate: bytes | None = await _peer_certificate(address, port, host, context)
            except ssl.SSLCertVerificationError as exc:
                # Only a certificate that does not verify is worth reading unverified; a connection that failed in
                # transit is not (item 776 review).
                verification_failed = True
                error = _one_line(str(exc))[:200]
                continue
            except (OSError, TimeoutError) as exc:
                error = _one_line(str(exc))[:200]
                continue
            if certificate is None:
                error = "the host gave no certificate"
                continue
            info = _read_certificate(certificate, host, verified=verify)
            error = ""
            break
        if info is not None:
            break
        if verify and not verification_failed:
            break
    if info is None:
        return TlsCheck(info=None, error=error or "no certificate was read")
    return TlsCheck(info=info, error="")


async def _peer_certificate(address: str, port: int, server_hostname: str, context: ssl.SSLContext) -> bytes | None:
    _reader, writer = await asyncio.wait_for(
        asyncio.open_connection(address, port, ssl=context, server_hostname=server_hostname),
        timeout=_CONNECT_TIMEOUT_S,
    )
    try:
        ssl_object: ssl.SSLObject | None = writer.get_extra_info("ssl_object")
        return None if ssl_object is None else ssl_object.getpeercert(binary_form=True)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


def _read_certificate(der: bytes, host: str, verified: bool) -> TlsInfo | None:
    """The certificate's names and dates, or ``None`` for bytes that are no certificate."""
    try:
        certificate: x509.Certificate = x509.load_der_x509_certificate(der)
    except ValueError:
        return None
    common: list[str] = [str(value.value) for value in certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
    issuer_parts: list[str] = [
        str(value.value)
        for oid in (NameOID.ORGANIZATION_NAME, NameOID.COMMON_NAME)
        for value in certificate.issuer.get_attributes_for_oid(oid)
    ]
    names: tuple[str, ...] = tuple(common)
    with contextlib.suppress(x509.ExtensionNotFound):
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        names = (*names, *san.value.get_values_for_type(x509.DNSName))
        names = (*names, *(str(value) for value in san.value.get_values_for_type(x509.IPAddress)))
    unique: tuple[str, ...] = tuple(dict.fromkeys(name for name in names if name))
    return TlsInfo(
        subject=common[0] if common else "",
        issuer=", ".join(dict.fromkeys(issuer_parts)) or "unknown",
        not_after=certificate.not_valid_after_utc,
        names=unique,
        verified=verified,
        covers=any(_covers(name, host) for name in unique),
    )


async def _probe(address: str, port: int) -> tuple[int, str] | None:
    """The port and the banner its service gives before anything is sent (empty for a service that says nothing), or
    ``None`` when nothing answers there. It connects to ``address``, not to the name, so the check that read the
    address cannot be undone by a second lookup."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(address, port), timeout=_PORT_TIMEOUT_S)
    except (OSError, TimeoutError):
        return None
    try:
        if port not in _BANNER_PORTS:
            return port, ""
        try:
            data: bytes = await asyncio.wait_for(reader.read(256), timeout=_BANNER_TIMEOUT_S)
        except (OSError, TimeoutError):
            return port, ""
        return port, _banner(data)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def open_ports(addresses: Sequence[str], ports: Sequence[int]) -> tuple[tuple[int, str], ...]:
    """The ports of ``ports`` that answer on any of ``addresses``, with the banner their service gives, in the order
    of the ports."""
    results: list[tuple[int, str] | None] = await asyncio.gather(
        *(_probe(address, port) for address in addresses for port in ports)
    )
    answered: dict[int, str] = {}
    for result in results:
        if result is not None:
            port, banner = result
            answered.setdefault(port, banner)
    return tuple(sorted(answered.items()))


def _render(
    target: Target,
    http: HttpAnswer | str,
    tls: TlsCheck,
    addresses: tuple[tuple[str, ...], tuple[str, ...]],
    tech: tuple[str, ...],
    title: str,
    findings: Sequence[str],
    ports: tuple[tuple[int, str], ...] | None,
    now: datetime,
) -> str:
    """The report the run reads."""
    v4, v6 = addresses
    kind: str = "passive and ports" if ports is not None else "passive"
    lines: list[str] = [f"Recon of {target.host} ({kind})."]
    lines.append("")
    lines.append("Addresses")
    lines.append(f"  A: {', '.join(v4) if v4 else 'none'}")
    lines.append(f"  AAAA: {', '.join(v6) if v6 else 'none'}")
    lines.append("")
    lines.append("HTTP")
    if isinstance(http, str):
        lines.append(f"  {http}")
    else:
        document: FetchedDocument = http.document
        redirects: int = len(document.redirected_from)
        where: str = ""
        if redirects:
            from_url: str = document.redirected_from[0]
            plural: str = "s" if redirects != 1 else ""
            where = f" ({redirects} redirect{plural} from {from_url})"
        lines.append(f"  {http.scheme} answered {document.status} {document.media_type or 'with no type'}{where}.")
        if http.fallback:
            lines.append("  The target's own scheme could not be read; this is the other one.")
        if title:
            lines.append(f"  Title: {title}")
    lines.append("")
    lines.append("TLS")
    if tls.info is None:
        lines.append(f"  No certificate read: {tls.error}.")
    else:
        info: TlsInfo = tls.info
        days: int = (info.not_after - now).days
        lines.append(
            f"  Issued to {info.subject or 'no common name'} by {info.issuer}; valid until "
            f"{info.not_after.date().isoformat()} ({days} days)."
        )
        lines.append(f"  Covers: {', '.join(info.names) or 'no name'}")
        lines.append(f"  Verified on this Mac: {'yes' if info.verified else 'no'}")
    lines.append("")
    lines.append("Technology")
    lines.append(f"  {', '.join(tech) if tech else 'nothing the headers or the page name'}")
    lines.append("")
    lines.append("Findings")
    if findings:
        lines.extend(f"  {index}. {finding}" for index, finding in enumerate(findings, start=1))
    else:
        lines.append("  None from what this audit reads.")
    if ports is not None:
        lines.append("")
        lines.append(f"Ports that answer ({len(ports)} of {len(_PORTS)})")
        if not ports:
            lines.append("  none of them")
        for port, banner in ports:
            service: str = _PORTS.get(port, "?")
            note: str = f" — {banner}" if banner else ""
            lines.append(f"  {port}/{service}{note}")
    return "\n".join(lines)


def _port_findings(ports: Sequence[tuple[int, str]]) -> tuple[str, ...]:
    """What an open port of the host says: a service that belongs behind the firewall, or a name and a version."""
    findings: list[str] = []
    for port, banner in ports:
        if port in _PRIVATE_PORTS:
            findings.append(
                f"Port {port} ({_PORTS.get(port, '?')}) answers: a service like this belongs behind the host's "
                "firewall, or on the loopback address."
            )
        elif banner and _VERSIONED.search(banner):
            findings.append(f"Port {port} ({_PORTS.get(port, '?')}) names its version: {banner[:100]}")
    return tuple(findings)[:_MAX_FINDINGS]


def _classify(args: ReconArgs, ctx: ToolContext) -> ActionInfo:
    """The action of a call: reading a site's public face, or asking for its ports, which is the heavier one."""
    url, flags = checked_address(args.target, ctx.runtime.config)
    parsed: Target | str = parse_target(url)
    host: str = parsed.host if isinstance(parsed, Target) else url.strip()[:200]
    active: bool = args.depth == "ports"
    summary: str = f"Recon: {len(_PORTS)} common ports of {host}" if active else f"Recon: the public face of {host}"
    return ActionInfo(
        tool=RECON_TOOL,
        kind="network",
        risk="medium" if active else "low",
        summary=summary[:_SUMMARY_CHARS],
        target=host,
        flags=flags,
    )


async def _run(args: ReconArgs, ctx: ToolContext) -> ToolResult:
    try:
        checked, _flags = checked_address(args.target, ctx.runtime.config)
    except ToolInputError as exc:
        return fail(exc.message)
    parsed: Target | str = parse_target(checked)
    if isinstance(parsed, str):
        return fail(parsed)
    target: Target = parsed
    if args.depth == "ports" and not owned(target.host, ctx.runtime.config.recon.owned_hosts):
        return fail(
            f"{target.host} is not in [recon] owned_hosts, and ports are scanned only on a host you own or may test. "
            "Add the domain there (it covers its subdomains), or run the passive audit instead."
        )
    allow_private: bool = ctx.runtime.config.web.allow_private_hosts
    try:
        resolved: tuple[str, ...] = await checked_addresses(target.host, target.port, allow_private_hosts=allow_private)
    except WebError as exc:
        return fail(exc.message)
    now: datetime = datetime.now(UTC)
    addresses: tuple[tuple[str, ...], tuple[str, ...]] = (
        tuple(address for address in resolved if ":" not in address),
        tuple(address for address in resolved if ":" in address),
    )
    tls_port: int = target.port if urlsplit(target.url).scheme == "https" else 443
    http_task = asyncio.create_task(_http(ctx.runtime.web, target))
    tls_task = asyncio.create_task(_tls(resolved, target.host, tls_port))
    ports_task = asyncio.create_task(open_ports(resolved, tuple(_PORTS))) if args.depth == "ports" else None
    http_result: HttpAnswer | str = await http_task
    tls_result: TlsCheck = await tls_task
    ports: tuple[tuple[int, str], ...] | None = None
    if ports_task is not None:
        ports = await ports_task

    body: str = ""
    headers: dict[str, str] = {}
    scheme: str = urlsplit(target.url).scheme
    title: str = ""
    if isinstance(http_result, HttpAnswer):
        document: FetchedDocument = http_result.document
        scheme = http_result.scheme
        headers = {name.lower(): value for name, value in document.headers}
        try:
            body = decode_body(document.body, document.charset)
        except WebError:
            # A charset the site names but Python does not know costs the page's words, not the header findings.
            body = ""
        title = page_title(body)
    tech: tuple[str, ...] = technology(headers, body) if headers or body else ()
    findings: list[str] = [*header_findings(scheme, headers)] if headers else []
    if tls_result.info is not None:
        findings.extend(tls_findings(tls_result.info, now))
    if ports is not None:
        findings.extend(_port_findings(ports))
    unique: tuple[str, ...] = tuple(dict.fromkeys(findings))[:_MAX_FINDINGS]
    report: str = _render(target, http_result, tls_result, addresses, tech, title, unique, ports, now)
    return mark_untrusted(ok(truncate_tail(report, ctx.output_chars)))


def build_recon_tool() -> RegisteredTool:
    """The recon tool (item 776): the public face of a site, and the ports of a host of the owner's."""
    return make_tool(
        name=RECON_TOOL,
        description=(
            "Look at a site or a host the way a security engineer starts: its addresses, its TLS certificate, its HTTP "
            "answer and headers, and the technology it names, with what its owner should fix. With depth `ports` on a "
            "host in [recon] owned_hosts, which ports answer and what their services say. It only reads: it sends no "
            "payload and tries no exploit."
        ),
        args_model=ReconArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_TIMEOUT_S,
        classify=_classify,
        run=_run,
    )
