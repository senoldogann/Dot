"""Dot's tool bridge for OpenCode's agent (item 284), over real HTTP with the MCP library's own client, as OpenCode's
agent reaches it: a door's requests list its tools and run them through its runner, the tool's words coming back; a
request with another secret, or with the door's once it closed, reaches nothing; and a call that outlasts the wait for
an answer tells the client it is still at work.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, ListToolsResult, TextContent

from dotagent.llm.types import ToolCall, ToolSpec
from dotagent.opencode import bridge as bridge_module
from dotagent.opencode.bridge import Door, ToolBridge, new_door
from dotagent.tools.base import ToolResult, ok

pytestmark = pytest.mark.asyncio

_SPEC: ToolSpec = ToolSpec(
    name="browser_open",
    description="Open a page in Dot's browser.",
    parameters={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
)
_INITIALIZE: dict[str, object] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "probe", "version": "1"}},
}
_ACCEPT: dict[str, str] = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@asynccontextmanager
async def _client(url: str, secret: str) -> AsyncIterator[Client]:
    async with (
        httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {secret}"}, timeout=httpx2.Timeout(10.0, read=30.0)
        ) as http,
        Client(streamable_http_client(url, http_client=http), mode="auto") as client,
    ):
        yield client


async def _status(url: str, secret: str | None) -> int:
    """The status of a bare first request of an MCP client that carries ``secret``, or no secret at all."""
    headers: dict[str, str] = _ACCEPT if secret is None else {**_ACCEPT, "Authorization": f"Bearer {secret}"}
    async with httpx2.AsyncClient(timeout=10.0) as http:
        response: httpx2.Response = await http.post(url, json=_INITIALIZE, headers=headers)
    return response.status_code


async def test_a_doors_requests_run_its_tools_and_nothing_else_reaches_it() -> None:
    calls: list[ToolCall] = []

    async def run(call: ToolCall) -> ToolResult:
        calls.append(call)
        return ok(f"opened {call.arguments['url']}")

    bridge: ToolBridge = ToolBridge()
    door: Door = new_door("thread-a", (_SPEC,), run)
    other: Door = new_door("thread-b", (_SPEC,), run)
    try:
        async with bridge.opened(door) as url:
            async with _client(url, door.secret) as client:
                listed: ListToolsResult = await client.list_tools()
                result: CallToolResult = await client.call_tool("browser_open", {"url": "https://example.com"})
                unknown: CallToolResult = await client.call_tool("shell", {"command": "ls"})
            assert [tool.name for tool in listed.tools] == ["browser_open"]
            assert listed.tools[0].input_schema["required"] == ["url"]
            assert not result.is_error
            assert isinstance(result.content[0], TextContent) and result.content[0].text == "opened https://example.com"
            assert [(call.name, call.arguments) for call in calls] == [("browser_open", {"url": "https://example.com"})]
            # A tool the door does not offer runs nothing, and says which ones it does.
            assert unknown.is_error and len(calls) == 1
            assert isinstance(unknown.content[0], TextContent) and "browser_open" in unknown.content[0].text
            # The secret of a door that is not open, or none, reaches nothing.
            assert await _status(url, other.secret) == 404
            assert await _status(url, None) == 404
            assert await _status(url, door.secret) == 200
        assert await _status(url, door.secret) == 404
    finally:
        await bridge.aclose()


async def test_a_call_that_outlasts_a_ping_tells_the_client_it_is_still_at_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bridge_module, "_PING_S", 0.1)

    async def slow(call: ToolCall) -> ToolResult:
        await asyncio.sleep(0.6)
        return ok("done")

    heard: list[float] = []

    async def progress(value: float, total: float | None, message: str | None) -> None:
        heard.append(value)

    bridge: ToolBridge = ToolBridge()
    door: Door = new_door("thread-a", (_SPEC,), slow)
    try:
        async with bridge.opened(door) as url, _client(url, door.secret) as client:
            result: CallToolResult = await client.call_tool(
                "browser_open", {"url": "https://example.com"}, progress_callback=progress
            )
    finally:
        await bridge.aclose()
    assert not result.is_error
    assert len(heard) >= 3, heard
