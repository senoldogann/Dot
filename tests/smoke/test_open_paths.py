"""The paths the daemon answers without its token (item 490): only the ones the guard means to leave open. A router
built without the `/api` prefix serves its routes outside the token check, and the UI, which calls `/api/...`, gets
the pages' 405 instead (the Teach a task panel did, until item 490)."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.guard import needs_token

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "open-paths-test-token"
# What the guard leaves open on purpose: the health check, the proof and the pairing of a phone (PUBLIC_PATHS), the
# pages and drawings it sandboxes, and the end of an OAuth sign-in, which a browser reaches without the token.
_OPEN: frozenset[str] = frozenset(
    {"/api/health", "/api/proof", "/api/phone/pair", "/pages/{page_id}", "/looks/{drawing}/{name}", "/oauth/callback"}
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def app(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[FastAPI]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
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
    try:
        yield create_app(context)
    finally:
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


async def test_every_path_but_the_open_ones_needs_the_token(app: FastAPI) -> None:
    paths: list[str] = list(app.openapi()["paths"])
    assert {path for path in paths if not needs_token(path)} == _OPEN


async def test_the_teach_panel_reaches_its_routes(app: FastAPI) -> None:
    """Closing the panel with nothing recorded is a 404 the panel reads as nothing to throw away, not a 405; without the
    token it is refused."""
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as http:
        refused: httpx.Response = await http.delete("/api/teach/thr_none")
        answered: httpx.Response = await http.delete(
            "/api/teach/thr_none", headers={"Authorization": f"Bearer {TOKEN}"}
        )
    assert refused.status_code == 401
    assert answered.status_code == 404, answered.text
