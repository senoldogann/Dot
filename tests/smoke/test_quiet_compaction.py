"""A compaction is a quiet line in the conversation (item 312, after Codex's): a run that compacted its context twice
shows two lines where it compacted, the first with the size of the context before and after it, the second, of an event
from before item 312, without them; no note tells the summary or its check (item 260), and the Details panel says how
many times it compacted and how many checks flagged something. A real server over a stand-in of Anthropic's provider in
Chrome, wide and on a phone, light and dark. Before item 260 each compaction was a note in the timeline ("Context
compacted: …"); from item 260 to item 312 the conversation showed nothing of it."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, ThreadRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "quiet-compaction-token"
OPUS: str = "anthropic:claude-opus-5-5"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
_FLAGGED: JsonObject = {
    "summarised_messages": 6,
    "compaction": 1,
    "tokens_before": 401_234,
    "tokens_after": 17_100,
    "method": "model",
    "model": OPUS,
    "attempts": 2,
    "problem": None,
    "fast_problem": None,
    "check": {"ok": False, "missing_sections": ["PLAN"], "unverified": ["/tmp/nowhere.txt"], "summary_chars": 900},
}
# An event of before item 312: it does not say the sizes.
_CLEAN: JsonObject = {
    "summarised_messages": 5,
    "compaction": 2,
    "method": "model",
    "model": OPUS,
    "attempts": 1,
    "problem": None,
    "fast_problem": None,
    "check": {"ok": True, "missing_sections": [], "unverified": [], "summary_chars": 800},
}


class Claude:
    """Anthropic's provider as far as the page needs it: one model, every call answers."""

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        return await ScriptedProvider(self.name, [ScriptedTurn(text="Hi.", calls=())]).complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return ModelInfo(
            id=model,
            vision=True,
            tools=True,
            thinking=True,
            context_length=1_000_000,
            own_context_length=None,
            window_source="provider",
            efforts=(),
            free=False,
        )

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return (await self.model_info("claude-opus-5-5"),)

    async def aclose(self) -> None:
        return None


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home, served by the stand-in."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(f'[browser]\nheadless = true\n\n[models]\nmain = "{OPUS}"\n', encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register("anthropic", Claude())
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[str]:
    """The address of a real server over the runtime."""
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
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()


async def _seed(runtime: Runtime) -> RunRecord:
    """A finished run that compacted its context twice, the first check flagged."""
    thread: ThreadRecord = await runtime.store.create_thread("Read the report", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        "Read the report",
        "task",
        OPUS,
        None,
        "test",
        "medium",
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )
    await runtime.store.add_event(run.id, "compacted", _FLAGGED)
    await runtime.store.add_event(run.id, "compacted", _CLEAN)
    await runtime.store.end_run(run.id, "done", "Done.", {"status": "done", "result": "Done.", "needs": ""})
    return run


@pytest.mark.browser
async def test_a_compaction_is_a_quiet_line_in_the_conversation_and_is_counted_in_the_details(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    run: RunRecord = await _seed(runtime)
    host, port = "127.0.0.1", int(served.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{served}/#/runs/{run.id}")
                details = page.locator("#panel-run-details")
                await expect(details).to_contain_text("Compacted 2 times")
                await expect(details).to_contain_text("1 check flagged")
                lines = page.locator(".timeline .compaction-line")
                await expect(lines).to_have_text(["Context compacted 401k → 17k tokens", "Context compacted"])
                await expect(lines.first).to_be_visible()
                await expect(page.locator(".timeline .note", has_text="compacted")).to_have_count(0)
                await expect(page.locator(".timeline .note", has_text="summarised")).to_have_count(0)
                await page.screenshot(path=str(tmp_path / f"quiet-{width}-{scheme}.png"), animations="disabled")
                await page.close()
        finally:
            await browser.close()
