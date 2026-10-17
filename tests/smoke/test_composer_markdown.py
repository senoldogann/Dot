"""The composer shows the markdown it holds (item 207). A copy of the box's text lies under the box with its marks
quiet, bold words and headings heavier, code tinted and links in the colour of links, while the box keeps its own
letters, clear, over it: the copy holds the box's text exactly, after typing, a pick of the slash menu, a draft
brought back and a paste, and it lies letter for letter under the box's letters, in the new chat's box and in a
chat's dock scrolled half way and to its end, which the pixels of both show; the composer is no higher for it. A real
server, wide and on a phone, light and dark."""

import asyncio
import io
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from PIL import Image, ImageChops
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "composer-markdown-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# Every kind of line and mark the composer shows, with lines long enough to wrap in a box on a phone.
_SAMPLE: str = "\n".join(
    [
        "# Plan for the garden",
        "",
        "The beds **face south**, with *paths* between them and room for a barrow to pass on either side; see "
        "[the guide](https://example.com/guide) or https://example.com/raw.",
        "- Beans by the fence",
        "1. Dig",
        "> Water in the morning, before the sun is high.",
        "",
        "| Bed | Crop |",
        "| --- | --- |",
        "| 1 | Beans \\| peas |",
        "",
        "```",
        "ls ~/garden",
        "```",
        "---",
        "Keep `bed 2` free.",
    ]
)
_TABLE: str = "| Bed | Sun |\n| --- | --- |\n| 1 | full |"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (1280, "dark"), (390, "light"))
# Of the box's pixels, how many may differ between the copy and the box's own letters. A copy one pixel off differs in
# a few percent of them (every letter's edge); one in its place differs in none.
_MOST_APART: float = 0.001
_GREY_STEP: int = 48
# Of the box's pixels, how many at least its marks and strokes change (the marks are small, the tints light).
_LEAST_STYLED: float = 0.001

# The copy in one colour, with no tints and no stroke, and no caret in the box: what differs then is where the letters
# stand. Then the box's own letters, in that colour, with the copy hidden. CSSOM rules, which the page's content
# security policy lets a script add; each call returns the rules' places, for `_DROP`.
_PLAIN: str = """() => {
  const sheet = document.styleSheets[0];
  return [
    ".composer-mirror, .composer-mirror * { color: var(--text) !important; -webkit-text-stroke-width: 0 !important; "
      + "background: none !important; }",
    "::highlight(md-mark), ::highlight(md-address), ::highlight(md-link), ::highlight(md-strong), ::highlight(md-code) "
      + "{ color: var(--text) !important; background-color: transparent !important; text-shadow: none !important; }",
    ".composer-field.mirrored .textarea { caret-color: transparent !important; }",
  ].map((rule) => sheet.insertRule(rule, sheet.cssRules.length));
}"""
_OWN_LETTERS: str = """() => {
  const sheet = document.styleSheets[0];
  return [
    ".composer-mirror { visibility: hidden !important; }",
    ".composer-field.mirrored .textarea { color: var(--text) !important; }",
  ].map((rule) => sheet.insertRule(rule, sheet.cssRules.length));
}"""
_DROP: str = """(places) => {
  const sheet = document.styleSheets[0];
  for (const place of [...places].sort((a, b) => b - a)) sheet.deleteRule(place);
}"""
# Whether the copy under the dock's box is scrolled as far as the box.
_SCROLLED_ALIKE: str = """() => {
  const box = document.querySelector('.dock .composer textarea');
  return Math.abs(box.parentElement.querySelector('.composer-mirror').scrollTop - box.scrollTop) < 1;
}"""
# The texts of the ranges of one kind of mark that the copies of the page paint.
_HIGHLIGHTED: str = "(kind) => [...(CSS.highlights.get(`md-${kind}`) ?? [])].map((range) => range.toString())"
# The copy's text, a line for each of its lines, as the box holds it.
_COPIED: str = """(box) => [...box.parentElement.querySelector('.composer-mirror').children]
  .map((line) => line.textContent).join('\\n')"""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the scripted model, which no run here calls."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, []))
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


async def _chat(runtime: Runtime) -> RunRecord:
    """A chat whose one run is over, for its dock."""
    thread: ThreadRecord = await runtime.store.create_thread("Garden", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        "Plan the beds.",
        "task",
        SCRIPTED_MODEL,
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
    await runtime.store.end_run(run.id, "done", "Done.", {"status": "done", "result": "Done.", "needs": ""})
    return run


async def _page(
    browser: Browser, address: str, hash_: str, width: int, scheme: Literal["light", "dark"]
) -> tuple[Page, list[str]]:
    """A page at ``hash_``, signed in, in a browser of its own (one that lets a test use the clipboard where the browser
    allows it), with the errors it throws."""
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    readable: bool = browser.browser_type.name == "chromium"
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 800},
        color_scheme=scheme,
        reduced_motion="reduce",
        permissions=["clipboard-read", "clipboard-write"] if readable else [],
    )
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#{hash_}")
    return page, errors


async def _copy_holds_the_text(box: Locator) -> None:
    """The copy under ``box`` holds the box's text, line for line."""
    value: str = await box.input_value()
    assert await box.evaluate(_COPIED) == value


async def _highlighted(page: Page, kind: str) -> list[str]:
    return [str(text) for text in await page.evaluate(_HIGHLIGHTED, kind)]


def _differing(first: bytes, second: bytes) -> float:
    """The share of the pixels that differ between two pictures of one size."""
    one: Image.Image = Image.open(io.BytesIO(first)).convert("RGB")
    two: Image.Image = Image.open(io.BytesIO(second)).convert("RGB")
    assert one.size == two.size
    grey: Image.Image = ImageChops.difference(one, two).convert("L")
    differing: int = grey.point(lambda level: 255 if level > _GREY_STEP else 0).histogram()[255]
    return differing / (one.width * one.height)


async def _apart(page: Page, box: Locator, tmp_path: Path, name: str) -> tuple[float, float]:
    """The shares of the pixels of ``box`` that differ between the copy under it as it shows and the copy in one colour
    (its marks, tints and strokes), and between the copy in one colour and the box's own letters in that colour (where
    the letters stand); the pictures are kept under ``name``."""
    await box.evaluate("node => node.blur()")
    styled: bytes = await box.screenshot()
    (tmp_path / f"{name}-styled.png").write_bytes(styled)
    plain: list[int] = await page.evaluate(_PLAIN)
    copied: bytes = await box.screenshot()
    own: list[int] = await page.evaluate(_OWN_LETTERS)
    typed: bytes = await box.screenshot()
    await page.evaluate(_DROP, own + plain)
    (tmp_path / f"{name}-copy.png").write_bytes(copied)
    (tmp_path / f"{name}-own.png").write_bytes(typed)
    return _differing(styled, copied), _differing(copied, typed)


async def test_the_composer_shows_its_markdown_letter_for_letter_under_its_own_letters(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    address: str = served
    chat: RunRecord = await _chat(runtime)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page, errors = await _page(browser, address, "/", width, scheme)
                box: Locator = page.locator(".composer textarea")
                mirror: Locator = page.locator(".composer .composer-mirror")
                await expect(page.locator(".composer .composer-field.mirrored")).to_have_count(1)
                assert await mirror.get_attribute("aria-hidden") == "true"
                await box.fill(_SAMPLE)
                await _copy_holds_the_text(box)
                # The field is as high as the box in it: the composer is as high as it was without the copy.
                heights: list[float] = await box.evaluate(
                    "node => [node.parentElement.getBoundingClientRect().height, node.getBoundingClientRect().height]"
                )
                assert heights[0] == heights[1], heights
                # Each line as it reads: its kind, and the marks within it as highlights of its text.
                assert await _highlighted(page, "mark") == [
                    "#",
                    "**",
                    "**",
                    "*",
                    "*",
                    "[",
                    "](",
                    ")",
                    "-",
                    "1.",
                    ">",
                    "|",
                    "|",
                    "|",
                    "| --- | --- |",
                    "|",
                    "|",
                    "|",
                    "```",
                    "```",
                    "---",
                    "`",
                    "`",
                ]
                assert await _highlighted(page, "strong") == ["face south"]
                assert await _highlighted(page, "link") == ["the guide", "https://example.com/raw"]
                assert await _highlighted(page, "address") == ["https://example.com/guide"]
                assert await _highlighted(page, "code") == ["bed 2"]
                await expect(mirror.locator(".md-heading")).to_have_text("# Plan for the garden")
                await expect(mirror.locator(".md-list")).to_have_count(2)
                await expect(mirror.locator(".md-quote")).to_have_count(1)
                await expect(mirror.locator(".md-table-head")).to_have_text("| Bed | Crop |")
                await expect(mirror.locator(".md-table-rule")).to_have_text("| --- | --- |")
                await expect(mirror.locator(".md-table")).to_have_count(1)
                await expect(mirror.locator(".md-block")).to_have_text("ls ~/garden")
                await expect(mirror.locator(".md-fence")).to_have_count(2)
                await expect(mirror.locator(".md-rule")).to_have_text("---")
                assert await box.evaluate("node => getComputedStyle(node).color") == "rgba(0, 0, 0, 0)"
                await page.screenshot(path=str(tmp_path / f"composer-{width}-{scheme}.png"))
                # Letter for letter: the copy lies under the box's own letters, and its marks are painted.
                styled, apart = await _apart(page, box, tmp_path, f"home-{width}-{scheme}")
                assert styled > _LEAST_STYLED, styled
                assert apart <= _MOST_APART, apart

                if index == 0:
                    # Typed at the end, a pick of the slash menu, a draft brought back and a paste: the copy follows.
                    await box.press("ControlOrMeta+End")
                    await page.keyboard.type(" And **more**")
                    assert await _highlighted(page, "strong") == ["face south", "more"]
                    await _copy_holds_the_text(box)
                    await box.fill("")
                    await box.press_sequentially("/res")
                    await box.press("Tab")
                    await expect(box).to_have_value("")
                    await _copy_holds_the_text(box)
                    assert await _highlighted(page, "strong") == []
                    await box.fill(_SAMPLE)
                    await page.reload()
                    await expect(box).to_have_value(_SAMPLE)
                    await expect(mirror.locator(".md-heading")).to_have_count(1)
                    await _copy_holds_the_text(box)
                    if browser.browser_type.name == "chromium":
                        await page.evaluate("text => navigator.clipboard.writeText(text)", _TABLE)
                        await box.fill("")
                        await box.focus()
                        await page.keyboard.press("ControlOrMeta+v")
                        await expect(box).to_have_value(_TABLE)
                        await expect(mirror.locator(".md-table-head")).to_have_count(1)
                        await _copy_holds_the_text(box)
                    await box.fill("")

                # A chat's dock, its text longer than the box, scrolled half way.
                await page.goto(f"{address}/#/runs/{chat.id}")
                dock: Locator = page.locator(".dock .composer textarea")
                await dock.fill(f"{_SAMPLE}\n\n{_SAMPLE}")
                scrolled: float = await dock.evaluate(
                    "node => { node.scrollTop = (node.scrollHeight - node.clientHeight) / 2; return node.scrollTop; }"
                )
                assert scrolled > 40
                # The copy scrolls with the box.
                await page.wait_for_function(_SCROLLED_ALIKE)
                styled, apart = await _apart(page, dock, tmp_path, f"dock-{width}-{scheme}")
                assert styled > _LEAST_STYLED, styled
                assert apart <= _MOST_APART, apart
                # And at its end, as far as the box scrolls (where few marks are left in sight).
                await dock.evaluate("node => { node.scrollTop = node.scrollHeight; }")
                await page.wait_for_function(_SCROLLED_ALIKE)
                _, apart = await _apart(page, dock, tmp_path, f"dock-end-{width}-{scheme}")
                assert apart <= _MOST_APART, apart
                await dock.fill("")
                assert errors == []
                await page.context.close()
        finally:
            await browser.close()
