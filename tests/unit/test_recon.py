"""The recon tool (item 776): the target a call names, the host a port scan may ask, what the headers and the
certificate say, the ports that answer, and the site a real request reads."""

import asyncio
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import cast

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import ValidationError

from dotagent.config import ReconConfig, load_default_config
from dotagent.core.errors import WebError
from dotagent.tools.context import ToolContext
from dotagent.tools.recon import (
    ReconArgs,
    Target,
    TlsInfo,
    _covers,
    _http,
    _read_certificate,
    _run,
    _tls,
    header_findings,
    open_ports,
    owned,
    parse_target,
    technology,
    tls_findings,
)
from dotagent.web.client import WebClient, checked_addresses

_BODY: bytes = (
    b"<html><head><title>A test site</title>"
    b'<meta name="generator" content="WordPress 6.4"></head>'
    b'<body><script src="/wp-includes/js/jquery.js"></script></body></html>'
)


class _Handler(BaseHTTPRequestHandler):
    """A site that names its stack in its headers and its page, and answers every path."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("X-Powered-By", "PHP/7.4")
        self.end_headers()
        self.wfile.write(_BODY)

    def version_string(self) -> str:
        return "nginx/1.18.0"

    def log_message(self, format: str, *args: object) -> None:
        """Keep the test's output for the test."""


class _UnknownCharsetHandler(_Handler):
    """The same site, naming a character set Python does not know."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=x-unknown")
        self.send_header("X-Powered-By", "PHP/7.4")
        self.end_headers()
        self.wfile.write(_BODY)


@contextmanager
def _site(handler: type[BaseHTTPRequestHandler] = _Handler) -> Iterator[int]:
    """A site of this Mac on a port of its own, for as long as the block runs."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_bare_name_is_read_over_https() -> None:
    target = parse_target("example.com")
    assert isinstance(target, Target)
    assert target.url == "https://example.com/"
    assert (target.host, target.port) == ("example.com", 443)


def test_a_url_keeps_its_path_its_scheme_and_its_port() -> None:
    target = parse_target("http://example.com:8080/about?x=1")
    assert isinstance(target, Target)
    assert target.url == "http://example.com:8080/about?x=1"
    assert target.port == 8080


@pytest.mark.parametrize(
    "text",
    ["", "ftp://example.com", "https://user:secret@example.com", "https://example.com:99999"],
)
def test_a_target_that_is_no_site_is_a_line_that_says_why(text: str) -> None:
    assert isinstance(parse_target(text), str)


def test_a_listed_host_covers_its_subdomains_and_no_lookalike() -> None:
    assert owned("example.com", ("example.com",))
    assert owned("www.example.com", ("example.com",))
    assert owned("a.b.example.com", ("example.com",))
    assert not owned("notexample.com", ("example.com",))
    assert not owned("example.com.evil.test", ("example.com",))
    assert owned("192.0.2.10", ("192.0.2.10",))


def test_the_technology_is_what_the_headers_and_the_page_name() -> None:
    headers = {"server": "nginx/1.18.0", "x-powered-by": "PHP/7.4"}
    tech = technology(headers, _BODY.decode())
    assert "nginx" in tech
    assert "PHP/7.4" in tech
    assert "WordPress 6.4" in tech
    assert "WordPress" in tech


def test_every_header_a_site_should_send_is_named_when_it_is_missing() -> None:
    findings = header_findings("https", {"server": "nginx/1.18.0", "x-powered-by": "PHP/7.4"})
    text = " ".join(findings)
    for name in (
        "Content-Security-Policy",
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
    ):
        assert name in text
    assert "Server header names its version" in text
    assert "X-Powered-By" in text


def test_a_site_that_sends_the_headers_gets_no_finding_from_them() -> None:
    headers = {
        "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
        "strict-transport-security": "max-age=31536000",
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "strict-origin-when-cross-origin",
        "permissions-policy": "camera=(), microphone=()",
        "server": "nginx",
    }
    assert header_findings("https", headers) == ()


def test_a_wildcard_covers_one_label_and_not_the_bare_domain() -> None:
    assert _covers("*.example.com", "www.example.com")
    assert not _covers("*.example.com", "example.com")
    assert not _covers("*.example.com", "a.b.example.com")
    assert _covers("example.com", "EXAMPLE.com.")


def _certificate(names: tuple[str, ...]) -> bytes:
    """A self-signed certificate for ``names``, made for the test and kept in its DER form."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Dot test")]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=90))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name) for name in names]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.DER)


def test_a_certificate_is_read_with_its_names_its_issuer_and_what_it_covers() -> None:
    info = _read_certificate(_certificate(("example.com", "www.example.com")), "www.example.com", verified=True)
    assert info is not None
    assert info.subject == "example.com"
    assert info.issuer == "Dot test"
    assert info.names == ("example.com", "www.example.com")
    assert info.covers
    other = _read_certificate(_certificate(("example.com",)), "other.example", verified=True)
    assert other is not None
    assert not other.covers


def test_a_certificate_close_to_its_end_and_a_past_one_are_findings() -> None:
    now = datetime(2026, 10, 28, tzinfo=UTC)
    info = TlsInfo(
        subject="example.com",
        issuer="Let's Encrypt",
        not_after=now + timedelta(days=10),
        names=("example.com",),
        verified=True,
        covers=True,
    )
    assert "expires in 10 days" in tls_findings(info, now)[0]
    expired = TlsInfo(
        subject="example.com",
        issuer="Let's Encrypt",
        not_after=now - timedelta(days=3),
        names=("example.com",),
        verified=True,
        covers=True,
    )
    assert "expired 3 days ago" in tls_findings(expired, now)[0]


def test_a_certificate_that_does_not_verify_or_cover_the_host_is_two_findings() -> None:
    now = datetime(2026, 10, 28, tzinfo=UTC)
    info = TlsInfo(
        subject="other.test",
        issuer="self",
        not_after=now + timedelta(days=365),
        names=("other.test",),
        verified=False,
        covers=False,
    )
    findings = tls_findings(info, now)
    assert any("does not verify" in finding for finding in findings)
    assert any("does not name the host" in finding for finding in findings)


async def test_a_listening_port_answers_and_a_closed_one_does_not() -> None:
    server = await asyncio.start_server(lambda _reader, _writer: None, "127.0.0.1", 0)
    sockets = server.sockets or ()
    port = int(sockets[0].getsockname()[1])
    try:
        answers = await open_ports(("127.0.0.1",), (port, 1))
    finally:
        server.close()
        await server.wait_closed()
    assert answers == ((port, ""),)


def _context(
    owned_hosts: tuple[str, ...] = (), *, blocked: tuple[str, ...] = (), allow_private: bool = True
) -> ToolContext:
    """A context with a real configuration, narrowed to what the recon tool reads before it refuses a scan."""
    base = load_default_config()
    config = base.model_copy(
        update={
            "recon": base.recon.model_copy(update={"owned_hosts": owned_hosts}),
            "web": base.web.model_copy(update={"allow_private_hosts": allow_private}),
            "browser": base.browser.model_copy(update={"blocked_hosts": blocked}),
        }
    )
    web = WebClient(config.web, config.browser.allowed_hosts, config.browser.blocked_hosts)
    return cast(ToolContext, SimpleNamespace(runtime=SimpleNamespace(config=config, web=web), output_chars=8000))


async def test_a_port_scan_of_a_host_that_is_not_the_owners_is_refused() -> None:
    result = await _run(ReconArgs(target="example.com", depth="ports"), _context())
    assert result.is_error
    assert "[recon] owned_hosts" in result.text


async def test_a_host_on_the_blocked_list_is_refused_before_it_is_read() -> None:
    result = await _run(ReconArgs(target="blocked.test"), _context(blocked=("blocked.test",)))
    assert result.is_error
    assert "blocked list" in result.text


async def test_a_name_with_one_private_address_is_not_connected_to(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(self: object, host: str, port: int, **kwargs: object) -> list[tuple[object, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port)),
        ]

    monkeypatch.setattr(type(asyncio.get_running_loop()), "getaddrinfo", fake)
    with pytest.raises(WebError):
        await checked_addresses("dual.test", 443, allow_private_hosts=False)
    found = await checked_addresses("dual.test", 443, allow_private_hosts=True)
    assert set(found) == {"93.184.216.34", "10.0.0.5"}


async def test_a_port_scan_of_a_name_that_resolves_to_a_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake(self: object, host: str, port: int, **kwargs: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(type(asyncio.get_running_loop()), "getaddrinfo", fake)
    result = await _run(
        ReconArgs(target="dev.example.com", depth="ports"),
        _context(("dev.example.com",), allow_private=False),
    )
    assert result.is_error
    assert "private or local" in result.text


def test_an_owned_host_may_be_an_ipv6_address_but_not_a_port() -> None:
    assert ReconConfig(owned_hosts=("2001:db8::1",)).owned_hosts == ("2001:db8::1",)
    with pytest.raises(ValidationError):
        ReconConfig(owned_hosts=("example.com:443",))


def test_an_address_in_the_certificate_covers_the_host() -> None:
    assert _covers("2001:0DB8::1", "2001:db8::1")
    assert _covers("93.184.216.34", "93.184.216.34")
    assert not _covers("93.184.216.34", "93.184.216.35")


async def test_a_site_with_an_unknown_charset_still_gives_its_headers() -> None:
    with _site(_UnknownCharsetHandler) as port:
        result = await _run(ReconArgs(target=f"http://127.0.0.1:{port}"), _context())
    assert not result.is_error
    assert "Content-Security-Policy" in result.text


async def test_the_tls_handshake_goes_to_the_address_the_check_read(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str | None]] = []

    async def fake_open(
        host: str, port: int, *, ssl: object = None, server_hostname: str | None = None
    ) -> tuple[object, object]:
        seen.append((host, server_hostname))
        raise OSError("the test refuses the connection")

    monkeypatch.setattr("dotagent.tools.recon.asyncio.open_connection", fake_open)
    result = await _tls(("93.184.216.34",), "example.com", 443)
    assert result.info is None
    assert seen[0] == ("93.184.216.34", "example.com")


async def test_the_audit_reads_a_site_over_a_real_request() -> None:
    config = load_default_config().web.model_copy(update={"allow_private_hosts": True})
    client = WebClient(config, (), ())
    try:
        with _site() as port:
            answer = await _http(client, Target(url=f"http://127.0.0.1:{port}/", host="127.0.0.1", port=port))
    finally:
        await client.aclose()
    assert not isinstance(answer, str)
    assert answer.document.status == 200
    headers = {name.lower(): value for name, value in answer.document.headers}
    assert headers["server"] == "nginx/1.18.0"
    assert "WordPress" in technology(headers, answer.document.body.decode())
