"""Removing what other sources gave Dot (item 497), through the API of a real in-process server: a server of a plugin
is not removed on its own, since it goes with its plugin; Remove plugin stops the plugin's servers now, moves its
folder to the Trash (one made here) and leaves them out of the servers Dot lists; a plugin read from a folder of
``[plugins] dirs`` is refused, the owner's to take out of the config.

No network and no model service is used: the plugin's MCP server is the test fixture, started on this Mac.
"""

import asyncio
import json
import shutil
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import routes_mcp
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "remove-plugin-token"
FIXTURE: Path = Path(__file__).resolve().parent.parent / "fixtures" / "mcp_fixture_server.py"
_OWN: str = "acme-tools"
_OUTSIDE: str = "elsewhere-tools"


def _plugin(folder: Path, name: str) -> None:
    """A plugin in the Claude Code layout whose one MCP server is the fixture."""
    (folder / ".claude-plugin").mkdir(parents=True)
    (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    servers: JsonObject = {
        "mcpServers": {"fixture": {"command": sys.executable, "args": ["${CLAUDE_PLUGIN_ROOT}/server.py"]}}
    }
    (folder / ".mcp.json").write_text(json.dumps(servers), encoding="utf-8")
    shutil.copy(FIXTURE, folder / "server.py")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def setup(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[tuple[httpx.AsyncClient, Path, Path]]:
    """A runtime whose home holds a plugin of Dot's own folder, and whose config reads another from a folder of its
    own; the Trash is a folder made here. Yields the client, Dot's plugins folder and the Trash."""
    home: Path = tmp_path_factory.mktemp("home")
    outside: Path = tmp_path_factory.mktemp("outside")
    trash: Path = tmp_path_factory.mktemp("trash")
    _plugin(home / "plugins" / _OWN, _OWN)
    _plugin(outside / _OUTSIDE, _OUTSIDE)
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[plugins]\ndirs = ["{outside}"]\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("DOT_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
    patch.setattr(routes_mcp, "trash_folder", lambda: trash)
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
            yield client, home / "plugins", trash
    finally:
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


def _servers(listing: httpx.Response) -> dict[str, tuple[str, str | None, str]]:
    return {
        server["name"]: (server["origin"], server["plugin"], server["state"])
        for server in listing.raise_for_status().json()["servers"]
    }


async def test_remove_plugin_stops_its_servers_and_moves_its_folder_to_the_trash(
    setup: tuple[httpx.AsyncClient, Path, Path],
) -> None:
    client, plugins, trash = setup
    before: dict[str, tuple[str, str | None, str]] = _servers(await client.get("/api/mcp/servers"))
    own: str = f"{_OWN}-fixture"
    assert before[own] == ("plugin", _OWN, "ready"), before
    # A plugin's server goes with its plugin.
    alone: httpx.Response = await client.delete(f"/api/mcp/servers/{own}")
    assert alone.status_code == 409 and "remove the plugin" in alone.text, alone.text
    # A plugin of [plugins] dirs is the owner's to take out of the config.
    outside: httpx.Response = await client.delete(f"/api/installed-plugins/{_OUTSIDE}")
    assert outside.status_code == 409 and "[plugins] dirs" in outside.text, outside.text
    removed: httpx.Response = await client.delete(f"/api/installed-plugins/{_OWN}")
    after: dict[str, tuple[str, str | None, str]] = _servers(removed)
    assert own not in after and f"{_OUTSIDE}-fixture" in after, after
    assert not (plugins / _OWN).exists()
    assert (trash / _OWN / ".claude-plugin" / "plugin.json").is_file()
    assert (await client.delete(f"/api/installed-plugins/{_OWN}")).status_code == 404
