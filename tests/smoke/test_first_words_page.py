"""Dot's first words in a chat on the web (item 2018): the words said at once as a run starts show before the run has
done anything, the fast model's line takes their place, in every way of showing the work and at a desktop's and a
phone's width, and they stay once the run is over. Measured in Chrome on a real daemon whose scripted model plays the
run (held at its first step until the line is shown) and the fast model."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio, pytest.mark.first_words]

TOKEN: str = "first-words-token"
LINE: str = "Looking at the folder first."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
)


class _Model(ScriptedProvider):
    """The run's one step, held until ``go`` is set, and the fast model's line, at once."""

    def __init__(self) -> None:
        super().__init__(_PROVIDER, [_FINISH])
        self.go: asyncio.Event = asyncio.Event()

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if request.session_key.endswith(":first_words"):
            return await ScriptedProvider(_PROVIDER, [ScriptedTurn(text=LINE, calls=())]).complete(request, sink)
        await self.go.wait()
        return await super().complete(request, sink)


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path, model: _Model) -> AsyncIterator[tuple[RunManager, str, int]]:
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\nfast = "{SCRIPTED_MODEL}"\n\n'
        "[agent]\ngoal_mode = false\nfirst_words = true",
        encoding="utf-8",
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, model)
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
            yield manager, f"http://127.0.0.1:{port}", port
        finally:
            model.go.set()
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def _open(browser: Browser, port: int, address: str, run_id: str, work: str, width: int) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 844 if width < 600 else 900}, reduced_motion="reduce"
    )
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.add_init_script(f"localStorage.setItem('dot.work', '{work}')")
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    return page


@pytest.mark.parametrize("width", [1440, 390])
async def test_the_first_words_show_in_every_way_of_showing_the_work_and_stay(own_home: Path, width: int) -> None:
    model: _Model = _Model()
    async with _served(own_home, model) as (manager, address, port):
        run: RunRecord = await manager.start(
            "Tidy the folder", "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
        )
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                for work in ("steps", "status", "messages"):
                    page: Page = await _open(browser, port, address, run.id, work, width)
                    first: Locator = page.locator(".say-first")
                    # One message of Dot's, the fast model's line in the place of the words said at once.
                    await expect(first).to_have_text(LINE)
                    await expect(first).to_be_visible()
                    await page.close()
                model.go.set()
                async with asyncio.timeout(30):
                    assert (await manager.wait(run.id)).status == "done"
                page = await _open(browser, port, address, run.id, "messages", width)
                await expect(page.locator(".answer .prose")).to_have_text("Done.")
                await expect(page.locator(".say-first")).to_have_text(LINE)
                await page.close()
            finally:
                await browser.close()
