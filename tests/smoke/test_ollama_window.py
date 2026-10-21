"""The window Dot asks an Ollama model of this Mac for (item 520): an Ollama server is served in this process, at the
two routes a chat needs (/api/show and /api/chat), and records what each chat request asked of it. Nothing is mocked
past the HTTP boundary: the real connector, its real requests."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from dotagent.agent.sizes import ContextSizes
from dotagent.config import OllamaConfig, load_default_config
from dotagent.core.types import JsonObject
from dotagent.llm.ollama import OllamaProvider
from dotagent.llm.types import ChatRequest, ModelInfo, StreamDelta, user_message

START_TIMEOUT_S: float = 15.0
_MODEL: str = "bonsai-2:9b"
# The window the model holds itself, as /api/show names it for a 9B model of the owner's.
_OWN_WINDOW: int = 262_144


@dataclass(frozen=True)
class _Served:
    port: int
    server: uvicorn.Server
    task: asyncio.Task[None]
    asked: list[JsonObject]


async def _serve() -> _Served:
    asked: list[JsonObject] = []

    async def show(request: Request) -> Response:
        return JSONResponse(
            {"capabilities": ["completion", "tools"], "model_info": {"qwen35.context_length": _OWN_WINDOW}}
        )

    async def chat(request: Request) -> Response:
        asked.append(json.loads(await request.body()))

        async def lines() -> AsyncIterator[bytes]:
            yield (
                json.dumps(
                    {"model": _MODEL, "message": {"role": "assistant", "content": "Done."}, "done": False}
                ).encode()
                + b"\n"
            )
            yield (
                json.dumps(
                    {"model": _MODEL, "done": True, "done_reason": "stop", "prompt_eval_count": 3, "eval_count": 1}
                ).encode()
                + b"\n"
            )

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    app: Starlette = Starlette(
        routes=[Route("/api/show", show, methods=["POST"]), Route("/api/chat", chat, methods=["POST"])]
    )
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
    return _Served(port=port, server=server, task=task, asked=asked)


async def _discard(delta: StreamDelta) -> None:
    return None


def _request() -> ChatRequest:
    return ChatRequest(
        model=_MODEL,
        messages=(user_message("Say done.", ()),),
        tools=(),
        temperature=None,
        max_output_tokens=None,
        effort="off",
        speed="standard",
        session_key="window-test",
        cache_mark=None,
    )


@pytest.mark.asyncio
async def test_a_model_given_a_context_size_is_asked_for_the_window_it_needs_and_planned_with_it() -> None:
    """Item 520: the owner's Ollama gave its models 128K, and Dot asked each for num_ctx 65,536, so a 9B model had a
    budget of 49,152, under what its prompt and tools take. A context size chosen for the model asks for the window it
    needs, up to the model's own; without one, num_ctx as before. What the connector says of the model is the window
    it asks for by default, with its own beside it, from which a run plans the same grown window."""
    served: _Served = await _serve()
    config: OllamaConfig = load_default_config().providers.ollama.model_copy(
        update={"base_url": f"http://127.0.0.1:{served.port}"}
    )
    sizes: ContextSizes = ContextSizes({})
    provider: OllamaProvider = OllamaProvider(config, None, sizes)
    try:
        info: ModelInfo = await provider.model_info(_MODEL)
        assert (info.context_length, info.own_context_length) == (config.num_ctx, _OWN_WINDOW)
        await provider.complete(_request(), _discard)
        sizes.choose(f"ollama:{_MODEL}", 98_304)
        await provider.complete(_request(), _discard)
        sizes.choose(f"ollama:{_MODEL}", 500_000)
        await provider.complete(_request(), _discard)
        assert [asked["options"]["num_ctx"] for asked in served.asked] == [config.num_ctx, 131_072, _OWN_WINDOW]
    finally:
        await provider.aclose()
        served.server.should_exit = True
        await served.task
