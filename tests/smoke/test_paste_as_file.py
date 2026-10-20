"""A long text pasted into a message box goes as a file (item 269), on the pages of a served daemon in Chrome: a long
Markdown text becomes `pasted-text-<time>.md` and a long plain one `pasted-text-<time>.txt`, each uploaded at once and
nothing typed into the box, while a short paste is the box's own; a pasted file goes back into the box as text from its
chip. A message of files alone goes with no word typed (item 445): a long paste or a picture pasted in is the message,
and the run it starts reads "See the attached file."; on a run at work a send with a file listed stays a send, never the
Stop that an empty box makes it. The chip of the new chat's box opens the file in a dialog,
the page having no panel beside a conversation: a Markdown file formatted, its HTML never drawn, and as it is written a
click away. Wide and on a phone, light and dark. Before, a paste of any length went into the box as text.

No network and no model service is used: the run at work waits for a scripted model that the test lets go at the end.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "paste-as-file-test-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
HELD: str = f"{_PROVIDER}:held"
_RUN_WAIT_S: float = 30.0
MARKDOWN: str = (
    "# Plan\n\nWhat to do this week, with <b>care</b>.\n\n## Steps\n\n"
    + "".join(f"- Step {number}: check the **numbers** of week {number}.\n" for number in range(1, 31))
    + "\n| Item | Amount |\n| --- | --- |\n| Rent | 1200 |\n| Food | 300 |\n\n```\nprint('done')\n```\n"
)
PLAIN: str = "".join(f"Line {number} of the log: all quiet.\n" for number in range(1, 61))
SHORT: str = "A short note."
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)

# Enough replies for every run the tests start: the one at work, then one Start per shape.
_REPLIES: int = 1 + 2 * len(_SHOTS)


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


class HeldModel:
    """The scripted model under its provider's name, whose replies wait until the test lets them go, so that a run
    works while its page is read."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script
        self.release: asyncio.Event = asyncio.Event()

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        await self.release.wait()
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return (await self._script.model_info("held"),)

    async def aclose(self) -> None:
        await self._script.aclose()


@asynccontextmanager
async def _served(home: Path, model: HeldModel) -> AsyncIterator[tuple[RunManager, str]]:
    """A daemon on the Dot home ``home`` whose main model is ``model``: its run manager and the address it is at."""
    (home / "config.toml").write_text(f'[browser]\nheadless = true\n\n[models]\nmain = "{HELD}"\n', encoding="utf-8")
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


async def _paste(box: Locator, text: str) -> bool:
    """Paste ``text`` into ``box`` as the clipboard does; whether the page took the paste, keeping it out of the box."""
    return await box.evaluate(
        "(box, text) => { const data = new DataTransfer(); data.setData('text/plain', text);"
        " return !box.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: "
        "true })); }",
        text,
    )


async def _one_size(
    browser: Browser, address: str, working: RunRecord, size: tuple[int, Literal["light", "dark"]], shots: Path
) -> None:
    width, scheme = size
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    await page.goto(f"{address}/#/runs")
    box: Locator = page.get_by_role("textbox", name="Goal")
    chips: Locator = page.locator(".composer .attach-chip")

    assert not await _paste(box, SHORT)
    assert await _paste(box, MARKDOWN)
    assert await _paste(box, PLAIN)
    await expect(chips).to_have_count(2)
    markdown: Locator = chips.filter(has_text=re.compile(r"^pasted-text-\d{6}\.md"))
    plain: Locator = chips.filter(has_text=re.compile(r"^pasted-text-\d{6}\.txt"))
    await expect(markdown).to_have_class(re.compile(r"\bready\b"))
    await expect(plain).to_have_class(re.compile(r"\bready\b"))
    assert await box.input_value() == ""
    await page.screenshot(path=str(shots / f"chips-{width}-{scheme}.png"))

    # The plain one goes back into the box as text.
    await plain.get_by_role("button", name=re.compile(r"in the message as text$")).click()
    await expect(chips).to_have_count(1)
    assert await box.input_value() == PLAIN

    # The Markdown one opens in a dialog, formatted, its HTML never drawn, and as it is written a click away.
    opener: Locator = markdown.locator("button.attach-open")
    await opener.click()
    dialog: Locator = page.locator("dialog.file-dialog")
    view: Locator = dialog.locator(".file-view")
    await expect(view).to_have_attribute("data-kind", "text")
    formatted: Locator = view.locator(".file-markdown")
    await expect(formatted.get_by_role("heading", name="Plan", exact=True)).to_be_visible()
    await expect(formatted.locator("li")).to_have_count(30)
    await expect(formatted.locator("strong").first).to_have_text("numbers")
    await expect(formatted.locator("td")).to_have_text(["Rent", "1200", "Food", "300"])
    await expect(formatted.locator("p").first).to_have_text("What to do this week, with <b>care</b>.")
    await expect(view.locator("b")).to_have_count(0)
    shown: dict[str, float] | None = await dialog.bounding_box()
    assert shown is not None and shown["x"] >= 0 and shown["x"] + shown["width"] <= width, shown
    await page.screenshot(path=str(shots / f"formatted-{width}-{scheme}.png"))
    await view.locator(".file-body").evaluate("(body) => body.scrollTo(0, body.scrollHeight)")
    await page.screenshot(path=str(shots / f"formatted-end-{width}-{scheme}.png"))
    await view.get_by_role("button", name="Source").click()
    await expect(view.locator("pre.file-text")).to_have_text(MARKDOWN)
    await expect(view.locator(".file-markdown")).to_have_count(0)
    await page.keyboard.press("Escape")
    await expect(dialog).to_have_count(0)
    await expect(opener).to_be_focused()

    # Files alone are a message too (item 445): the send starts the run with no word typed, and what the run reads is
    # the line the file is worth. The box goes back to empty first, the pasted text having been tried in it.
    await box.fill("")
    await expect(chips).to_have_count(1)
    await page.get_by_role("button", name="Start").click()
    await expect(page).to_have_url(re.compile(r"#/runs/run_"))
    await expect(page.locator(".bubble.user")).to_contain_text("See the attached file.")

    # On a run at work an empty box makes the send its Stop; a long paste leaves the box empty and the send a send.
    await page.goto(f"{address}/#/runs/{working.id}")
    dock: Locator = page.locator(".dock .composer")
    send: Locator = dock.locator(".btn.send")
    await expect(send).to_have_attribute("aria-label", "Stop")
    assert await _paste(dock.get_by_role("textbox"), PLAIN)
    await expect(dock.locator(".attach-chip")).to_have_class(re.compile(r"\bready\b"))
    await expect(send).not_to_have_attribute("aria-label", "Stop")
    await page.screenshot(path=str(shots / f"working-{width}-{scheme}.png"))
    # Sent with no word typed, the pasted text goes: the dock clears and its file with it (item 445).
    await send.click()
    await expect(dock.locator(".attach-chip")).to_have_count(0)
    await page.close()


async def test_a_long_paste_goes_as_a_file_that_opens_formatted(own_home: Path, tmp_path: Path) -> None:
    finish: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
    )
    model: HeldModel = HeldModel(ScriptedProvider(_PROVIDER, [finish] * _REPLIES))
    async with _served(own_home, model) as (manager, address), async_playwright() as playwright:
        working: RunRecord = await manager.start(
            "Work while the page is read", "task", HELD, None, "test", None, None, "standard", None, "ask", False, ()
        )
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for size in _SHOTS:
                await _one_size(browser, address, working, size, tmp_path)
        finally:
            await browser.close()
            model.release.set()
            async with asyncio.timeout(_RUN_WAIT_S):
                await manager.wait(working.id)


_PASTE_PICTURE: str = """async (box) => {
  const canvas = document.createElement("canvas");
  canvas.width = 8;
  canvas.height = 8;
  const blob = await new Promise((resolved) => canvas.toBlob(resolved, "image/png"));
  const data = new DataTransfer();
  data.items.add(new File([blob], "dot.png", { type: "image/png" }));
  return !box.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
}"""


@pytest.mark.browser
async def test_a_pasted_picture_goes_with_no_word_typed(own_home: Path) -> None:
    """Item 445, the owner's word of 2026-10-20: a picture pasted into the box goes as the message, with no word typed —
    on a phone and on a wide window — and the run reads the line the file is worth."""
    finish: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
    )
    model: HeldModel = HeldModel(ScriptedProvider(_PROVIDER, [finish] * _REPLIES))
    async with _served(own_home, model) as (_manager, address), async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for width in (1280, 390):
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 900}, color_scheme="dark", reduced_motion="reduce"
                )
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/runs")
                box: Locator = page.get_by_role("textbox", name="Goal")
                assert await box.evaluate(_PASTE_PICTURE)
                chips: Locator = page.locator(".composer .attach-chip")
                await expect(chips).to_have_count(1)
                await expect(chips.first).to_have_attribute("title", "dot.png")
                # The picture is uploaded before the message may go: the chip says so while it is on its way.
                await expect(chips.first).not_to_have_class(re.compile(r"\buploading\b"))
                await page.get_by_role("button", name="Start").click()
                await expect(page).to_have_url(re.compile(r"#/runs/run_"))
                await expect(page.locator(".bubble.user")).to_contain_text("See the attached file.")
                await page.close()
        finally:
            await browser.close()
            model.release.set()
