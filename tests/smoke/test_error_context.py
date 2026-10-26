"""The context of an error of Dot's is masked where the API answers with it and where it logs it (item 690): an
address with a key in its query, an argument that is a key and a field that names a secret are not told as they stand,
whichever route raised the error."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.errors import DotError, ToolExecutionError
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext

pytestmark = pytest.mark.asyncio(loop_scope="module")

_KEY: str = "k3Yx9-0123456789abcdef"


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
        token="error-context-test-token",
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


async def test_the_context_of_an_error_is_masked_in_the_answer(app: FastAPI) -> None:
    error: ToolExecutionError = ToolExecutionError(
        "the server did not answer",
        {
            "server": "docs",
            "url": f"https://mcp.example.test/mcp?api_key={_KEY}",
            "args": ["--api-key", _KEY, "--verbose"],
            "password": "plain-word",
            "causes": [f"GET https://mcp.example.test/mcp?api_key={_KEY} failed"],
        },
    )
    request: Request = Request({"type": "http", "method": "GET", "path": "/api/x", "headers": [], "query_string": b""})
    answer: Response = await app.exception_handlers[DotError](request, error)
    body: str = bytes(answer.body).decode("utf-8")
    assert _KEY not in body and "plain-word" not in body
    context: dict[str, object] = json.loads(body)["context"]
    assert context["server"] == "docs"
    assert context["url"] == "https://mcp.example.test/mcp?…"
    assert context["args"] == ["--api-key", "***", "--verbose"]
    assert context["password"] == "***"
