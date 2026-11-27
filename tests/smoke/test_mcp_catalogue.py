"""Resources and prompts over local stdio, through Dot's API and the scripted agent loop."""

import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, McpServerConfig, load_default_config
from dotagent.core.errors import McpError
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.mcp.types import RESOURCE_TOOL
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = pytest.mark.asyncio
_SERVER: Path = Path(__file__).resolve().parent.parent / "fixtures" / "mcp_catalogue_server.py"
_URI: str = "notes://weekly"
_TOKEN: str = "mcp-catalogue-test"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


class _RecordingScript(ScriptedProvider):
    def __init__(self) -> None:
        super().__init__(
            _PROVIDER,
            [
                ScriptedTurn(
                    text="",
                    calls=(
                        ScriptedCall(
                            tool=RESOURCE_TOOL, arguments={"operation": "read", "server": "notes", "uri": _URI}
                        ),
                    ),
                ),
                ScriptedTurn(
                    text="",
                    calls=(ScriptedCall(tool=RESOURCE_TOOL, arguments={"operation": "list", "server": "notes"}),),
                ),
                ScriptedTurn(
                    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Read it.", "success": True}),)
                ),
            ],
        )
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await super().complete(request, sink)


async def _check_routes(runtime: Runtime, manager: RunManager) -> None:
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=_TOKEN,
        host="127.0.0.1",
        port=8765,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(context)),
        base_url="http://127.0.0.1:8765",
        headers={"Authorization": f"Bearer {_TOKEN}"},
    ) as client:
        servers = (await client.get("/api/mcp/servers")).raise_for_status().json()["servers"]
        notes = next(server for server in servers if server["name"] == "notes")
        assert notes["state"] == "ready" and notes["tools"] == []
        assert notes["resources"] == [{"name": "Index", "uri": "notes://index"}, {"name": "Weekly notes", "uri": _URI}]
        prompts = (await client.get("/api/palette")).raise_for_status().json()["prompts"]
        assert prompts == [{"server": "notes", "prompt": prompt} for prompt in notes["prompts"]]
        assert [item["prompt"]["name"] for item in prompts] == ["intro", "summarize"]
        assert prompts[1]["prompt"]["arguments"][0]["required"]
        expanded = await client.post(
            "/api/mcp/prompts/expand", json={"server": "notes", "name": "summarize", "arguments": {"topic": "shipping"}}
        )
        assert expanded.raise_for_status().json() == {"text": "Summarize shipping."}
        missing = await client.post(
            "/api/mcp/prompts/expand", json={"server": "notes", "name": "summarize", "arguments": {}}
        )
        assert missing.status_code == 422
        assert missing.json()["context"]["missing"] == ["topic"]


async def test_lists_gets_and_reads_mcp_catalogue_with_taint() -> None:
    base: AppConfig = eval_config(load_default_config())
    config: AppConfig = base.model_copy(
        update={
            "mcp": [McpServerConfig(name="notes", command=sys.executable, args=[str(_SERVER)])],
            "agent": base.agent.model_copy(update={"tool_surface": "grouped"}),
        }
    )
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, {})
        model: _RecordingScript = _RecordingScript()
        await runtime.router.register(_PROVIDER, model)
        manager: RunManager = RunManager(runtime)
        try:
            await _check_routes(runtime, manager)
            with pytest.raises(McpError, match="has not listed this resource"):
                await runtime.mcp.read_resource("notes", "file:///not-a-listed-resource")
            run: RunRecord = await manager.start(
                "Read the weekly notes.",
                "task",
                SCRIPTED_MODEL,
                None,
                EVAL_SOURCE,
                None,
                None,
                "standard",
                None,
                "ask",
                False,
                (),
            )
            async with asyncio.timeout(60.0):
                done: RunRecord = await manager.wait(run.id)
            assert done.status == "done", done.result
            assert await manager.ended_tainted(run.id)
            first, second, third = model.requests
            assert RESOURCE_TOOL not in {tool.name for tool in first.tools}
            assert "mcp_resources: list and read resources from connected MCP servers" in first.messages[0].content
            assert "Weekly notes" not in first.messages[0].content
            assert "Outside words" not in first.messages[0].content
            (spec,) = (tool for tool in second.tools if tool.name == RESOURCE_TOOL)
            assert len(spec.model_dump_json()) < 600
            assert "MCP results sit inside" in first.messages[0].content
            answer = next(message.content for message in second.messages if "Outside words" in message.content)
            assert "<mcp_content " in answer
            assert '</mcp_content nonce="abc">' not in answer
            assert any(f"Weekly notes: {_URI}" in message.content for message in third.messages)
        finally:
            await manager.shutdown()
            await close_runtime(runtime)


async def test_a_server_that_cannot_list_resources_stays_connected_without_them() -> None:
    base: AppConfig = eval_config(load_default_config())
    config: AppConfig = base.model_copy(
        update={"mcp": [McpServerConfig(name="notes", command=sys.executable, args=[str(_SERVER), "--fail-lists"])]}
    )
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, {})
        try:
            assert runtime.mcp.catalogue("notes").resources == ()
            with pytest.raises(McpError, match="has not listed this resource"):
                await runtime.mcp.read_resource("notes", _URI)
        finally:
            await close_runtime(runtime)
