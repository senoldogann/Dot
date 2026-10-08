"""MCP servers reached at a URL: the fixture server is served in this process over streamable HTTP and over SSE, behind
a check of the Authorization header, and connected to the way a user configures it. A wrong token and a closed port
are reported as such. Nothing is mocked: a real HTTP server on a free port, the real client library."""

import asyncio
import importlib.util
import os
import socket
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
import pytest_asyncio
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from dotagent.config import McpServerConfig
from dotagent.mcp.hub import McpHub, ServerReport, inspect_servers, open_hub
from dotagent.mcp.types import McpTool

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.usefixtures("environment")]

FIXTURE: Path = Path(__file__).resolve().parent.parent / "fixtures" / "mcp_fixture_server.py"
TOKEN: str = "http-fixture-token"
TOKEN_VARIABLE: str = "DOT_TEST_HTTP_TOKEN"
START_TIMEOUT_S: float = 15.0
REPORTED_WITHIN_S: float = 10.0


def _fixture_server() -> MCPServer:
    spec = importlib.util.spec_from_file_location("mcp_fixture_server", FIXTURE)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server: MCPServer = module.server
    return server


class _BearerGuard:
    """An ASGI wrapper that lets a request through only with the expected Authorization header; 401 otherwise."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and Headers(scope=scope).get("authorization") != self._expected:
            await Response("who are you?", status_code=401)(scope, receive, send)
            return
        await self._app(scope, receive, send)


@dataclass(frozen=True)
class _Served:
    port: int
    server: uvicorn.Server
    task: asyncio.Task[None]


async def _serve(app: ASGIApp) -> _Served:
    server: uvicorn.Server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    task: asyncio.Task[None] = asyncio.create_task(server.serve())
    deadline: float = time.monotonic() + START_TIMEOUT_S
    while not server.started:
        if task.done():
            task.result()
            raise AssertionError("the HTTP server stopped before it started")
        assert time.monotonic() < deadline, "the HTTP server did not start in time"
        await asyncio.sleep(0.05)
    port: int = server.servers[0].sockets[0].getsockname()[1]
    return _Served(port=port, server=server, task=task)


async def _stop(served: _Served) -> None:
    served.server.should_exit = True
    await served.task


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


@pytest.fixture(scope="module")
def environment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    home: Path = tmp_path_factory.mktemp("home")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_LOG_DIR", str(home / "logs"))
    patch.setenv(TOKEN_VARIABLE, TOKEN)
    yield
    patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def http_port() -> AsyncIterator[int]:
    served: _Served = await _serve(_BearerGuard(_fixture_server().streamable_http_app(), TOKEN))
    try:
        yield served.port
    finally:
        await _stop(served)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def sse_port() -> AsyncIterator[int]:
    served: _Served = await _serve(_BearerGuard(_fixture_server().sse_app(), TOKEN))
    try:
        yield served.port
    finally:
        await _stop(served)


def _login_env() -> dict[str, str]:
    return dict(os.environ)


def _http(name: str, port: int, header: str) -> McpServerConfig:
    return McpServerConfig(
        name=name, url=f"http://127.0.0.1:{port}/mcp", headers={"Authorization": header}, call_timeout_s=5.0
    )


def _sse(name: str, port: int, header: str) -> McpServerConfig:
    return McpServerConfig(
        name=name,
        url=f"http://127.0.0.1:{port}/sse",
        transport="sse",
        headers={"Authorization": header},
        call_timeout_s=5.0,
    )


async def _add_through(hub: McpHub, server: str) -> str:
    names: set[str] = {tool.exposed_name for tool in hub.tools}
    assert f"mcp__{server}__add" in names, names
    add: McpTool = next(tool for tool in hub.tools if tool.server == server and tool.remote_name == "add")
    result: CallToolResult = await hub.call(add, {"a": 2, "b": 3})
    assert not result.is_error, result
    (block,) = result.content
    assert isinstance(block, TextContent)
    return block.text


async def test_a_server_at_a_url_is_reached_over_streamable_http_with_its_headers(http_port: int) -> None:
    hub: McpHub = await open_hub([_http("docs", http_port, f"Bearer ${{{TOKEN_VARIABLE}}}")], _login_env())
    try:
        assert await _add_through(hub, "docs") == "5"
    finally:
        await hub.close()


async def test_a_server_at_a_url_is_reached_over_sse_when_asked(sse_port: int) -> None:
    hub: McpHub = await open_hub([_sse("legacy", sse_port, f"Bearer ${{{TOKEN_VARIABLE}}}")], _login_env())
    try:
        assert await _add_through(hub, "legacy") == "5"
    finally:
        await hub.close()


async def test_a_wrong_token_is_reported_as_a_refused_connection_without_the_headers(
    http_port: int, sse_port: int
) -> None:
    started: float = time.monotonic()
    reports: tuple[ServerReport, ...] = await inspect_servers(
        [_http("docs", http_port, "Bearer wrong"), _sse("legacy", sse_port, "Bearer wrong")], _login_env()
    )
    assert time.monotonic() - started < REPORTED_WITHIN_S
    for report in reports:
        assert report.error is not None and "refused the connection" in report.error, report
        assert "check the url, the headers" in report.error
        assert report.detail["url"] == f"http://127.0.0.1:{http_port if report.server == 'docs' else sse_port}/" + (
            "mcp" if report.server == "docs" else "sse"
        )
        assert "headers" not in report.detail
        assert "wrong" not in str(report.detail)


async def test_a_closed_port_is_reported_as_unreachable_quickly() -> None:
    nobody: McpServerConfig = McpServerConfig(name="nobody", url=f"http://127.0.0.1:{_closed_port()}/mcp")
    started: float = time.monotonic()
    (report,) = await inspect_servers([nobody], _login_env())
    assert time.monotonic() - started < REPORTED_WITHIN_S
    assert report.error is not None and "could not be reached" in report.error, report
    assert report.detail["transport"] == "http"
    assert "stderr_tail" not in report.detail
