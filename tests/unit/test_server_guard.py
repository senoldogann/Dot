"""The rules that keep the server local: addresses, hosts, origins, tokens and the token file."""

import os
import stat
from pathlib import Path

import pytest

from dotagent.core.errors import ConfigError
from dotagent.server.guard import (
    Refusal,
    bearer_token,
    check_request,
    read_or_create_token,
    read_token,
    require_loopback,
)

PORT: int = 8765
TOKEN: str = "MARKER-0123456789abcdef"
LOCAL_HOST: str = f"127.0.0.1:{PORT}"
LOCAL_ORIGIN: str = f"http://127.0.0.1:{PORT}"
GOOD_AUTH: str = f"Bearer {TOKEN}"


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
        ("/", LOCAL_HOST, None, None, None),
        ("/app.js", LOCAL_HOST, LOCAL_ORIGIN, None, None),
        ("/api/status", LOCAL_HOST, None, None, 401),
        ("/api/status", LOCAL_HOST, None, "Bearer wrong", 401),
        ("/api/status", LOCAL_HOST, None, f"Basic {TOKEN}", 401),
        ("/api/health/", LOCAL_HOST, None, None, 401),
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
    refusal: Refusal | None = check_request(path, host, origin, authorization, TOKEN, PORT)
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
