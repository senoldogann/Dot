"""The pictures a sent message carried (item 431): small squares side by side over the message, not chips with a name
under it; a file that is no picture stays a chip under it. A real server on a scripted model."""

import asyncio
import re
import struct
import time
import zlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.attachments import StoredAttachment, store_attachment
from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "sent-pictures-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_GOAL: str = "What do these two photos show?"
_RUN_WAIT_S: float = 30.0
_TILE_PX: float = 56.0


def _png(red: int, green: int, blue: int) -> bytes:
    """A 64 x 64 PNG of one colour."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    row: bytes = b"\x00" + bytes((red, green, blue)) * 64
    header: bytes = struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(row * 64)) + chunk(b"IEND", b"")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\nvision = "{SCRIPTED_MODEL}"\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    answer: ScriptedTurn = ScriptedTurn(
        text="",
        calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "A red and a blue square.", "success": True}),),
    )
    # The pictures are described first, by the vision model, which is the scripted one here.
    described: ScriptedTurn = ScriptedTurn(text="A red square and a blue one.", calls=())
    # Two runs of the module, each described and answered: the turns are taken in order, whichever test runs first.
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [described, answer, described, answer]))
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


async def _ended(runtime: Runtime, run_id: str) -> RunRecord:
    deadline: float = time.monotonic() + _RUN_WAIT_S
    while time.monotonic() < deadline:
        run: RunRecord = await runtime.store.get_run(run_id)
        if run.status in ("done", "failed", "cancelled"):
            return run
        await asyncio.sleep(0.1)
    raise AssertionError(f"the run {run_id} did not end in {_RUN_WAIT_S} s")


async def test_pictures_stand_over_the_message_as_small_squares_and_a_text_file_stays_a_chip(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    files: list[StoredAttachment] = [
        await asyncio.to_thread(store_attachment, runtime.workspace, "red-wall.png", "image/png", _png(220, 40, 40)),
        await asyncio.to_thread(store_attachment, runtime.workspace, "blue-door.png", "image/png", _png(40, 60, 220)),
        await asyncio.to_thread(store_attachment, runtime.workspace, "notes.txt", "text/plain", b"Two photos.\n"),
    ]
    run: RunRecord = await manager.start(
        _GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, tuple(files)
    )
    assert (await _ended(runtime, run.id)).status == "done"
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width in (1280, 390):
                page: Page = await browser.new_page(viewport={"width": width, "height": 800}, color_scheme="dark")
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                message: Locator = page.locator(".run-thread > .user-message")
                await expect(message.locator(".bubble.user")).to_have_text(_GOAL)
                tiles: Locator = message.locator(".sent-picture")
                await expect(tiles).to_have_count(2)
                # The pictures stand over the bubble, a file that is no picture under it, then the foot.
                parts: list[str] = await message.evaluate("node => [...node.children].map((child) => child.className)")
                assert parts == ["attach-list sent pictures", "bubble user", "attach-list sent", "message-foot"], parts
                # Each is the picture itself, drawn, with no name beside it.
                await expect(tiles.locator("img")).to_have_count(2)
                await page.wait_for_function(
                    "() => [...document.querySelectorAll('.sent-picture img')].every((i) => i.naturalWidth === 64)"
                )
                await expect(tiles.first).to_have_text("")
                await expect(tiles.first).to_have_attribute("title", "Show red-wall.png")
                # Two squares of one size, side by side on one line, over the bubble.
                first = await tiles.nth(0).bounding_box()
                second = await tiles.nth(1).bounding_box()
                bubble = await message.locator(".bubble.user").bounding_box()
                assert first is not None and second is not None and bubble is not None
                # A box sits on the subpixel grid of its place in the page: its size is the tile's within a hundredth.
                sizes = [round(box[side], 2) for box in (first, second) for side in ("width", "height")]
                assert sizes == [_TILE_PX] * 4, sizes
                assert abs(first["y"] - second["y"]) <= 1 and first["x"] + first["width"] <= second["x"]
                assert second["y"] + second["height"] <= bubble["y"] + 1
                # The text file keeps its chip, with its name, and no picture has one.
                await expect(message.locator(".attach-chip")).to_have_count(1)
                await expect(message.locator(".attach-chip")).to_contain_text("notes.txt")
                await page.screenshot(path=tmp_path / f"pictures-{width}.png", animations="disabled")
                # A page change lets the pictures' URLs go, and the chat drawn again makes them anew.
                shown: list[str] = await tiles.locator("img").evaluate_all("images => images.map((image) => image.src)")
                await page.evaluate("location.hash = '#/settings'")
                await expect(message).to_have_count(0)
                assert await page.evaluate("url => fetch(url).then(() => 'read', () => 'gone')", shown[0]) == "gone"
                await page.evaluate("id => { location.hash = `#/runs/${id}`; }", run.id)
                await expect(tiles.locator("img")).to_have_count(2)
                await page.wait_for_function(
                    "() => [...document.querySelectorAll('.sent-picture img')].every((i) => i.naturalWidth === 64)"
                )
                again: list[str] = await tiles.locator("img").evaluate_all("images => images.map((image) => image.src)")
                assert not set(again) & set(shown), (shown, again)
                await page.close()
        finally:
            await browser.close()


async def test_a_picture_that_cannot_be_read_says_so_where_it_stands(
    runtime: Runtime, served: tuple[RunManager, str]
) -> None:
    """A picture whose file is gone shows the mark of a fault and why, as a chip of the box does, not a plain image."""
    manager, address = served
    gone: StoredAttachment = await asyncio.to_thread(
        store_attachment, runtime.workspace, "lost.png", "image/png", _png(10, 10, 10)
    )
    run: RunRecord = await manager.start(
        _GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, (gone,)
    )
    assert (await _ended(runtime, run.id)).status == "done"
    await asyncio.to_thread(Path(gone.path).unlink)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 800}, color_scheme="dark")
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            await page.goto(f"{address}/#/runs/{run.id}")
            tile: Locator = page.locator(".run-thread > .user-message .sent-picture")
            await expect(tile).to_have_count(1)
            await expect(tile).to_have_attribute("title", re.compile(r"^lost\.png: \S"))
            await expect(tile.locator("img")).to_have_count(0)
        finally:
            await browser.close()
