"""A message that streams is drawn as it will stay (item 274), on a served daemon in Chrome, with a model that thinks
and then writes a message of ten parts in small pieces, faster than the page paints. While it streams the message is
formatted (its bold is bold), drawn at most once a frame, the parts written before the one being written left as they
were drawn, with the caret in its text, not in the column of the face; when it ends its height does not change; and the
arrow down never shows while the page follows the run to its answer, also when a long message comes whole, with the
answer right after it, from a model that does not stream. On a phone, light and dark, and wide. Before, the message
streamed as plain text in another line height, with its `**` showing, and became formatted prose when it ended, so that
the whole message moved; its caret stood under the face; and the arrow down showed for a frame when the answer came
below the window.

No network and no model service is used: the model is a scripted one that streams.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, TypedDict

import pytest
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

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

TOKEN: str = "streamed-message-test-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
STREAMER: str = f"{_PROVIDER}:streamer"
# A model of the same provider that does not stream: its message comes whole.
WHOLE: str = f"{_PROVIDER}:whole"
_PARTS: int = 10
MESSAGE: str = "\n\n".join(
    f"**Part {number}.** " + "Some words of the streamed message. " * 6 for number in range(1, _PARTS + 1)
)
_PIECE_CHARS: int = 6
_PIECE_S: float = 0.004
_THOUGHTS: int = 20
# The model rests this long after its last piece, so that the page has drawn the whole message before it ends.
_REST_S: float = 0.4
_RUN_WAIT_S: float = 60.0
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (390, "light"),
    (390, "dark"),
    (1280, "light"),
)
# What the page does while the message streams, seen once a frame. A draw is a change to the live message's text, as one
# call of a mutation observer sees it; a part is written once another follows it, and redrawn if its node leaves the
# page.
_WATCH: str = """() => {
  const seen = {
    frames: 0, draws: 0, formatted: false, carets: 0, caretsInText: 0, liveHeight: 0, written: 0, redrawn: 0,
    jumpShown: 0,
  };
  window.__seen = seen;
  const inText = (node) => {
    const element = node.nodeType === 1 ? node : node.parentElement;
    return element !== null && element.closest('.bubble.live') !== null && element.closest('.turn-face') === null;
  };
  new MutationObserver((records) => {
    if (records.some((record) => inText(record.target))) seen.draws += 1;
  }).observe(document.body, { subtree: true, childList: true, characterData: true });
  const written = new Set();
  const look = () => {
    seen.frames += 1;
    const live = document.querySelector('.bubble.live .prose');
    if (live !== null) {
      seen.liveHeight = live.getBoundingClientRect().height;
      seen.formatted = seen.formatted || live.querySelector('strong') !== null;
      const caret = live.querySelector('.stream-caret');
      if (caret !== null) {
        seen.carets += 1;
        if (caret.getBoundingClientRect().left >= live.getBoundingClientRect().left) seen.caretsInText += 1;
      }
      for (const part of written) {
        if (!part.isConnected) {
          seen.redrawn += 1;
          written.delete(part);
        }
      }
      for (const part of [...live.children].slice(0, -1)) {
        if (!written.has(part)) {
          written.add(part);
          seen.written += 1;
        }
      }
    }
    const jump = document.querySelector('.jump');
    if (jump !== null && !jump.hidden) seen.jumpShown += 1;
    if (!window.__stopWatch) requestAnimationFrame(look);
  };
  requestAnimationFrame(look);
}"""


class Seen(TypedDict):
    frames: int
    draws: int
    formatted: bool
    carets: int
    caretsInText: int
    liveHeight: float
    written: int
    redrawn: int
    jumpShown: int


class Streamer:
    """The scripted model under its provider's name, which streams its thought and its message in small pieces once the
    test lets it go; as ``whole`` it streams nothing."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script
        self.go: asyncio.Event = asyncio.Event()
        self.pieces: int = 0

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        reply: ModelReply = await self._script.complete(request, sink)
        await self.go.wait()
        self.go.clear()
        if request.model == "whole":
            return reply
        for number in range(_THOUGHTS):
            await sink(StreamDelta(kind="thinking", text=f"Thinking about part {number} of the ask. "))
            await asyncio.sleep(_PIECE_S)
        for start in range(0, len(MESSAGE), _PIECE_CHARS):
            await sink(StreamDelta(kind="text", text=MESSAGE[start : start + _PIECE_CHARS]))
            self.pieces += 1
            await asyncio.sleep(_PIECE_S)
        await asyncio.sleep(_REST_S)
        return reply

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return (await self._script.model_info("streamer"),)

    async def aclose(self) -> None:
        await self._script.aclose()


def _turn() -> ScriptedTurn:
    return ScriptedTurn(
        text=MESSAGE, calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Ten parts.", "success": True}),)
    )


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path, model: Streamer) -> AsyncIterator[tuple[RunManager, str]]:
    """A daemon on the Dot home ``home`` whose main model is ``model``: its run manager and the address it is at."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{STREAMER}"\n', encoding="utf-8"
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


async def _opened(page: Page) -> None:
    """Wait out the opening, which plays over the page once a tab, so that the pictures taken while it streams show
    it."""
    await page.wait_for_function("() => document.documentElement.dataset.opening === undefined")


async def _one_size(
    browser: Browser,
    address: str,
    manager: RunManager,
    model: Streamer,
    size: tuple[int, Literal["light", "dark"]],
    shots: Path,
) -> None:
    width, scheme = size
    page: Page = await browser.new_page(viewport={"width": width, "height": 844}, color_scheme=scheme)
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    run: RunRecord = await manager.start(
        "Write ten parts", "task", STREAMER, None, "test", None, None, "standard", None, "ask", False, ()
    )
    await page.goto(f"{address}/#/runs/{run.id}")
    await expect(page.locator(".dock .btn.send")).to_have_attribute("aria-label", "Stop")
    await _opened(page)
    await page.evaluate(_WATCH)
    model.pieces = 0
    model.go.set()

    # While it streams the message is formatted, its caret in its text.
    live: Locator = page.locator(".bubble.live .prose")
    await expect(live.locator("strong").nth(2)).to_have_text("Part 3.")
    await page.screenshot(path=str(shots / f"streaming-{width}-{scheme}.png"))
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(run.id)
    message: Locator = page.locator(".bubble[role='article']:not(.live) .prose").filter(has_text="Part 10.")
    await expect(message).to_have_count(1)
    await page.wait_for_timeout(500)
    await page.evaluate("window.__stopWatch = true")
    seen: Seen = await page.evaluate("window.__seen")
    final: float = await message.evaluate("(body) => body.getBoundingClientRect().height")
    await page.screenshot(path=str(shots / f"ended-{width}-{scheme}.png"))
    await page.close()

    assert seen["formatted"], seen
    assert seen["carets"] > 0 and seen["caretsInText"] == seen["carets"], seen
    # At most once a frame, far fewer times than the model sent a piece.
    assert 0 < seen["draws"] <= seen["frames"], seen
    assert seen["draws"] < model.pieces, (seen, model.pieces)
    # A draw changes the part being written alone: the parts written before it stay as they were drawn.
    assert seen["written"] >= _PARTS - 1 and seen["redrawn"] == 0, seen
    # The end moves nothing: the message is as tall as it was while it streamed.
    assert abs(final - seen["liveHeight"]) <= 1, (final, seen)
    assert seen["jumpShown"] == 0, seen


async def _whole(browser: Browser, address: str, manager: RunManager, model: Streamer, shots: Path) -> None:
    """A long message that comes whole, the answer right after it: the page lands on the answer, never offering the
    way down on the way."""
    page: Page = await browser.new_page(viewport={"width": 390, "height": 844})
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    run: RunRecord = await manager.start(
        "Write ten parts at once", "task", WHOLE, None, "test", None, None, "standard", None, "ask", False, ()
    )
    await page.goto(f"{address}/#/runs/{run.id}")
    await expect(page.locator(".dock .btn.send")).to_have_attribute("aria-label", "Stop")
    await _opened(page)
    await page.evaluate(_WATCH)
    model.go.set()
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(run.id)
    await expect(page.locator(".bubble[role='article'] .prose").filter(has_text="Part 10.")).to_have_count(1)
    await page.wait_for_timeout(500)
    await page.evaluate("window.__stopWatch = true")
    seen: Seen = await page.evaluate("window.__seen")
    await page.screenshot(path=str(shots / "whole-390-light.png"))
    await page.close()
    assert seen["jumpShown"] == 0, seen


async def test_a_streamed_message_is_drawn_as_it_will_stay(own_home: Path, tmp_path: Path) -> None:
    model: Streamer = Streamer(ScriptedProvider(_PROVIDER, [_turn() for _ in range(len(_SHOTS) + 1)]))
    async with _served(own_home, model) as (manager, address), async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for size in _SHOTS:
                await _one_size(browser, address, manager, model, size, tmp_path)
            await _whole(browser, address, manager, model, tmp_path)
        finally:
            await browser.close()
