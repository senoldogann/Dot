"""The desktop shell's settings (``[desktop_app]``) through the API of a real in-process server: they start as the
defaults say (Dot.app opens at login, its words in English), a change is written into the config file, which the shell
reads, and read back as the file holds it; a language the shell does not speak is refused and nothing is written.

No network and no model service is used."""

import asyncio
import time
import tomllib
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

TOKEN: str = "desktop-app-token"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def setup(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[tuple[httpx.AsyncClient, Path]]:
    """A runtime in a home of its own; yields the client and the home's config file."""
    home: Path = tmp_path_factory.mktemp("home")
    config: Path = home / "config.toml"
    await asyncio.to_thread(config.write_text, "[browser]\nheadless = true\n", encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("DOT_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
    runtime: Runtime = await build_runtime(load_config(config))
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
            yield client, config
    finally:
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


def _desktop_app(config: Path) -> dict[str, object]:
    table: object = tomllib.loads(config.read_text(encoding="utf-8")).get("desktop_app", {})
    assert isinstance(table, dict)
    return {str(key): value for key, value in table.items()}


async def test_the_desktop_apps_settings_are_written_where_the_shell_reads_them(
    setup: tuple[httpx.AsyncClient, Path],
) -> None:
    client, config = setup
    started: httpx.Response = await client.get("/api/desktop-app")
    assert started.raise_for_status().json() == {"start_at_login": True, "language": "en"}

    off: httpx.Response = await client.put("/api/desktop-app", json={"start_at_login": False})
    assert off.raise_for_status().json() == {"start_at_login": False, "language": "en"}
    assert _desktop_app(config) == {"start_at_login": False}

    turkish: httpx.Response = await client.put("/api/desktop-app", json={"language": "tr"})
    assert turkish.raise_for_status().json() == {"start_at_login": False, "language": "tr"}
    assert _desktop_app(config) == {"start_at_login": False, "language": "tr"}
    assert (await client.get("/api/desktop-app")).json() == {"start_at_login": False, "language": "tr"}
    # The file stays one the daemon starts with.
    assert load_config(config).desktop_app.language == "tr"

    refused: httpx.Response = await client.put("/api/desktop-app", json={"language": "de"})
    assert refused.status_code == 422, refused.text
    assert _desktop_app(config) == {"start_at_login": False, "language": "tr"}
    # A hand edit of the file, which the shell reads, is what the page is told.
    text: str = await asyncio.to_thread(config.read_text, encoding="utf-8")
    await asyncio.to_thread(
        config.write_text, text.replace("start_at_login = false", "start_at_login = true"), encoding="utf-8"
    )
    assert (await client.get("/api/desktop-app")).json() == {"start_at_login": True, "language": "tr"}
