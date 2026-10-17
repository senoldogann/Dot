"""The rules that keep the server local: addresses, hosts, origins, tokens, the token file and the proof of the
daemon."""

import os
import stat
from pathlib import Path

import pytest

from dotagent.core.errors import ConfigError
from dotagent.server.guard import (
    Refusal,
    bearer_token,
    check_request,
    daemon_proof,
    navigation_is_foreign,
    read_or_create_token,
    read_token,
    require_loopback,
)

PORT: int = 8765
TOKEN: str = "MARKER-0123456789abcdef"
LOCAL_HOST: str = f"127.0.0.1:{PORT}"
LOCAL_ORIGIN: str = f"http://127.0.0.1:{PORT}"
GOOD_AUTH: str = f"Bearer {TOKEN}"
NONCE: str = "0123456789abcdef" * 4
# daemon_proof(TOKEN, NONCE); the desktop shell's tests (desktop/src-tauri/src/lib.rs) hold the same pair.
PROOF: str = "4aea8baff569980b8b9dceb16e4d02f89d6563a252c47dcf94912b82ae440020"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_loopback_addresses_are_accepted(host: str) -> None:
    require_loopback(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "::", "example.org", ""])
def test_addresses_other_machines_can_reach_are_refused(host: str) -> None:
    with pytest.raises(ConfigError) as refused:
        require_loopback(host)
    assert refused.value.context["host"] == host


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (f"Bearer {TOKEN}", TOKEN),
        (f"bearer   {TOKEN}  ", TOKEN),
        (f"Basic {TOKEN}", None),
        ("Bearer", None),
        ("Bearer   ", None),
        (TOKEN, None),
        (None, None),
    ],
)
def test_only_a_bearer_header_carries_the_token(header: str | None, expected: str | None) -> None:
    assert bearer_token(header) == expected


@pytest.mark.parametrize(
    ("path", "host", "origin", "authorization", "refused_with"),
    [
        ("/api/status", LOCAL_HOST, None, GOOD_AUTH, None),
        ("/api/status", f"localhost:{PORT}", LOCAL_ORIGIN, GOOD_AUTH, None),
        ("/api/status", f"[::1]:{PORT}", None, GOOD_AUTH, None),
        ("/api/health", LOCAL_HOST, None, None, None),
        ("/api/proof", LOCAL_HOST, None, None, None),
        ("/", LOCAL_HOST, None, None, None),
        ("/app.js", LOCAL_HOST, LOCAL_ORIGIN, None, None),
        ("/api/status", LOCAL_HOST, None, None, 401),
        ("/api/status", LOCAL_HOST, None, "Bearer wrong", 401),
        ("/api/status", LOCAL_HOST, None, f"Basic {TOKEN}", 401),
        ("/api/health/", LOCAL_HOST, None, None, 401),
        ("/api/proof/", LOCAL_HOST, None, None, 401),
        ("/api", LOCAL_HOST, None, None, 401),
        ("/api/status", "attacker.example:8765", None, GOOD_AUTH, 421),
        ("/api/status", f"127.0.0.1:{PORT + 1}", None, GOOD_AUTH, 421),
        ("/", None, None, None, 421),
        ("/api/status", LOCAL_HOST, "https://attacker.example", GOOD_AUTH, 403),
        ("/api/status", LOCAL_HOST, f"http://127.0.0.1:{PORT + 1}", GOOD_AUTH, 403),
        ("/", LOCAL_HOST, "null", None, 403),
        ("/api/health", "attacker.example", None, None, 421),
    ],
)
def test_a_request_is_served_only_from_this_mac_to_pages_of_this_server_with_the_token(
    path: str, host: str | None, origin: str | None, authorization: str | None, refused_with: int | None
) -> None:
    refusal: Refusal | None = check_request(path, host, origin, None, None, authorization, TOKEN, PORT, frozenset())
    assert (None if refusal is None else refusal.status) == refused_with


@pytest.mark.parametrize(
    ("fetch_site", "fetch_mode", "refused"),
    [
        ("cross-site", "navigate", True),
        ("Cross-Site", "Navigate", True),
        ("cross-site", "cors", False),
        ("cross-site", "no-cors", False),
        ("same-origin", "navigate", False),
        ("same-site", "navigate", False),
        ("none", "navigate", False),
        (None, "navigate", False),
        ("cross-site", None, False),
        (None, None, False),
    ],
)
def test_a_navigation_a_page_of_another_site_started_is_refused(
    fetch_site: str | None, fetch_mode: str | None, refused: bool
) -> None:
    """A page of another site that sends a browser to the UI (a link, a script, a redirect) is refused on its Fetch
    Metadata, on every path; a request without any (a program, an older browser) is not. The address bar and the
    desktop shell send ``none``, the UI's own links ``same-origin``, and cross-site subresource requests are the
    Origin check's."""
    assert navigation_is_foreign(fetch_site, fetch_mode) is refused
    for path in ("/", "/index.html", "/api/status"):
        refusal: Refusal | None = check_request(
            path, LOCAL_HOST, None, fetch_site, fetch_mode, GOOD_AUTH, TOKEN, PORT, frozenset()
        )
        assert (refusal is not None and refusal.status == 403) is refused, (path, refusal)


@pytest.mark.parametrize("path", ["/oauth/callback", "/pages/abc123", f"/looks/{'a' * 32}/Attachment7.html"])
def test_what_other_sites_send_a_browser_to_by_design_is_served_to_a_foreign_navigation(path: str) -> None:
    """A sign-in comes back from the authorization server's site (item 279), and a page a run showed or a drawing of an
    Office file (item 268) opens with an opaque origin wherever it is opened from, which makes its own frames foreign
    navigations: none is the UI, so none is refused on Fetch Metadata."""
    assert check_request(path, LOCAL_HOST, None, "cross-site", "navigate", None, TOKEN, PORT, frozenset()) is None


def test_the_proof_of_the_daemon_is_an_hmac_of_the_nonce_keyed_with_the_token() -> None:
    assert daemon_proof(TOKEN, NONCE) == PROOF
    assert daemon_proof("another token", NONCE) != PROOF
    assert daemon_proof(TOKEN, "f" * 64) != PROOF


REACH: frozenset[str] = frozenset({"mac.tail1234.ts.net"})


@pytest.mark.parametrize(
    ("host", "origin", "refused_with"),
    [
        ("mac.tail1234.ts.net", None, None),
        ("MAC.tail1234.ts.net", "https://mac.tail1234.ts.net", None),
        ("mac.tail1234.ts.net", "http://mac.tail1234.ts.net", 403),
        ("other.tail1234.ts.net", None, 421),
        (LOCAL_HOST, "https://mac.tail1234.ts.net", None),
    ],
)
def test_a_name_of_reach_is_served_like_the_loopback_address_over_https(
    host: str, origin: str | None, refused_with: int | None
) -> None:
    """``[server] reach``: the names a reverse proxy on the owner's network presents the daemon as (item 52)."""
    refusal: Refusal | None = check_request("/api/status", host, origin, None, None, GOOD_AUTH, TOKEN, PORT, REACH)
    assert (None if refusal is None else refusal.status) == refused_with


def test_the_token_file_is_created_private_and_reused(tmp_path: Path) -> None:
    path: Path = tmp_path / "api-token"
    first: str = read_or_create_token(path)
    assert len(first) >= 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_or_create_token(path) == first
    assert read_token(path) == first


def test_a_token_file_other_users_can_read_is_refused(tmp_path: Path) -> None:
    path: Path = tmp_path / "api-token"
    read_or_create_token(path)
    os.chmod(path, 0o640)
    with pytest.raises(ConfigError) as refused:
        read_or_create_token(path)
    assert refused.value.context["fix"] == f"chmod 600 {path}"


def test_an_empty_token_file_is_refused(tmp_path: Path) -> None:
    path: Path = tmp_path / "api-token"
    path.write_text("\n", encoding="utf-8")
    os.chmod(path, 0o600)
    with pytest.raises(ConfigError, match="empty"):
        read_token(path)
