"""A run the owner stops keeps the words it had streamed, with a quiet "stopped" note under them, instead of a bare
"The run was stopped." that replaces everything they had read (item 23 of the 2026-11-26 review). A real server on a
model that streams a few words and then waits to be stopped, in Chrome, in English and in Turkish."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply, StreamDelta
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "stopped-run-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_GOAL: str = "Tell me about the shelves, at length."
_STREAMED: str = "The winter things are sorted by the room they belong to."
# What the page draws of them: the daemon holds back the last word, which may still become part of a secret (item 551).
_SHOWN: str = "The winter things are sorted by the room they belong"
_WAIT_S: float = 30.0


class StreamingProvider(ScriptedProvider):
    """A model that streams a few words once `go` is set (the page follows the live stream by then), then waits for
    ever: only a stop ends its reply."""

    def __init__(self) -> None:
        super().__init__(_PROVIDER, [])
        self.go: asyncio.Event = asyncio.Event()

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        await self.go.wait()
        await sink(StreamDelta(kind="text", text=_STREAMED[:30]))
        await sink(StreamDelta(kind="text", text=_STREAMED[30:]))
        await asyncio.Event().wait()
        raise AssertionError("a stop ends the wait")


STREAMING = StreamingProvider()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        "[browser]\nheadless = true\n\n[agent]\nplaybooks = false"
        f'\ngoal_mode = false\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n'
        f'review = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, STREAMING)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[tuple[RunManager, str]]:
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


async def _stop_after_streaming(browser: Browser, manager: RunManager, address: str, language: str) -> Page:
    """Open a run that streams, stop it from the composer once its words show, and return the page once it ended."""
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    STREAMING.go.clear()
    run: RunRecord = await manager.start(
        _GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    context = await browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme="light")
    page: Page = await context.new_page()
    await page.add_init_script(
        f"sessionStorage.setItem('dot.opened', '1'); localStorage.setItem('dot.uiLanguage', '{language}')"
    )
    await page.goto(ui_url(host, port, TOKEN))
    # The live stream, not the stored events the page reads first: the words are sent once the page follows it.
    async with page.expect_response(
        lambda response: f"/runs/{run.id}/stream" in response.url and "stored=true" not in response.url,
        timeout=_WAIT_S * 1000,
    ):
        await page.goto(f"{address}/#/runs/{run.id}")
    STREAMING.go.set()
    await expect(page.get_by_text(_SHOWN)).to_be_visible(timeout=_WAIT_S * 1000)
    await page.locator(
        "button.btn.send, button[title='Stop this task'], button[title='Bu görevi durdur']"
    ).first.click()
    await expect(page.locator(".answer-cancelled")).to_be_visible(timeout=_WAIT_S * 1000)
    return page


async def test_a_stopped_run_keeps_the_words_it_streamed_with_a_stopped_note(served: tuple[RunManager, str]) -> None:
    manager, address = served
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            page: Page = await _stop_after_streaming(browser, manager, address, "en")
            # The words the owner had read are still there, in the answer's place, and a note says the run stopped.
            answer = page.locator(".answer-cancelled")
            await expect(answer).to_contain_text(_SHOWN)
            await expect(answer.locator(".answer-stopped-note")).to_have_text("Stopped")
            await expect(page.get_by_text("The run was stopped.")).to_have_count(0)
        finally:
            await browser.close()


async def test_in_turkish_the_note_says_durduruldu(served: tuple[RunManager, str]) -> None:
    manager, address = served
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            page: Page = await _stop_after_streaming(browser, manager, address, "tr")
            answer = page.locator(".answer-cancelled")
            await expect(answer).to_contain_text(_SHOWN)
            await expect(answer.locator(".answer-stopped-note")).to_have_text("Durduruldu")
        finally:
            await browser.close()
