"""A run still at work, left for another page while its reply streams and opened again (item 559), on a served daemon in
Chrome with a model that calls a tool and then writes a message in pieces. The page draws what it read of the run at
once and goes on from the newest event the daemon stored, not from the last message it was sent: that is the words of
the reply being written, stored nowhere (their id is 0). Before, it went on from 0, so the daemon sent every stored
event again and each was drawn a second time: the message, the call, and the numbers of the run, and a card that was
answered stayed as it was asked.

No network and no model service is used: the model is a scripted one that streams.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply, StreamDelta
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "kept-live-run-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
GOAL: str = "Say what the tide did, then write it down."
ANSWER: str = "The kept run is done."
_WORDS: tuple[str, ...] = ("The tide ", "came in ", "at noon.")
_RUN_WAIT_S: float = 60.0


class Writer:
    """The scripted model under its provider's name. Its first reply is a call of a tool, which the run makes; its
    second waits for the test to say the page follows the run (``write``: the words of a reply being written are sent
    to the pages that follow it then, and kept nowhere), writes a message in pieces and rests where the last of them
    was sent, until the test lets it go on (``go_on``)."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script
        self._calls: int = 0
        self.write: asyncio.Event = asyncio.Event()
        self.go_on: asyncio.Event = asyncio.Event()

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        reply: ModelReply = await self._script.complete(request, sink)
        self._calls += 1
        if self._calls == 2:
            await self.write.wait()
            for piece in _WORDS:
                await sink(StreamDelta(kind="text", text=piece))
            await self.go_on.wait()
        return reply

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


def _script() -> list[ScriptedTurn]:
    return [
        ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "echo the tide"}),)),
        ScriptedTurn(
            text="".join(_WORDS),
            calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": ANSWER, "success": True}),),
        ),
    ]


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path, model: Writer) -> AsyncIterator[tuple[RunManager, str]]:
    """A daemon on the Dot home ``home`` whose main model is ``model``: its run manager and the address it is at."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[agent]\nplaybooks = false\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n'
        "[memory]\nembed_enabled = false\n",
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
            yield manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def test_a_run_at_work_that_is_opened_again_goes_on_from_its_newest_stored_event(
    own_home: Path, tmp_path: Path
) -> None:
    model: Writer = Writer(ScriptedProvider(_PROVIDER, _script()))
    async with _served(own_home, model) as (manager, address), async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.add_init_script(
                "sessionStorage.setItem('dot.opened', '1'); localStorage.setItem('dot.foldSteps', 'off')"
            )
            streams: list[str] = []
            page.on("request", lambda request: streams.append(request.url) if "/runs/" in request.url else None)
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            run: RunRecord = await manager.start(
                GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
            )
            await page.goto(f"{address}/#/runs/{run.id}")
            # The call is read, and the message is on its way: the last thing the page was sent is a piece of it.
            await expect(page.locator(".tool-name")).to_have_count(1, timeout=30_000)
            model.write.set()
            # The word being written (``noon.``) waits for what follows it, in case it is the start of a key (item
            # 551): the words before it are on the page.
            await expect(page.locator(".bubble.live")).to_contain_text("came in at")
            await page.goto(f"{address}/#/agents")
            await expect(page.locator(".agent-card").first).to_be_visible()
            # Back on the run, the page follows its stream again: waited for, as the view it kept shows the call
            # at once.
            async with page.expect_request(lambda request: f"/api/runs/{run.id}/stream" in request.url):
                await page.go_back()
            await expect(page.locator(".tool-name")).to_have_count(1)
            await expect(page.locator(".user-message")).to_have_count(1)
            # The page read the stored events once, when it first opened the run (item 734), and went on from an event
            # the daemon stored, after the one its first stream started from, not from 0 again.
            asked: list[dict[str, list[str]]] = [
                parse_qs(urlsplit(url).query) for url in streams if urlsplit(url).path == f"/api/runs/{run.id}/stream"
            ]
            stored: list[int] = [int(query["after"][0]) for query in asked if query.get("stored") == ["true"]]
            followed: list[int] = [int(query["after"][0]) for query in asked if query.get("stored") != ["true"]]
            assert stored == [0], asked
            assert len(followed) == 2 and followed[1] > followed[0] >= 0, asked

            model.go_on.set()
            async with asyncio.timeout(_RUN_WAIT_S):
                assert (await manager.wait(run.id)).status == "done"
            await expect(page.get_by_text(ANSWER)).to_have_count(1)
            # Each thing the run did is drawn once: the message sent, and the call.
            await expect(page.locator(".user-message")).to_have_count(1)
            await expect(page.locator(".tool-name")).to_have_count(1)
            await page.screenshot(path=str(tmp_path / "kept-live-run.png"))
            await page.close()
        finally:
            await browser.close()
