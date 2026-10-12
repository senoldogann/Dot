"""The CLI sends the token only to a daemon that proves it holds it (item 143): a program on the daemon's port that
answers the health check as the daemon does, but not the nonce of the proof, gets neither the token nor the address
that carries it."""

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from dotagent.cli_commands import ServicePermissionsCommand
from dotagent.cli_daemon import cmd_service_permissions, reachable_ui_url
from dotagent.config import AppConfig, load_config
from dotagent.core.errors import ServiceError

TOKEN: str = "MARKER-0123456789abcdef"
HEALTHY: str = '{"ok": true, "version": "0.1.0"}'
_HEADER_END: bytes = b"\r\n\r\n"

# How a program on the daemon's port answers a request for a path: the status and the body.
Answer = Callable[[str], tuple[int, str]]


def _stranger(path: str) -> tuple[int, str]:
    """Another program on the port: the daemon's health, a proof that is not the token's, and an empty object."""
    if path == "/api/health":
        return 200, HEALTHY
    if path.startswith("/api/proof?"):
        return 200, f'{{"proof": "{"00" * 32}"}}'
    return 200, "{}"


def _without_proof(path: str) -> tuple[int, str]:
    """A program that knows no proof, as the daemon of before it answers it: the token is asked for."""
    if path == "/api/health":
        return 200, HEALTHY
    return 401, '{"message": "a valid bearer token is required", "context": {}}'


async def _listen(answer: Answer, requests: list[str]) -> asyncio.Server:
    """A program on a free port of this Mac that answers each request with ``answer`` and keeps its head."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head: str = (await reader.readuntil(_HEADER_END)).decode("latin-1")
        requests.append(head)
        status, body = answer(head.split(" ", 2)[1])
        writer.write(
            f"HTTP/1.1 {status} Answer\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n{body}".encode()
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


def _home(home: Path, port: int, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    """A home whose daemon listens on ``port`` and whose token is ``TOKEN``."""
    (home / "config.toml").write_text(f"[server]\nport = {port}\n", encoding="utf-8")
    descriptor: int = os.open(home / "api-token", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(TOKEN)
    monkeypatch.setenv("DOT_HOME", str(home))
    return load_config(home / "config.toml")


def _port(server: asyncio.Server) -> int:
    port: int = server.sockets[0].getsockname()[1]
    return port


def _carries_the_token(head: str) -> bool:
    return TOKEN in head or "authorization:" in head.lower()


@pytest.mark.parametrize("answer", [_stranger, _without_proof])
async def test_the_signed_in_address_is_given_only_for_a_daemon_that_proves_it_holds_the_token(
    answer: Answer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[str] = []
    server: asyncio.Server = await _listen(answer, requests)
    try:
        config: AppConfig = _home(tmp_path, _port(server), monkeypatch)
        with pytest.raises(ServiceError) as refused:
            await reachable_ui_url(config)
    finally:
        server.close()
        await server.wait_closed()
    assert TOKEN not in str(refused.value.context)
    assert not any(_carries_the_token(head) for head in requests)


@pytest.mark.parametrize("answer", [_stranger, _without_proof])
async def test_service_permissions_sends_the_token_only_to_a_daemon_that_proves_it_holds_it(
    answer: Answer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[str] = []
    server: asyncio.Server = await _listen(answer, requests)
    try:
        _home(tmp_path, _port(server), monkeypatch)
        with pytest.raises(ServiceError):
            await cmd_service_permissions(ServicePermissionsCommand(name="service-permissions"))
    finally:
        server.close()
        await server.wait_closed()
    assert [head.split(" ", 2)[1].partition("?")[0] for head in requests] == ["/api/proof"]
    assert not any(_carries_the_token(head) for head in requests)
