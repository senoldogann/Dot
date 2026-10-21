"""The window Dot asks an Ollama model of this Mac for (item 520): an Ollama server is served in this process, at the
two routes a run needs (/api/show and /api/chat), and records what each chat request asked of it; a run of the real
connector talks to it as to Ollama. Nothing is mocked past the HTTP boundary."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.types import JsonObject
from dotagent.evals.runner import EVAL_SOURCE, eval_config, eval_environment, scratch_home
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import RunRecord
from dotagent.tools.shell import read_login_env

START_TIMEOUT_S: float = 15.0
_RUN_WAIT_S: float = 60.0
_MODEL: str = "bonsai-2:9b"
_REF: str = f"ollama:{_MODEL}"
# The window the model holds itself, as /api/show names it for the owner's 9B model.
_OWN_WINDOW: int = 262_144


@dataclass(frozen=True)
class _Served:
    port: int
    server: uvicorn.Server
    task: asyncio.Task[None]
    asked: list[JsonObject]


async def _serve() -> _Served:
    """An Ollama of this Mac whose one model calls finish at once, recording every chat request it is sent."""
    asked: list[JsonObject] = []

    async def show(request: Request) -> Response:
        return JSONResponse(
            {"capabilities": ["completion", "tools"], "model_info": {"qwen35.context_length": _OWN_WINDOW}}
        )

    async def chat(request: Request) -> Response:
        asked.append(json.loads(await request.body()))
        finish: JsonObject = {"function": {"name": "finish", "arguments": {"answer": "Done.", "success": True}}}
        done: JsonObject = {
            "model": _MODEL,
            "message": {"role": "assistant", "content": "", "tool_calls": [finish]},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 3,
            "eval_count": 1,
        }

        async def lines() -> AsyncIterator[bytes]:
            yield json.dumps(done).encode() + b"\n"

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    routes: list[Route] = [Route("/api/show", show, methods=["POST"]), Route("/api/chat", chat, methods=["POST"])]
    server: uvicorn.Server = uvicorn.Server(
        uvicorn.Config(Starlette(routes=routes), host="127.0.0.1", port=0, log_level="warning")
    )
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


@pytest_asyncio.fixture(loop_scope="function")
async def ollama() -> AsyncIterator[_Served]:
    served: _Served = await _serve()
    try:
        yield served
    finally:
        served.server.should_exit = True
        await served.task


async def _run_once(ollama: _Served, sizes: dict[str, int]) -> list[int]:
    """The ``num_ctx`` of every chat request a run of the model sent, with ``sizes`` its home's context sizes."""
    login_env: dict[str, str] = await read_login_env(load_default_config().shell.login_env_timeout_s, ())
    base: AppConfig = eval_config(load_default_config())
    config: AppConfig = base.model_copy(
        update={
            "providers": base.providers.model_copy(
                update={
                    "ollama": base.providers.ollama.model_copy(update={"base_url": f"http://127.0.0.1:{ollama.port}"})
                }
            ),
            "models": base.models.model_copy(update={"context": sizes}),
        }
    )
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    "Say done.", "task", _REF, None, EVAL_SOURCE, None, None, "standard", None, "yolo", False, ()
                )
                async with asyncio.timeout(_RUN_WAIT_S):
                    ended: RunRecord = await manager.wait(run.id)
                assert ended.status == "done", ended
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)
    return [asked["options"]["num_ctx"] for asked in ollama.asked if asked["model"] == _MODEL]


@pytest.mark.asyncio(loop_scope="function")
async def test_a_run_of_a_model_given_a_context_size_asks_ollama_for_the_window_it_needs(ollama: _Served) -> None:
    """Item 520: the owner's Ollama gave its models 128K, and Dot asked each for num_ctx 65,536, so a 9B model had a
    budget of 49,152, under what its prompt and tools take, and no run started. A run of a model given 96K asks Ollama
    for the window that needs, 131,072, at every step; a run of one given none asks for num_ctx, as before."""
    assert set(await _run_once(ollama, {_REF: 98_304})) == {131_072}
    ollama.asked.clear()
    assert set(await _run_once(ollama, {})) == {load_default_config().providers.ollama.num_ctx}
