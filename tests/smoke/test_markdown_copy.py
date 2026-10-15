"""Markdown in the conversation (item 206). What the owner wrote reads as markdown, as an answer does, and a copy out of
the conversation goes on the clipboard as the markdown it was written in, with a clean copy in HTML: a whole answer
gives back its text, a selection across the items of a list or the rows of a table keeps its list or table, the feet
and the faces stay out, and a copy inside a block of code or out of the composer is the browser's own. Two turns of a
chat in a home of their own, served by a real server, wide and on a phone, light and dark."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
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

TOKEN: str = "markdown-copy-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# What the owner wrote: a table and a list, as a paste brings them.
_GOAL: str = "\n".join(
    [
        "Which crop for each bed?",
        "",
        "| Bed | Sun |",
        "| --- | --- |",
        "| 1 | full |",
        "| 2 | half |",
        "",
        "- keep **bed 3** free",
        "- water at `06:00`",
    ]
)
# Every kind of block and mark Dot draws, written as a copy gives them back.
_ANSWER: str = "\n".join(
    [
        "# Plan",
        "",
        "The beds **face south**, with *paths* between them; see [the guide](https://example.com/guide) or "
        "https://example.com/raw.",
        "",
        "- Beans by the fence",
        "- Tomatoes in `bed 2`",
        "",
        "1. Dig",
        "2. Sow",
        "",
        "> Water in the morning.",
        "",
        "| Bed | Crop |",
        "| --- | --- |",
        "| 1 | Beans \\| peas |",
        "| 2 | Tomatoes |",
        "",
        "```",
        "ls ~/garden",
        "```",
        "",
        "---",
        "",
        "The list lives in `~/garden/plan.md`.",
    ]
)
_FOLLOW_UP: str = "And the **beans**?"
_FOLLOW_UP_ANSWER: str = "By the fence."
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (1280, "dark"), (390, "light"))

# A copy of the selection the page holds, as the browser fires it at the selection's start: whether the page took it
# over, and what it put on the clipboard.
_COPY: str = """() => {
  const range = getSelection().getRangeAt(0);
  const start = range.startContainer.nodeType === Node.ELEMENT_NODE ? range.startContainer : range.startContainer.parentElement;
  const data = new DataTransfer();
  const event = new ClipboardEvent("copy", { clipboardData: data, bubbles: true, cancelable: true });
  start.dispatchEvent(event);
  return { taken: event.defaultPrevented, text: data.getData("text/plain"), html: data.getData("text/html") };
}"""


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


async def _turn(runtime: Runtime, thread: ThreadRecord, goal: str, answer: str) -> RunRecord:
    """A run of the chat ``thread`` that was asked ``goal`` and answered ``answer``."""
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        goal,
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
    await runtime.store.end_run(run.id, "done", answer, {"status": "done", "result": answer, "needs": ""})
    return run


async def _page(
    browser: Browser, address: str, run_id: str, width: int, scheme: Literal["light", "dark"]
) -> tuple[Page, list[str]]:
    """The page of the run ``run_id``, signed in, in a browser of its own (one that lets a test read the clipboard where
    the browser allows it), with the errors it throws."""
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
    await page.goto(f"{address}/#/runs/{run_id}")
    return page, errors


async def _copy(page: Page, select: str) -> tuple[bool, str, str]:
    """Select with ``select`` (a function of the page that sets the selection) and copy: whether the page took the copy
    over, and the text and HTML it put on the clipboard."""
    await page.evaluate(select)
    copied: dict[str, object] = await page.evaluate(_COPY)
    return bool(copied["taken"]), str(copied["text"]), str(copied["html"])


def _select(start: str, end: str) -> str:
    """A function of the page that selects from ``start`` to ``end``, each an expression for a node and an offset."""
    return (
        "() => { const range = document.createRange();"
        f" range.setStart({start}); range.setEnd({end});"
        " getSelection().removeAllRanges(); getSelection().addRange(range); }"
    )


async def test_the_owners_messages_read_as_markdown_and_a_copy_of_the_conversation_keeps_its_markdown(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    address: str = served
    thread: ThreadRecord = await runtime.store.create_thread("Garden", "test")
    await _turn(runtime, thread, _GOAL, _ANSWER)
    latest: RunRecord = await _turn(runtime, thread, _FOLLOW_UP, _FOLLOW_UP_ANSWER)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SHOTS:
                page, errors = await _page(browser, address, latest.id, width, scheme)
                answers: Locator = page.locator(".answer .prose")
                bubbles: Locator = page.locator(".bubble.user")
                await expect(answers).to_have_count(2)
                await expect(bubbles).to_have_count(2)
                # What the owner wrote reads as markdown: its table, its list and their marks.
                first: Locator = bubbles.nth(0)
                await expect(first.locator("table tr")).to_have_count(3)
                await expect(first.locator("th")).to_have_text(["Bed", "Sun"])
                await expect(first.locator("li")).to_have_count(2)
                await expect(first.locator("strong")).to_have_text("bed 3")
                await expect(first.locator("code")).to_have_text("06:00")
                await expect(bubbles.nth(1).locator("strong")).to_have_text("beans")
                await expect(page.locator(".answer h2[data-level='1']")).to_have_text("Plan")

                # A whole answer gives back the markdown it was written in; its HTML is clean.
                taken, text, html = await _copy(
                    page, "() => getSelection().selectAllChildren(document.querySelector('.answer .prose'))"
                )
                assert taken
                assert text == _ANSWER
                assert "<table" in html and "<code>~/garden/plan.md</code>" in html
                assert 'href="https://example.com/guide"' in html
                for unwanted in ("<button", "class=", "data-", "title="):
                    assert unwanted not in html, unwanted

                # From the owner's message to the end of its answer: both, without their feet and the face.
                taken, text, _ = await _copy(
                    page,
                    _select(
                        "document.querySelector('.bubble.user .prose'), 0",
                        "document.querySelector('.answer .prose'), "
                        "document.querySelector('.answer .prose').childNodes.length",
                    ),
                )
                assert taken
                assert text == f"{_GOAL}\n\n{_ANSWER}"

                # Across two items of a list, or two rows of a table: the list, the table.
                list_items: str = "document.querySelectorAll('.answer .prose ul li')"
                taken, text, _ = await _copy(
                    page, _select(f"{list_items}[0].firstChild, 6", f"{list_items}[1].firstChild, 8")
                )
                assert taken
                assert text == "- by the fence\n- Tomatoes"
                rows: str = "document.querySelectorAll('.answer .prose tbody tr')"
                taken, text, _ = await _copy(
                    page,
                    _select(
                        f"{rows}[0].cells[0].firstChild, 0",
                        f"{rows}[1].cells[1].firstChild, {len('Tomatoes')}",
                    ),
                )
                assert taken
                assert text == "| 1 | Beans \\| peas |\n| --- | --- |\n| 2 | Tomatoes |"

                # Within a line, its marks; within a block of code or the composer, the browser's own copy.
                paragraph: str = "document.querySelector('.answer .prose p')"
                taken, text, _ = await _copy(
                    page, _select(f"{paragraph}.firstChild, 4", f"{paragraph}.querySelector('strong').firstChild, 10")
                )
                assert taken
                assert text == "beds **face south**"
                code: str = "document.querySelector('.answer .prose pre code').firstChild"
                taken, _, _ = await _copy(page, _select(f"{code}, 0", f"{code}, 2"))
                assert not taken
                composer: Locator = page.locator(".composer textarea")
                await page.evaluate("() => getSelection().removeAllRanges()")
                await composer.fill("A draft with **marks**")
                await composer.focus()
                await composer.evaluate("node => node.select()")
                copied: dict[str, object] = await composer.evaluate(
                    "node => { const event = new ClipboardEvent('copy', { clipboardData: new DataTransfer(),"
                    " bubbles: true, cancelable: true }); node.dispatchEvent(event); return { taken: event.defaultPrevented }; }"
                )
                assert copied["taken"] is False

                # The keys of a real copy, where the browser lets a test read the clipboard.
                if browser.browser_type.name == "chromium":
                    await page.evaluate(
                        "() => getSelection().selectAllChildren(document.querySelector('.answer .prose'))"
                    )
                    await page.keyboard.press("ControlOrMeta+c")
                    assert await page.evaluate("() => navigator.clipboard.readText()") == _ANSWER

                await page.evaluate("() => getSelection().removeAllRanges()")
                await first.scroll_into_view_if_needed()
                await page.screenshot(path=str(tmp_path / f"markdown-{width}-{scheme}.png"))
                assert errors == []
                await page.context.close()
        finally:
            await browser.close()
