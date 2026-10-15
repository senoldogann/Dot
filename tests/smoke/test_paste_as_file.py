"""A long text pasted into a message box goes as a file (item 269), on the pages of a served daemon in Chrome: a long
Markdown text becomes `pasted-text-<time>.md` and a long plain one `pasted-text-<time>.txt`, each uploaded at once and
nothing typed into the box, while a short paste is the box's own; a pasted file goes back into the box as text from its
chip. The chip of the new chat's box opens the file in a dialog, the page having no panel beside a conversation: a
Markdown file formatted, its HTML never drawn, and as it is written a click away. Wide and on a phone, light and dark.
Before, a paste of any length went into the box as text.

No network and no model service is used: no message is sent.
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
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "paste-as-file-test-token"
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


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[str]:
    """A daemon on the Dot home ``home``, at the address it yields."""
    (home / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
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
        await close_runtime(runtime)


async def _paste(box: Locator, text: str) -> bool:
    """Paste ``text`` into ``box`` as the clipboard does; whether the page took the paste, keeping it out of the box."""
    return await box.evaluate(
        "(box, text) => { const data = new DataTransfer(); data.setData('text/plain', text);"
        " return !box.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true })); }",
        text,
    )


async def _one_size(browser: Browser, address: str, size: tuple[int, Literal["light", "dark"]], shots: Path) -> None:
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
    await page.close()


async def test_a_long_paste_goes_as_a_file_that_opens_formatted(own_home: Path, tmp_path: Path) -> None:
    async with _served(own_home) as address, async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for size in _SHOTS:
                await _one_size(browser, address, size, tmp_path)
        finally:
            await browser.close()
