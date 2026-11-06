"""``dotx doctor`` names the tools the running daemon left out of its connected servers (item 1001): the checks start no
server, so the doctor asks the daemon, once it has proven that it holds the token, and prints a warn row per tool. With
no daemon running it tells none. No network and no model service is used."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from dotagent.agent.manager import RunManager
from dotagent.cli_daemon import daemon_left_out
from dotagent.cli_doctor import check_line
from dotagent.config import AppConfig, load_config
from dotagent.core.paths import ensure_layout, token_path
from dotagent.doctor import left_out_checks
from dotagent.mcp.types import LeftOutTool
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.guard import read_or_create_token

pytestmark = pytest.mark.asyncio

_ODD: LeftOutTool = LeftOutTool(server="notes", tool="odd_filter", reason="its input schema cannot be used")


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[AppConfig, Runtime]]:
    """A daemon on ``home`` with the installation's token, and the config that points at its port."""
    await asyncio.to_thread((home / "config.toml").write_text, "[browser]\nheadless = true\n", encoding="utf-8")
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    manager: RunManager = RunManager(runtime)
    token: str = read_or_create_token(token_path())
    try:
        stopping: asyncio.Event = asyncio.Event()
        listener = bind_listener("127.0.0.1", 0)
        port: int = listener.getsockname()[1]
        context: ServerContext = ServerContext(
            runtime=runtime,
            manager=manager,
            scheduler=None,
            token=token,
            host="127.0.0.1",
            port=port,
            started_at=time.time(),
            stopping=stopping,
        )
        server: DotServer = DotServer(serving_config(create_app(context)), stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        config: AppConfig = runtime.config.model_copy(
            update={"server": runtime.config.server.model_copy(update={"host": "127.0.0.1", "port": port})}
        )
        try:
            yield config, runtime
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def test_doctor_names_the_tools_the_running_daemon_left_out(own_home: Path) -> None:
    async with _served(own_home) as (config, runtime):
        assert await daemon_left_out(config) == []
        runtime.mcp.omitted = (_ODD,)
        found: list[LeftOutTool] | None = await daemon_left_out(config)
        assert found == [_ODD]
        line: str = check_line(left_out_checks(found)[0])
        assert line.startswith("warn") and "notes/odd_filter" in line and "left out odd_filter" in line
    # No daemon answers at the port any more: there is nothing to tell.
    assert await daemon_left_out(config) is None
