"""Fast mode (item 223): the Claude models that have it offer it in the composer's reasoning menu, off at first; a chat
keeps it with its choices, and the run it starts asks the model for it at every step, with the beta Anthropic names.
A model without it refuses it. A stand-in of Anthropic's API for the request, and a real server over a stand-in of the
provider in a browser, wide and on a phone, light and dark."""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from playwright.async_api import Browser, Locator, Page, async_playwright, expect
from test_wire_codecs import claude_entry

from dotagent.agent.manager import RunManager
from dotagent.config import AnthropicConfig, load_config
from dotagent.core.types import JsonObject
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.llm.anthropic import AnthropicProvider
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply, StreamDelta, system_message, user_message
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import ChatChoices, RunRecord, ThreadRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "fast-mode-token"
OPUS: str = "anthropic:claude-opus-5-5"
HAIKU: str = "anthropic:claude-haiku-5-5"
_BETAS: str = "thinking-binding-controls-2026-08-01"
_FAST_BETA: str = "fast-mode-2026-02-01"
_RUN_WAIT_S: float = 30.0
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (1280, "dark"), (390, "light"))
# A reply of Anthropic's Messages API, streamed: one line of text, at the speed the request asked.
_EVENTS: tuple[tuple[str, JsonObject], ...] = (
    (
        "message_start",
        {"type": "message_start", "message": {"usage": {"input_tokens": 12, "output_tokens": 1}}},
    ),
    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
    (
        "content_block_delta",
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi."}},
    ),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}}),
    ("message_stop", {"type": "message_stop"}),
)


def _stream() -> Iterator[bytes]:
    for name, data in _EVENTS:
        yield f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()


def _stand_in(asked: list[tuple[str, JsonObject]]) -> FastAPI:
    """Anthropic's API as far as a call needs it: a model's entry, and a reply to a message; each request's betas and
    body go to ``asked``."""
    app: FastAPI = FastAPI()

    @app.get("/v1/models/{model}")
    async def entry(model: str) -> JsonObject:
        return claude_entry(model, ("adaptive",), ("low", "medium", "high", "max"), 1_000_000, 128_000)

    @app.post("/v1/messages")
    async def messages(request: Request) -> StreamingResponse:
        body: object = await request.json()
        assert isinstance(body, dict)
        asked.append((request.headers.get("anthropic-beta", ""), body))
        return StreamingResponse(_stream(), media_type="text/event-stream")

    return app


async def _ignore(delta: StreamDelta) -> None:
    return None


def _request(speed: Literal["standard", "fast"]) -> ChatRequest:
    return ChatRequest(
        model="claude-opus-5-5",
        messages=(system_message("be brief"), user_message("Say hi.", ())),
        tools=(),
        temperature=None,
        max_output_tokens=None,
        effort="high",
        speed=speed,
        session_key="fast-mode",
    )


async def test_fast_mode_asks_claude_for_its_speed_with_the_beta_and_a_standard_call_does_not() -> None:
    asked: list[tuple[str, JsonObject]] = []
    listener = bind_listener("127.0.0.1", 0)
    port: int = listener.getsockname()[1]
    server: uvicorn.Server = uvicorn.Server(serving_config(_stand_in(asked)))
    serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        assert not serving.done(), "the stand-in stopped while starting"
        await asyncio.sleep(0.05)
    config: AnthropicConfig = AnthropicConfig(
        base_url=f"http://127.0.0.1:{port}/v1",
        api_key_env="ANTHROPIC_API_KEY",
        connect_timeout_s=5.0,
        read_timeout_s=10.0,
        attempts=1,
        max_concurrent=1,
    )
    provider: AnthropicProvider = AnthropicProvider(config, "test-key")
    try:
        fast: ModelReply = await provider.complete(_request("fast"), _ignore)
        standard: ModelReply = await provider.complete(_request("standard"), _ignore)
    finally:
        await provider.aclose()
        server.should_exit = True
        await serving
    assert (fast.text, standard.text) == ("Hi.", "Hi.")
    (fast_betas, fast_body), (standard_betas, standard_body) = asked
    assert fast_betas == f"{_BETAS},{_FAST_BETA}" and fast_body["speed"] == "fast" and list(fast_body)[-1] == "stream"
    assert standard_betas == _BETAS and "speed" not in standard_body
    assert fast_body["output_config"] == standard_body["output_config"] == {"effort": "high"}


def _reply() -> ScriptedTurn:
    """A chat's answer: words, and no call, which ends its run."""
    return ScriptedTurn(text="Hi.", calls=())


class Claude:
    """Anthropic's provider as far as the menu and a run need it: Opus 5.5, which has fast mode, and Haiku 5.5, which
    has not, each with reasoning levels; every step answers, which ends a chat's run, and what each asked for is kept
    in ``asked``."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script
        self.asked: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.asked.append((request.model, request.speed))
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return ModelInfo(
            id=model,
            vision=True,
            tools=True,
            thinking=True,
            context_length=1_000_000,
            own_context_length=None,
            efforts=("low", "medium", "high", "max"),
            free=False,
        )

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return (await self.model_info("claude-haiku-5-5"), await self.model_info("claude-opus-5-5"))

    async def aclose(self) -> None:
        await self._script.aclose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def claude() -> Claude:
    return Claude(ScriptedProvider("anthropic", [_reply() for _ in range(4)]))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory, claude: Claude) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home whose default model is Opus 5.5, served by the stand-in."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(f'[browser]\nheadless = true\n\n[models]\nmain = "{OPUS}"\n', encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register("anthropic", claude)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[tuple[RunManager, str]]:
    """The run manager and the address of a real server over it."""
    manager: RunManager = RunManager(runtime)
    try:
        stopping: asyncio.Event = asyncio.Event()
        listener = bind_listener("127.0.0.1", 0)
        port: int = listener.getsockname()[1]
        context: ServerContext = ServerContext(
            runtime=runtime,
            manager=manager,
            scheduler=None,
            token=TOKEN,
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
        try:
            yield manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()


async def _ended(manager: RunManager, run_id: str) -> RunRecord:
    async with asyncio.timeout(_RUN_WAIT_S):
        return await manager.wait(run_id)


@pytest.mark.browser
async def test_the_reasoning_menu_offers_fast_mode_and_the_chats_next_run_asks_for_it(
    runtime: Runtime, served: tuple[RunManager, str], claude: Claude, tmp_path: Path
) -> None:
    manager, address = served
    first: RunRecord = await manager.start(
        "Say hi.", "chat", OPUS, None, "test", None, None, "standard", "yolo", None, ()
    )
    ended: RunRecord = await _ended(manager, first.id)
    assert ended.status == "done", ended.result
    assert claude.asked == [("claude-opus-5-5", "standard")]
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{address}/#/runs/{first.id}")
                pill: Locator = page.locator(".dock .composer-pill[data-pill='reasoning']")
                await expect(pill).to_be_visible()
                await pill.click()
                menu: Locator = page.locator(".composer-popover[aria-label='Reasoning']")
                speed: Locator = menu.get_by_role("listbox", name="Speed")
                await expect(speed.get_by_role("option")).to_have_count(2)
                fast: Locator = speed.get_by_role("option", name="Fast")
                await expect(fast).to_contain_text("twice the price")
                if index == 0:
                    await expect(speed.get_by_role("option", name="Standard")).to_have_attribute(
                        "aria-selected", "true"
                    )
                    await fast.click()
                    await expect(pill).to_contain_text("Fast")
                    # The page tells the daemon, which keeps it with the chat's choices.
                    await _kept_fast(runtime, first.thread_id)
                    await pill.click()
                await expect(fast).to_have_attribute("aria-selected", "true")
                await page.screenshot(path=str(tmp_path / f"fast-{width}-{scheme}.png"), animations="disabled")
                await page.keyboard.press("Escape")
                await page.close()
        finally:
            await browser.close()
    # The chat's next run starts in fast mode, and its step asks the model for it.
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        started: httpx.Response = await client.post(
            "/api/runs", json={"goal": "Say hi again.", "mode": "chat", "thread_id": first.thread_id, "speed": "fast"}
        )
    assert started.status_code == 201, started.text
    second: RunRecord = await _ended(manager, started.json()["id"])
    assert (second.status, second.speed) == ("done", "fast")
    assert claude.asked[-1] == ("claude-opus-5-5", "fast")


async def _kept_fast(runtime: Runtime, thread_id: str) -> None:
    """Wait until the chat keeps fast mode with its choices."""
    async with asyncio.timeout(10):
        while True:
            choices: ChatChoices | None = await runtime.store.chat_choices(thread_id)
            if choices is not None and choices.speed == "fast":
                return
            await asyncio.sleep(0.05)


async def test_a_model_without_fast_mode_refuses_it(runtime: Runtime, served: tuple[RunManager, str]) -> None:
    _, address = served
    thread: ThreadRecord = await runtime.store.create_thread("Say hi.", "test")
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        options: httpx.Response = await client.get("/api/models")
        run: httpx.Response = await client.post(
            "/api/runs", json={"goal": "Say hi.", "mode": "chat", "model": HAIKU, "speed": "fast"}
        )
        kept: httpx.Response = await client.patch(
            f"/api/threads/{thread.id}",
            json={
                "choices": {
                    "mode": "chat",
                    "model": HAIKU,
                    "effort": None,
                    "speed": "fast",
                    "approvals": None,
                    "goal_mode": None,
                }
            },
        )
    assert options.status_code == 200, options.text
    listed: dict[str, bool] = {
        model["ref"]: model["fast_mode"]
        for provider in options.json()
        for model in provider["models"]
        if provider["provider"] == "anthropic"
    }
    assert listed == {HAIKU: False, OPUS: True}
    assert (run.status_code, kept.status_code) == (422, 422), (run.text, kept.text)
    assert "the model does not offer fast mode" in run.text and "the model does not offer fast mode" in kept.text
