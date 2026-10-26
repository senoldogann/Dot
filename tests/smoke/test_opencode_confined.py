"""The server of OpenCode a run outside yolo works with runs in the sandbox of the run's commands (item 677): what it
starts reads none of Dot's sealed files, writes its own data folder and not its config, and cannot reach back to the
server's port, where the password its environment holds would answer the agent's own asks. The server is a stand-in
that answers OpenCode's health check; the sandbox is macOS's own."""

import asyncio
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from dotagent.config import load_default_config
from dotagent.core.paths import opencode_dir
from dotagent.opencode.server import OpenCodeServer
from dotagent.policy.sandbox import build_sandbox

pytestmark = pytest.mark.asyncio

STAND_IN: Path = Path(__file__).resolve().parents[1] / "fixtures" / "confined_opencode.py"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root: Path = Path(os.path.realpath(tmp_path))
    monkeypatch.setenv("DOT_HOME", str(root / "dot"))
    monkeypatch.setenv("DOT_LOG_DIR", str(root / "logs"))
    monkeypatch.setenv("DOT_OPENCODE", str(STAND_IN))
    (root / "logs").mkdir()
    (root / "dot" / "workspace").mkdir(parents=True)
    yield root


async def test_a_confined_server_and_what_it_starts_keep_to_the_sandbox_of_a_run(home: Path) -> None:
    token: Path = home / "dot" / "api-token"
    await asyncio.to_thread(token.write_text, "secret", encoding="utf-8")
    sandbox: tuple[str, ...] = build_sandbox(
        (token,),
        (token,),
        (opencode_dir(),),
        home / "agents",
        (home / "dot", home / "logs"),
        (home / "dot" / "workspace",),
    ).commands
    base: OpenCodeServer = OpenCodeServer(
        load_default_config().providers.opencode, {"PATH": os.path.dirname(sys.executable) + ":/usr/bin:/bin"}
    )
    server: OpenCodeServer = base.confined(sandbox)
    try:
        client: httpx.AsyncClient = await server._client()
        answer: dict[str, bool] = json.loads((await client.get("/probe")).text)
    finally:
        await base.aclose()
    assert answer == {
        "read_sealed": False,
        "write_data": True,
        "write_config": False,
        "reach_itself": False,
        "reach_itself_mapped": False,
    }
    written: Path = opencode_dir() / "sandboxed"
    assert await asyncio.to_thread((written / "data" / "note").exists)
    assert not await asyncio.to_thread((written / "config" / "opencode" / "note").exists)
