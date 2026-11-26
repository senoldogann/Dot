"""Settings › System › Maintenance reads the provider programs on the Mac (item 1794), through the API of a real
in-process server: a Claude Code whose version Dot was measured on reads ``supported``, one older reads ``graceful``,
an OpenCode reads its version with ``unknown`` (Dot keeps no measure of it), and a program that is not there reads
``missing``. The programs are stand-ins in a folder of the test's own (``DOT_CLAUDE``, ``DOT_OPENCODE``); nothing
outside this Mac is reached and the owner's own programs are never started."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "provider-programs-token"


def _program(folder: Path, name: str, says: str) -> Path:
    """A stand-in that answers ``--version`` with ``says``."""
    path: Path = folder / name
    path.write_text(f"#!/bin/sh\necho '{says}'\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def setup(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[tuple[httpx.AsyncClient, pytest.MonkeyPatch, Path]]:
    home: Path = tmp_path_factory.mktemp("home")
    programs: Path = tmp_path_factory.mktemp("programs")
    (home / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("DOT_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    manager: RunManager = RunManager(runtime)
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=8765,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    transport: httpx.ASGITransport = httpx.ASGITransport(app=create_app(context))
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8765", headers={"Authorization": f"Bearer {TOKEN}"}
        ) as client:
            yield client, patch, programs
    finally:
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


def _by_name(response: httpx.Response) -> dict[str, dict[str, str | None]]:
    return {program["name"]: program for program in response.raise_for_status().json()["programs"]}


async def test_each_program_says_its_version_and_how_dot_fares_on_it(
    setup: tuple[httpx.AsyncClient, pytest.MonkeyPatch, Path],
) -> None:
    client, patch, programs = setup
    patch.setenv("DOT_CLAUDE", str(_program(programs, "claude", "2.1.331 (Claude Code)")))
    patch.setenv("DOT_OPENCODE", str(_program(programs, "opencode", "1.19.5")))
    found: dict[str, dict[str, str | None]] = _by_name(await client.get("/api/diagnostics/programs"))
    assert found["Claude Code"]["version"] == "2.1.331"
    assert found["Claude Code"]["support"] == "supported"
    assert found["OpenCode"]["version"] == "1.19.5"
    assert found["OpenCode"]["support"] == "unknown"

    patch.setenv("DOT_CLAUDE", str(_program(programs, "claude-old", "2.1.100 (Claude Code)")))
    assert _by_name(await client.get("/api/diagnostics/programs"))["Claude Code"]["support"] == "graceful"


async def test_a_program_that_is_not_there_reads_missing(
    setup: tuple[httpx.AsyncClient, pytest.MonkeyPatch, Path],
) -> None:
    client, patch, programs = setup
    patch.setenv("DOT_CLAUDE", str(programs / "no-such-claude"))
    patch.setenv("DOT_OPENCODE", str(programs / "no-such-opencode"))
    found: dict[str, dict[str, str | None]] = _by_name(await client.get("/api/diagnostics/programs"))
    assert found["Claude Code"]["support"] == "missing"
    assert found["Claude Code"]["path"] is None
    assert found["OpenCode"]["support"] == "missing"
