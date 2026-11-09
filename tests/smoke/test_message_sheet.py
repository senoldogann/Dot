"""A message's sheet on a touch screen (item 1064): no buttons under a message there, and holding one opens a sheet from
the foot of the screen with the reactions and what the message can do. Holding Dot's answer: its seven reactions and
Copy, Select text; a reaction pressed there stands under the answer. Holding your message: Copy, Select text, Edit from
here; Select text selects its words. A Mac's window keeps its buttons. A real server on a scripted model, in a phone's
window that touches."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from message_hold import HOLD, HOLD_MS
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

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

TOKEN: str = "message-sheet-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
ASKED: str = "What does the sea do at night?"
ANSWER: str = "It keeps moving under the moon."
# Whether every element `selector` finds under the node is out of sight: a box of a pixel at most, as app.css keeps the
# buttons under a message on a touch screen, out of sight but in VoiceOver's and the keyboard's reach.
_OUT_OF_SIGHT: str = """(node, selector) => [...node.querySelectorAll(selector)].every((found) => {
  const box = found.getBoundingClientRect();
  return box.width <= 1 || box.height <= 1;
})"""


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
    finish: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": ANSWER, "success": True}),)
    )
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [finish]))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[tuple[str, RunRecord]]:
    manager: RunManager = RunManager(runtime)
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
        run: RunRecord = await manager.start(
            ASKED, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
        )
        async with asyncio.timeout(30):
            assert (await manager.wait(run.id)).status == "done"
        yield f"http://127.0.0.1:{port}", run
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


async def _middle(node: Locator) -> tuple[float, float]:
    box = await node.bounding_box()
    assert box is not None
    return box["x"] + box["width"] / 2, box["y"] + min(12.0, box["height"] / 2)


async def _open(browser: Browser, address: str, run: RunRecord, touch: bool) -> tuple[BrowserContext, Page]:
    context: BrowserContext = await browser.new_context(
        viewport={"width": 390, "height": 844} if touch else {"width": 1280, "height": 900},
        is_mobile=touch,
        has_touch=touch,
        color_scheme="dark",
        reduced_motion="reduce",
    )
    page: Page = await context.new_page()
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    await page.goto(f"{address}/#/runs/{run.id}")
    await expect(page.locator(".timeline article.answer")).to_be_visible(timeout=15000)
    return context, page


async def test_holding_a_message_opens_its_sheet_and_a_touch_screen_shows_no_buttons(
    served: tuple[str, RunRecord], tmp_path: Path
) -> None:
    address, run = served
    async with async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch()
        try:
            context, page = await _open(browser, address, run, True)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            assert await page.evaluate("matchMedia('(hover: none)').matches")
            answer: Locator = page.locator(".timeline article.answer")
            assert await answer.evaluate(_OUT_OF_SIGHT, ".answer-foot, .answer-foot > *")
            assert await page.locator(".user-message").evaluate(_OUT_OF_SIGHT, ".message-foot, .message-foot > *")
            # Out of sight, the buttons are still there for VoiceOver.
            await expect(answer.get_by_role("button", name="Copy the answer")).to_be_attached()

            # Dot's answer: the seven reactions and what it can do; a reaction pressed stands under it.
            await page.evaluate(HOLD, [".timeline article.answer .prose", HOLD_MS])
            sheet: Locator = page.locator("dialog.message-sheet")
            await expect(sheet).to_be_visible()
            await expect(sheet.locator(".sheet-reaction")).to_have_count(7)
            labels: list[str] = await sheet.locator(".sheet-action").all_inner_texts()
            assert labels[:2] == ["Copy the answer", "Select text"], labels
            await page.screenshot(path=str(tmp_path / "answer-sheet.png"))
            await sheet.get_by_role("button", name="Good").click()
            await expect(sheet).to_have_count(0)
            await expect(answer.locator(".reaction-chip")).to_have_count(1)
            await expect(answer.locator(".answer-foot")).to_be_visible()
            assert await answer.evaluate(_OUT_OF_SIGHT, ".answer-foot .btn")

            # Your message: Copy, Select text and Edit from here; Select text selects its words.
            await page.evaluate(HOLD, [".user-message .bubble.user", HOLD_MS])
            await expect(sheet).to_be_visible()
            labels = await sheet.locator(".sheet-action").all_inner_texts()
            assert labels == ["Copy the message", "Select text", "Edit from here"], labels
            await expect(sheet.locator(".sheet-reaction")).to_have_count(0)
            await sheet.get_by_role("button", name="Select text").click()
            await expect(sheet).to_have_count(0)
            assert (await page.evaluate("getSelection().toString()")).strip() == ASKED
            # The words stay selectable until the selection leaves them; a hold then opens the sheet again.
            await page.evaluate("getSelection().removeAllRanges()")
            await expect(page.locator(".user-message.selecting")).to_have_count(0)

            # While its turn's run works, your message's foot is kept back, and its sheet lends none of it: an Edit
            # from here would be refused then.
            await page.evaluate("document.querySelector('.run-thread').classList.add('run-working')")
            await page.evaluate(HOLD, [".user-message .bubble.user", HOLD_MS])
            await expect(sheet).to_be_visible()
            assert await sheet.locator(".sheet-action").all_inner_texts() == ["Select text"]
            await page.keyboard.press("Escape")
            await expect(sheet).to_have_count(0)
            await page.evaluate("document.querySelector('.run-thread').classList.remove('run-working')")
            # A link keeps the phone's own menu of a long press.
            assert await page.evaluate(
                "() => { const link = document.createElement('a'); link.href = '#'; link.textContent = 'here';"
                " document.querySelector('.timeline article.answer .prose').append(link);"
                " return getComputedStyle(link).webkitTouchCallout ?? 'default'; }"
            ) in ("default", "")
            # A short press is no hold: no sheet.
            await page.evaluate(HOLD, [".timeline article.answer .prose", 100])
            await page.wait_for_timeout(600)
            await expect(sheet).to_have_count(0)
            assert errors == []
            await context.close()

            # A Mac's window keeps the buttons under a message, and a hold of the mouse opens nothing.
            context, page = await _open(browser, address, run, False)
            assert not await page.evaluate("matchMedia('(hover: none)').matches")
            answer = page.locator(".timeline article.answer")
            await page.mouse.move(*(await _middle(answer)))
            await page.mouse.down()
            await page.wait_for_timeout(HOLD_MS)
            await page.mouse.up()
            await expect(page.locator("dialog.message-sheet")).to_have_count(0)
            await answer.hover()
            await expect(
                page.locator(".timeline .answer-foot").get_by_role("button", name="Copy the answer")
            ).to_be_visible()
            await context.close()
        finally:
            await browser.close()
